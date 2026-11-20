"""非线性探索画布：节点 / 连线 CRUD + 流式 AI 回答（SSE）。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine, get_session
from ..models import CanvasEdge, CanvasNode, Course
from ..services import canvas as canvas_svc
from ..services import gamification, llm

router = APIRouter(prefix="/api", tags=["canvas"])

KIND_EVENT = {"highlight": "HIGHLIGHT_ASKED", "followup": "NODE_ASKED", "term": "TERM_EXPLORED", "merge": "MERGE_ASKED"}


def node_out(n: CanvasNode) -> dict:
    d = n.model_dump(exclude={"anchor_json", "sources_json"})
    d["anchor"] = json.loads(n.anchor_json) if n.anchor_json else None
    d["sources"] = json.loads(n.sources_json) if n.sources_json else []
    d["created_at"] = n.created_at.isoformat()
    return d


class NodeIn(BaseModel):
    type: str
    kind: str = ""
    title: str = ""
    question: str = ""
    content: str = ""
    anchor: dict[str, Any] | None = None
    x: float = 0
    y: float = 0
    parent_ids: list[int] = []


class NodePatch(BaseModel):
    title: str | None = None
    question: str | None = None
    content: str | None = None
    x: float | None = None
    y: float | None = None


class Pos(BaseModel):
    id: int
    x: float
    y: float


class EdgeIn(BaseModel):
    source: int
    target: int


@router.get("/courses/{cid}/canvas")
def get_canvas(cid: int, s: Session = Depends(get_session)):
    nodes = s.exec(select(CanvasNode).where(CanvasNode.course_id == cid)).all()
    edges = s.exec(select(CanvasEdge).where(CanvasEdge.course_id == cid)).all()
    return {"nodes": [node_out(n) for n in nodes], "edges": edges}


@router.post("/courses/{cid}/nodes")
def create_node(cid: int, body: NodeIn, s: Session = Depends(get_session)):
    if not s.get(Course, cid):
        raise HTTPException(404, "课程不存在")
    if body.type not in ("excerpt", "qa", "note", "concept"):
        raise HTTPException(400, "未知节点类型")
    parents = [s.get(CanvasNode, p) for p in body.parent_ids]
    parents = [p for p in parents if p and p.course_id == cid]
    # 追问层级：问答节点 = 父问答节点最大层级 + 1（从原文直接拉出的第一问为 1 层）
    depth = 1 + max((p.depth for p in parents if p.type == "qa"), default=0) if body.type == "qa" else 0
    n = CanvasNode(course_id=cid, type=body.type, kind=body.kind, title=body.title, question=body.question,
                   content=body.content, anchor_json=json.dumps(body.anchor, ensure_ascii=False) if body.anchor else "",
                   x=body.x, y=body.y, depth=depth)
    s.add(n)
    s.flush()
    for p in parents:
        s.add(CanvasEdge(course_id=cid, source=p.id, target=n.id))
    s.commit()
    s.refresh(n)
    out = node_out(n)
    edges = [e.model_dump() for e in s.exec(select(CanvasEdge).where(CanvasEdge.target == n.id)).all()]
    reward = None
    if body.type == "note":
        reward = gamification.emit(s, "NOTE_CREATED", cid, {"node_id": out["id"]})
    return {"node": out, "edges": edges, "reward": reward}


@router.patch("/nodes/{nid}")
def patch_node(nid: int, body: NodePatch, s: Session = Depends(get_session)):
    n = s.get(CanvasNode, nid)
    if not n:
        raise HTTPException(404, "节点不存在")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(n, k, v)
    s.add(n)
    s.commit()
    s.refresh(n)
    return node_out(n)


@router.post("/nodes/positions")
def save_positions(body: list[Pos], s: Session = Depends(get_session)):
    for p in body:
        n = s.get(CanvasNode, p.id)
        if n:
            n.x, n.y = p.x, p.y
            s.add(n)
    s.commit()
    return {"ok": True}


@router.delete("/nodes/{nid}")
def delete_node(nid: int, s: Session = Depends(get_session)):
    n = s.get(CanvasNode, nid)
    if not n:
        raise HTTPException(404, "节点不存在")
    s.delete(n)
    s.commit()
    return {"ok": True}


def _creates_cycle(s: Session, cid: int, source: int, target: int) -> bool:
    if source == target:
        return True
    edges = s.exec(select(CanvasEdge).where(CanvasEdge.course_id == cid)).all()
    children: dict[int, list[int]] = {}
    for e in edges:
        children.setdefault(e.source, []).append(e.target)
    stack, seen = [target], set()
    while stack:
        cur = stack.pop()
        if cur == source:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(children.get(cur, []))
    return False


@router.post("/courses/{cid}/edges")
def create_edge(cid: int, body: EdgeIn, s: Session = Depends(get_session)):
    a, b = s.get(CanvasNode, body.source), s.get(CanvasNode, body.target)
    if not a or not b or a.course_id != cid or b.course_id != cid:
        raise HTTPException(404, "节点不存在")
    exists = s.exec(select(CanvasEdge).where(CanvasEdge.source == body.source, CanvasEdge.target == body.target)).first()
    if exists:
        return exists
    if _creates_cycle(s, cid, body.source, body.target):
        raise HTTPException(400, "这条连线会形成环，画布需保持有向无环")
    e = CanvasEdge(course_id=cid, source=body.source, target=body.target)
    s.add(e)
    s.commit()
    s.refresh(e)
    return e


@router.delete("/edges/{eid}")
def delete_edge(eid: int, s: Session = Depends(get_session)):
    e = s.get(CanvasEdge, eid)
    if e:
        s.delete(e)
        s.commit()
    return {"ok": True}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/nodes/{nid}/answer")
async def answer_node(nid: int, use_rag: bool = True, tier: str = "strong"):
    if tier not in ("fast", "strong"):
        raise HTTPException(400, "tier 只能是 fast 或 strong")
    with Session(engine) as s:
        n = s.get(CanvasNode, nid)
        if not n or n.type not in ("qa", "concept"):
            raise HTTPException(404, "问答节点不存在")
        if n.type == "concept":
            tier = "fast"  # 概念速查走轻量档，省钱提速
        messages, sources = canvas_svc.build_messages(s, n, use_rag=use_rag)
        parents = len(canvas_svc.parent_ids(s, nid))

    async def gen():
        yield _sse({"type": "sources", "sources": sources})
        parts: list[str] = []
        ok = False
        try:
            async for piece in llm.stream_chat(tier, messages):
                parts.append(piece)
                yield _sse({"type": "delta", "text": piece})
            ok = True
        except llm.LLMNotConfigured as e:
            yield _sse({"type": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"模型调用失败：{str(e)[:300]}"})
        finally:
            # 即使中途断开也保存已生成的部分
            with Session(engine) as s:
                node = s.get(CanvasNode, nid)
                if node and parts:
                    node.content = "".join(parts)
                    node.sources_json = json.dumps(sources, ensure_ascii=False)
                    s.add(node)
                    s.commit()
                reward = None
                if ok and node and not node.rewarded:
                    node.rewarded = True
                    s.add(node)
                    s.commit()
                    event = KIND_EVENT.get(node.kind, "NODE_ASKED")
                    reward = gamification.emit(s, event, node.course_id, {"node_id": nid, "parents": parents})
        if ok:
            yield _sse({"type": "done", "reward": reward})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
