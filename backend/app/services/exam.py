"""试卷分析：拆成一道道题 → 识别考点 / 能力层级 / 难度 → 对应到课件的知识点（及页码）。

对应关系用知识梳理里的知识点编号表达（模型只能从给定的目录里选编号，不能自己编），
所以必须先对课件做知识梳理。课件阅读器据此在相关页提示「这里是考点」。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from sqlmodel import Session, select

from ..db import engine
from ..models import Document, ExamQuestion, KnowledgeNode
from .blockstore import ensure_blocks
from .knowledge import make_windows, norm_title, page_texts, parse_json, to_int
from .translate import TranslateError, call_llm

log = logging.getLogger("app.exam")

ABILITIES = ("记忆", "理解", "应用", "分析", "综合")
QTYPES = ("选择", "填空", "判断", "简答", "计算", "证明", "分析", "综合")
BATCH = 6

SPLIT_SYSTEM = """下面是一份考试试卷的文字（带「[第N页]」页码标记，可能有排版噪声，也可能是英文）。请把它拆成一道道题目。
输出 JSON：{"questions":[{"no":"题号，如「一、3」或「Q2」","type":"选择/填空/判断/简答/计算/证明/分析/综合 之一","text":"完整题干（含各小问、选项，保留公式）","points":分值数字或null,"answer":"试卷里附带的参考答案，没有就留空"}]}
规则：不要遗漏或合并题目；题干原样保留，不要翻译或改写；不属于题目的内容（试卷说明、页眉页脚、装订线）忽略。"""

ANALYZE_SYSTEM = """你是理工科课程的命题分析专家。下面给出：①课程的知识点目录（编号|课件 > 小节 > 知识点）；②几道考试题。请逐题分析。
输出 JSON：{"items":[{"i":题目序号,"topics":["考点短语，最多 3 个，简短"],"node_ids":[对应的知识点编号，最多 3 个],"ability":"记忆|理解|应用|分析|综合","difficulty":1到5}]}
规则：
1. node_ids 只能从目录里选，找不到对应的就给空数组，绝对不要编造编号；
2. ability（布鲁姆认知层级）：记忆=直接回忆定义/公式；理解=解释、判断、比较概念；应用=套用公式或方法求解；分析=拆解多步、推导、分析原因；综合=综合多个知识点设计、评价或证明；
3. difficulty：1 很简单 … 5 很难。"""


@dataclass
class ExamJob:
    document_id: int
    state: str = "running"
    stage: str = ""
    total: int = 0
    done: int = 0
    error: str = ""
    started: float = field(default_factory=time.time)
    task: asyncio.Task | None = None


JOBS: dict[int, ExamJob] = {}


def _dedupe(qs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for q in qs:
        key = norm_title(q["text"])[:50]
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out


def _clean_question(q: dict) -> dict | None:
    text = str(q.get("text", "")).strip()
    if len(text) < 6:
        return None
    pts = q.get("points")
    qt = str(q.get("type", "")).strip()
    return {"no": str(q.get("no", "")).strip(), "type": qt if qt in QTYPES else qt[:6], "text": text,
            "points": float(pts) if isinstance(pts, (int, float)) else None, "answer": str(q.get("answer", "") or "").strip()}


async def split_questions(title: str, pages: dict[int, str]) -> list[dict]:
    sem = asyncio.Semaphore(2)

    async def one(text: str):
        async with sem:
            raw, _ = await call_llm([{"role": "system", "content": SPLIT_SYSTEM}, {"role": "user", "content": f"试卷：《{title}》\n\n{text}"}],
                                    json_mode=True)
            data = parse_json(raw)
            if data is None:
                raise TranslateError("模型没有按要求返回 JSON")
            return [c for c in (_clean_question(q) for q in data.get("questions", []) if isinstance(q, dict)) if c]

    parts = await asyncio.gather(*(one(t) for _ps, t in make_windows(pages, 7000)))
    return _dedupe([q for part in parts for q in part])


def catalog(s: Session, course_id: int) -> tuple[str, set[int]]:
    nodes = {n.id: n for n in s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == course_id)).all()}
    lines, ids = [], set()
    for n in nodes.values():
        if n.level != 3:
            continue
        sec = nodes.get(n.parent_id)
        ch = nodes.get(sec.parent_id) if sec else None
        lines.append(f"{n.id}|{ch.title if ch else ''} > {sec.title if sec else ''} > {n.title}")
        ids.add(n.id)
    return "\n".join(lines), ids


async def analyze_batch(cat: str, valid: set[int], batch: list[tuple[int, dict]]) -> dict[int, dict]:
    body = "\n\n".join(f"【题 {i}】（{q['type'] or '题型未知'}）\n{q['text'][:1200]}" for i, q in batch)
    raw, _ = await call_llm([{"role": "system", "content": ANALYZE_SYSTEM},
                             {"role": "user", "content": f"知识点目录：\n{cat}\n\n考试题：\n{body}"}], json_mode=True)
    data = parse_json(raw) or {}
    out: dict[int, dict] = {}
    for it in data.get("items", []) if isinstance(data, dict) else []:
        qi = to_int(it.get("i")) if isinstance(it, dict) else None
        if qi is None:
            continue
        diff = to_int(it.get("difficulty"))
        out[qi] = {
            "topics": [str(t).strip() for t in (it.get("topics") or []) if str(t).strip()][:3],
            "node_ids": [n for n in (to_int(x) for x in (it.get("node_ids") or [])) if n in valid][:3],  # 过滤掉编造的编号（兼容字符串编号）
            "ability": it.get("ability") if it.get("ability") in ABILITIES else "",
            "difficulty": diff if diff in (1, 2, 3, 4, 5) else 3,
        }
    return out


def start_analysis(document_id: int) -> ExamJob:
    old = JOBS.get(document_id)
    if old and old.state == "running":
        return old
    job = ExamJob(document_id=document_id, stage="拆分题目")
    JOBS[document_id] = job
    job.task = asyncio.create_task(_run(job), name=f"exam-{document_id}")
    return job


async def _run(job: ExamJob) -> None:
    try:
        with Session(engine) as s:
            doc = s.get(Document, job.document_id)
            title, course_id = re.sub(r"\.\w+$", "", doc.filename), doc.course_id
            pages = page_texts(ensure_blocks(s, doc))
            s.commit()
            cat, valid = catalog(s, course_id)
        if not pages:
            raise TranslateError("这份试卷没有可读的文字（可能是扫描件）")
        questions = await split_questions(title, pages)
        if not questions:
            raise TranslateError("没有从试卷里拆出题目")
        job.stage, job.total = "识别考点", len(questions)
        indexed = list(enumerate(questions, start=1))
        results: dict[int, dict] = {}
        for i in range(0, len(indexed), BATCH):
            chunk = indexed[i:i + BATCH]
            results.update(await analyze_batch(cat, valid, chunk))
            job.done = min(len(indexed), i + BATCH)
        with Session(engine) as s:
            for old in s.exec(select(ExamQuestion).where(ExamQuestion.document_id == job.document_id)).all():
                s.delete(old)
            for i, q in indexed:
                r = results.get(i, {})
                s.add(ExamQuestion(course_id=course_id, document_id=job.document_id, number=q["no"], qtype=q["type"], text=q["text"],
                                   points=q["points"], answer=q["answer"], topics_json=json.dumps(r.get("topics", []), ensure_ascii=False),
                                   node_ids_json=json.dumps(r.get("node_ids", [])), ability=r.get("ability", ""),
                                   difficulty=r.get("difficulty", 3)))
            s.commit()
        job.state, job.stage = "done", ""
    except TranslateError as e:
        job.state, job.error = "error", str(e)
    except asyncio.CancelledError:
        job.state = "cancelled"
    except Exception as e:  # noqa: BLE001
        log.exception("试卷分析出错")
        job.state, job.error = "error", f"分析出错：{type(e).__name__}"


async def shutdown() -> None:
    from .translate import cancel_tasks

    await cancel_tasks(j.task for j in list(JOBS.values()))


def status(document_id: int) -> dict:
    j = JOBS.get(document_id)
    if not j:
        return {"state": "idle", "stage": "", "total": 0, "done": 0, "error": ""}
    return {"state": j.state, "stage": j.stage, "total": j.total, "done": j.done, "error": j.error}


def question_out(q: ExamQuestion, nodes: dict[int, KnowledgeNode] | None = None) -> dict:
    ids = json.loads(q.node_ids_json or "[]")
    return {"id": q.id, "document_id": q.document_id, "number": q.number, "qtype": q.qtype, "text": q.text, "points": q.points,
            "answer": q.answer, "topics": json.loads(q.topics_json or "[]"), "ability": q.ability, "difficulty": q.difficulty,
            "nodes": [{"id": i, "title": nodes[i].title, "document_id": nodes[i].document_id,
                       "page": (json.loads(nodes[i].refs_json or "[]") or [{}])[0].get("page")}
                      for i in ids if nodes and i in nodes]}


def marks_for_document(s: Session, document_id: int) -> dict[int, list[dict]]:
    """课件的每一页上有哪些考点：{页码: [{题目信息}]}。"""
    doc = s.get(Document, document_id)
    nodes = {n.id: n for n in s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == doc.course_id)).all()}
    mine = {nid: n for nid, n in nodes.items() if n.document_id == document_id and n.level == 3}
    exam_docs = {d.id: d.filename for d in s.exec(select(Document).where(Document.course_id == doc.course_id, Document.kind == "exam")).all()}
    marks: dict[int, list[dict]] = {}
    for q in s.exec(select(ExamQuestion).where(ExamQuestion.course_id == doc.course_id)).all():
        for nid in json.loads(q.node_ids_json or "[]"):
            n = mine.get(nid)
            if not n:
                continue
            for r in json.loads(n.refs_json or "[]"):
                lst = marks.setdefault(int(r["page"]), [])
                if not any(m["question_id"] == q.id for m in lst):
                    lst.append({"question_id": q.id, "exam": re.sub(r"\.\w+$", "", exam_docs.get(q.document_id, "试卷")), "number": q.number,
                                "qtype": q.qtype, "points": q.points, "excerpt": q.text[:90], "topics": json.loads(q.topics_json or "[]"),
                                "node": n.title, "ability": q.ability})
    return marks
