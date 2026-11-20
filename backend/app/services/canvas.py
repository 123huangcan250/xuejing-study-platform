"""画布上下文：只拼接与目标节点有连线关系的祖先节点 + 相关原文，实现 DAG 显式上下文剪枝。"""
from __future__ import annotations

import json
from collections import deque

from sqlmodel import Session, select

from ..models import CanvasEdge, CanvasNode, Course, Document
from . import rag

MAX_ANCESTORS = 12
MAX_NODE_CHARS = 1800

SYSTEM_PROMPT = """你是「{course}」这门课的私人导师，正在一张非线性思维画布上和学生一起探索。
回答要求：
1. 先给直觉，再给严谨解释；必要时用分点、表格、公式（LaTeX，用 $...$ 或 $$...$$）或代码块。
2. 把回答中值得继续深挖的 3~6 个关键术语用双中括号标出，如 [[页表]]、[[梯度下降]]，学生点击后会生成新分支。
3. 如果引用了下方「课程资料检索结果」，在句末用 [1]、[2] 这样的编号标注来源；资料里没有的内容可以用你自己的知识回答，但不要编造引用。
4. 回答聚焦当前问题，不要复述上下文。默认使用中文。"""


def ancestors(s: Session, node_id: int, course_id: int) -> list[CanvasNode]:
    """沿连线向上 BFS 收集祖先，按创建顺序返回（近似拓扑序）。"""
    edges = s.exec(select(CanvasEdge).where(CanvasEdge.course_id == course_id)).all()
    parents: dict[int, list[int]] = {}
    for e in edges:
        parents.setdefault(e.target, []).append(e.source)
    seen: set[int] = set()
    q = deque(parents.get(node_id, []))
    order: list[int] = []
    while q and len(order) < MAX_ANCESTORS * 3:
        n = q.popleft()
        if n in seen or n == node_id:
            continue
        seen.add(n)
        order.append(n)
        q.extend(parents.get(n, []))
    if not order:
        return []
    nodes = s.exec(select(CanvasNode).where(CanvasNode.id.in_(order))).all()
    # BFS 顺序越靠前越近；保留最近的 MAX_ANCESTORS 个，再按时间排序
    rank = {nid: i for i, nid in enumerate(order)}
    nodes = sorted(nodes, key=lambda n: rank[n.id])[:MAX_ANCESTORS]
    return sorted(nodes, key=lambda n: n.created_at)


def parent_ids(s: Session, node_id: int) -> list[int]:
    return list(s.exec(select(CanvasEdge.source).where(CanvasEdge.target == node_id)).all())


def _clip(t: str, n: int = MAX_NODE_CHARS) -> str:
    return t if len(t) <= n else t[:n] + "…（已截断）"


def describe(s: Session, n: CanvasNode) -> str:
    if n.type == "excerpt":
        a = json.loads(n.anchor_json or "{}")
        doc = s.get(Document, a.get("document_id")) if a.get("document_id") else None
        where = f"{doc.filename if doc else '资料'} 第 {a.get('page', '?')} 页"
        return f"【原文摘录 · {where}】\n{_clip(n.content)}"
    if n.type == "qa":
        return f"【之前的问答】\n问：{n.question}\n答：{_clip(n.content)}"
    if n.type == "concept":
        return f"【概念卡 · {n.title}】\n{_clip(n.content)}"
    return f"【学生笔记{(' · ' + n.title) if n.title else ''}】\n{_clip(n.content)}"


def build_messages(s: Session, node: CanvasNode, use_rag: bool = True) -> tuple[list[dict], list[dict]]:
    course = s.get(Course, node.course_id)
    ctx_nodes = ancestors(s, node.id, node.course_id)
    ctx = "\n\n".join(describe(s, n) for n in ctx_nodes)

    sources: list[dict] = []
    if use_rag:
        seed = node.question + " " + " ".join(n.content[:200] for n in ctx_nodes if n.type == "excerpt")
        # 直接相连的原文已经在上下文里了，检索结果里去掉同页重复
        anchored = {(a.get("document_id"), a.get("page")) for a in
                    (json.loads(n.anchor_json or "{}") for n in ctx_nodes if n.type == "excerpt")}
        for r in rag.search(s, seed, node.course_id, k=6):
            if (r["document_id"], r["page"]) in anchored and len(sources) >= 2:
                continue
            sources.append(r)
            if len(sources) >= 4:
                break

    parts = []
    if ctx:
        parts.append(f"以下是画布上与本问题直接相连的上下文节点（按时间顺序）：\n\n{ctx}")
    if sources:
        lines = [f"[{i + 1}] 《{r['filename']}》第 {r['page']} 页：{_clip(r['text'], 700)}" for i, r in enumerate(sources)]
        parts.append("课程资料检索结果：\n" + "\n\n".join(lines))
    parts.append(f"学生的问题：{node.question}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(course=course.name if course else "本课程")},
        {"role": "user", "content": "\n\n---\n\n".join(parts)},
    ]
    public_sources = [{k: r[k] for k in ("chunk_id", "document_id", "filename", "page", "filetype")} | {"snippet": r["text"][:160]}
                      for r in sources]
    return messages, public_sources
