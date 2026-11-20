"""知识梳理：把「课件」提炼成 课件(章节) → 小节 → 知识点 三层结构，并记录出处页码。

流程（每份课件）：按页切成若干窗口 → 每个窗口让模型提炼知识点（并发）→ 同名知识点合并 →
让模型把知识点分组成小节 → 整份课件的子树一次性替换入库（失败不会破坏旧结果）。
只处理 kind = slides 的资料；内容没变化的课件会跳过。
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
from .blockstore import ensure_blocks, src_hash
from .translate import TranslateError, call_llm

log = logging.getLogger("app.knowledge")

WINDOW_CHARS = 6500
CONCURRENCY = 3
MAX_TOPICS_FOR_SINGLE_SECTION = 6


def natural_key(name: str):
    # 每一段带上类型标记 (0, 数字) / (1, 文字)：数字段和文字段永远不会互相比较，
    # 否则 "01-EN…" 和 "暂态01-EN…" 排到同一位置时会出现 int 与 str 比较的 TypeError
    return [(0, int(t)) if t.isdigit() else (1, t.lower()) for t in re.findall(r"\d+|\D+", name)]


def parse_json(raw: str):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def to_int(x) -> int | None:
    """模型有时把数字写成字符串（"7"）或浮点（7.0），统一宽容地转成整数；转不了返回 None。"""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, str) and re.fullmatch(r"\s*-?\d+\s*", x):
        return int(x)
    return None


def norm_title(t: str) -> str:
    return re.sub(r"[\s（）()\-—:：,，.。]", "", t).lower()


# ----------------------------------------------------------------- 切窗口
def page_texts(blocks) -> dict[int, str]:
    pages: dict[int, list[str]] = {}
    for b in blocks:
        if b.kind != "code" and b.text.strip():
            pages.setdefault(b.page, []).append(b.text.strip())
    return {p: "\n".join(t) for p, t in sorted(pages.items())}


def make_windows(pages: dict[int, str], limit: int = WINDOW_CHARS) -> list[tuple[list[int], str]]:
    out, cur_pages, cur = [], [], []
    size = 0
    for p, t in pages.items():
        piece = f"[第{p}页]\n{t}"
        if cur and size + len(piece) > limit:
            out.append((cur_pages, "\n\n".join(cur)))
            cur_pages, cur, size = [], [], 0
        cur_pages.append(p)
        cur.append(piece)
        size += len(piece)
    if cur:
        out.append((cur_pages, "\n\n".join(cur)))
    return out


# ----------------------------------------------------------------- 提示词
EXTRACT_SYSTEM = """你是理工科课程的助教。下面是一份课件的一部分（带「[第N页]」页码标记，可能是英文）。请提炼其中讲授的知识点。
输出 JSON：{"topics":[{"title":"知识点名称（中文；专业术语后括号附英文，如「潮流计算（power flow）」）","summary":"一两句话讲清它是什么、有什么用","points":["要点、公式、结论、判据（每条尽量短，最多 6 条）"],"pages":[出现的页码数字],"importance":1到3}]}
规则：
1. 只依据给定内容，不要编造；同一个知识点合并成一条；
2. 忽略目录、封面、致谢、参考文献、页眉页脚、课程安排等非知识内容；
3. importance：3=核心概念/重要公式/反复出现，2=一般，1=补充或举例；
4. 中文输出，公式、符号保持原样；如果这部分没有知识内容，输出 {"topics":[]}。"""

MERGE_SYSTEM = """你是理工科课程的助教。下面是一份课件里提炼出的知识点列表（带编号）。请把它们整理成层次清晰的小节。
输出 JSON：{"title":"这份课件的章节标题（中文）","summary":"一句话概括这份课件","sections":[{"title":"小节名称","summary":"一句话","topics":[知识点编号, ...]}]}
规则：每个知识点编号必须恰好出现一次；小节 2 到 8 个，按讲授顺序排列；小节名称要具体，不要叫「其他」「综合」之类。"""


# ----------------------------------------------------------------- 提炼
def _clean_topic(t: dict, window_pages: list[int]) -> dict | None:
    title = str(t.get("title", "")).strip()
    if not title or len(title) > 80:
        return None
    pts = [str(x).strip() for x in (t.get("points") or []) if str(x).strip()][:8]
    pages = sorted({p for p in (to_int(x) for x in (t.get("pages") or [])) if p in window_pages}) or window_pages[:1]
    imp = to_int(t.get("importance"))
    return {"title": title, "summary": str(t.get("summary", "")).strip(), "points": pts, "pages": pages,
            "importance": imp if imp in (1, 2, 3) else 2}


async def extract_window(doc_title: str, pages: list[int], text: str) -> list[dict]:
    raw, _model = await call_llm(
        [{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": f"课件：《{doc_title}》\n\n{text}"}], json_mode=True)
    data = parse_json(raw)
    if data is None:
        raise TranslateError("模型没有按要求返回 JSON")
    return [c for c in (_clean_topic(t, pages) for t in data.get("topics", []) if isinstance(t, dict)) if c]


def merge_same_title(topics: list[dict]) -> list[dict]:
    """不同窗口里提炼出同名知识点时合并：要点取并集、页码取并集。"""
    out: dict[str, dict] = {}
    for t in topics:
        k = norm_title(t["title"])
        if k in out:
            o = out[k]
            o["points"] = list(dict.fromkeys(o["points"] + t["points"]))[:8]
            o["pages"] = sorted(set(o["pages"]) | set(t["pages"]))
            o["importance"] = max(o["importance"], t["importance"])
            o["summary"] = o["summary"] or t["summary"]
        else:
            out[k] = dict(t)
    return list(out.values())


async def group_sections(doc_title: str, topics: list[dict]) -> dict:
    if len(topics) <= MAX_TOPICS_FOR_SINGLE_SECTION:
        return {"title": doc_title, "summary": "", "sections": [{"title": doc_title, "summary": "", "topics": list(range(len(topics)))}]}
    listing = "\n".join(f"{i}. {t['title']} —— {t['summary'][:60]}" for i, t in enumerate(topics))
    raw, _ = await call_llm([{"role": "system", "content": MERGE_SYSTEM},
                             {"role": "user", "content": f"课件：《{doc_title}》\n\n{listing}"}], json_mode=True)
    data = parse_json(raw) or {}
    seen: set[int] = set()
    sections = []
    for sec in data.get("sections", []) if isinstance(data, dict) else []:
        idx = [i for i in (to_int(x) for x in sec.get("topics", [])) if i is not None and 0 <= i < len(topics) and i not in seen]
        seen.update(idx)
        if idx and str(sec.get("title", "")).strip():
            sections.append({"title": str(sec["title"]).strip(), "summary": str(sec.get("summary", "")).strip(), "topics": idx})
    rest = [i for i in range(len(topics)) if i not in seen]
    if rest:  # 模型漏掉的知识点不能丢
        sections.append({"title": "补充要点", "summary": "", "topics": rest})
    if not sections:
        sections = [{"title": doc_title, "summary": "", "topics": list(range(len(topics)))}]
    return {"title": str(data.get("title") or doc_title).strip(), "summary": str(data.get("summary", "")).strip(), "sections": sections}


# ----------------------------------------------------------------- 入库
def save_tree(s: Session, course_id: int, doc: Document, tree: dict, topics: list[dict], order: int, h: str) -> int:
    for n in s.exec(select(KnowledgeNode).where(KnowledgeNode.document_id == doc.id)).all():
        s.delete(n)
    s.flush()
    ch = KnowledgeNode(course_id=course_id, document_id=doc.id, level=1, order=order, title=tree["title"] or doc.filename,
                       summary=tree.get("summary", ""), src_hash=h, importance=2,
                       refs_json=json.dumps([{"document_id": doc.id, "page": 1}]))
    s.add(ch)
    s.flush()
    count = 0
    for si, sec in enumerate(tree["sections"]):
        sn = KnowledgeNode(course_id=course_id, document_id=doc.id, parent_id=ch.id, level=2, order=si,
                           title=sec["title"], summary=sec.get("summary", ""))
        s.add(sn)
        s.flush()
        pages_all: set[int] = set()
        for ti, idx in enumerate(sec["topics"]):
            t = topics[idx]
            pages_all |= set(t["pages"])
            s.add(KnowledgeNode(course_id=course_id, document_id=doc.id, parent_id=sn.id, level=3, order=ti, title=t["title"],
                                summary=t["summary"], importance=t["importance"],
                                points_json=json.dumps(t["points"], ensure_ascii=False),
                                refs_json=json.dumps([{"document_id": doc.id, "page": p} for p in t["pages"]])))
            count += 1
        if pages_all:
            sn.refs_json = json.dumps([{"document_id": doc.id, "page": min(pages_all)}])
            s.add(sn)
    s.commit()
    return count


# ----------------------------------------------------------------- 任务
@dataclass
class BuildJob:
    course_id: int
    state: str = "running"  # running / done / error / cancelled
    total: int = 0
    done: int = 0
    current: str = ""
    error: str = ""
    failed: list = field(default_factory=list)
    skipped: int = 0
    started: float = field(default_factory=time.time)
    task: asyncio.Task | None = None


JOBS: dict[int, BuildJob] = {}


def doc_hash(blocks) -> str:
    return src_hash("\n".join(b.text for b in blocks))


def start_build(course_id: int, document_ids: list[int] | None = None, force: bool = False) -> BuildJob | None:
    old = JOBS.get(course_id)
    if old and old.state == "running":
        return old
    with Session(engine) as s:
        docs = [d for d in s.exec(select(Document).where(Document.course_id == course_id, Document.kind == "slides",
                                                         Document.status == "ready")).all()
                if document_ids is None or d.id in document_ids]
        docs.sort(key=lambda d: natural_key(d.filename))
        todo, skipped = [], 0
        for order, d in enumerate(docs):
            h = doc_hash(ensure_blocks(s, d))
            have = s.exec(select(KnowledgeNode).where(KnowledgeNode.document_id == d.id, KnowledgeNode.level == 1)).first()
            if not force and have and have.src_hash == h:
                skipped += 1
                continue
            todo.append((d.id, order, h))
        s.commit()
    job = BuildJob(course_id=course_id, total=len(todo), skipped=skipped)
    JOBS[course_id] = job
    if not todo:
        job.state = "done"
        return job
    job.task = asyncio.create_task(_run(job, todo), name=f"knowledge-{course_id}")
    return job


async def _build_doc(doc_id: int, order: int, h: str, course_id: int, job: BuildJob) -> int:
    with Session(engine) as s:
        doc = s.get(Document, doc_id)
        title = re.sub(r"\.\w+$", "", doc.filename)
        pages = page_texts(ensure_blocks(s, doc))
        s.commit()
    job.current = title
    if not pages:
        raise TranslateError("这份资料没有可读的文字（可能是扫描件）")
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(ps, text):
        async with sem:
            return await extract_window(title, ps, text)

    parts = await asyncio.gather(*(one(ps, t) for ps, t in make_windows(pages)))
    topics = merge_same_title([t for part in parts for t in part])
    if not topics:
        raise TranslateError("没有提炼出知识点（这份资料可能不是讲授内容）")
    tree = await group_sections(title, topics)
    with Session(engine) as s:
        doc = s.get(Document, doc_id)
        return save_tree(s, course_id, doc, tree, topics, order, h)


async def _run(job: BuildJob, todo: list[tuple[int, int, str]]) -> None:
    streak = 0
    try:
        for doc_id, order, h in todo:
            try:
                await _build_doc(doc_id, order, h, job.course_id, job)
                streak = 0
            except TranslateError as e:
                streak += 1
                job.failed.append({"document_id": doc_id, "error": str(e)})
                if "都失败" in str(e) or "还没有配置" in str(e) or streak >= 2:
                    job.state, job.error = "error", str(e)
                    return
            except Exception as e:  # noqa: BLE001
                log.exception("知识梳理出错")
                job.failed.append({"document_id": doc_id, "error": f"{type(e).__name__}"})
            job.done += 1
        job.state = "done"
    except asyncio.CancelledError:
        job.state = "cancelled"
    finally:
        job.current = ""


def cancel_build(course_id: int) -> bool:
    job = JOBS.get(course_id)
    if job and job.state == "running" and job.task:
        job.state = "cancelled"
        job.task.cancel()
        return True
    return False


async def shutdown() -> None:
    from .translate import cancel_tasks

    await cancel_tasks(j.task for j in list(JOBS.values()))


# ----------------------------------------------------------------- 读取
def exam_counts(s: Session, course_id: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    for q in s.exec(select(ExamQuestion).where(ExamQuestion.course_id == course_id)).all():
        try:
            for nid in json.loads(q.node_ids_json or "[]"):
                counts[nid] = counts.get(nid, 0) + 1
        except ValueError:
            pass
    return counts


def get_tree(s: Session, course_id: int) -> list[dict]:
    nodes = s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == course_id)).all()
    counts = exam_counts(s, course_id)
    by_parent: dict[int | None, list[KnowledgeNode]] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)

    def build(n: KnowledgeNode) -> dict:
        kids = [build(c) for c in sorted(by_parent.get(n.id, []), key=lambda x: x.order)]
        own = counts.get(n.id, 0)
        return {"id": n.id, "document_id": n.document_id, "level": n.level, "title": n.title, "summary": n.summary,
                "points": json.loads(n.points_json) if n.points_json else [], "refs": json.loads(n.refs_json) if n.refs_json else [],
                "importance": n.importance, "exam_count": own, "exam_total": own + sum(k["exam_total"] for k in kids), "children": kids}

    roots = sorted(by_parent.get(None, []), key=lambda x: x.order)
    return [build(r) for r in roots]


def status(course_id: int) -> dict:
    j = JOBS.get(course_id)
    if not j:
        return {"state": "idle", "total": 0, "done": 0, "current": "", "error": "", "failed": [], "skipped": 0}
    return {"state": j.state, "total": j.total, "done": j.done, "current": j.current, "error": j.error,
            "failed": j.failed, "skipped": j.skipped}


def to_markdown(tree: list[dict], docs: dict[int, str]) -> str:
    out = ["# 知识梳理", ""]

    def walk(n: dict, depth: int):
        if n["level"] <= 2:
            out.append(f"{'#' * (depth + 1)} {n['title']}" + (f"  （考点 ×{n['exam_total']}）" if n["exam_total"] else ""))
            if n["summary"]:
                out.extend(["", n["summary"]])
            out.append("")
        else:
            pg = "、".join(str(r["page"]) for r in n["refs"])
            star = "★" * n["importance"] if n["importance"] > 1 else ""
            out.append(f"- **{n['title']}** {star}" + (f" （第 {pg} 页）" if pg else "") + (f" 〔考点 ×{n['exam_count']}〕" if n["exam_count"] else ""))
            if n["summary"]:
                out.append(f"  - {n['summary']}")
            out.extend(f"  - {p}" for p in n["points"])
        for c in n["children"]:
            walk(c, depth + 1)
        if n["level"] == 2:
            out.append("")

    for r in tree:
        walk(r, 1)
    return "\n".join(out)
