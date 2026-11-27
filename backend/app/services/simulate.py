"""真题仿真：学真题“考什么、怎么考”，出新题——考点、能力、难度、题型、分值相同，题目本身（场景、数据、措辞）全新。

流程
1. 考情画像：把已分析的真题统计成题型 / 能力 / 难度 / 知识点分布和试卷结构。
2. 命题规格：每道真题只抽象成「考点 + 能力 + 难度 + 题型 + 分值 + 小问数」，连同对应知识点的课件内容交给模型。
3. 防抄袭：新题和这门课所有真题逐一比较（措辞 3 元组 / 序列相似度 / 数据重合），太像就带着“哪里像”的反馈重出，最多 3 次，仍然太像就丢弃。
4. 模拟卷：照着一份真题的结构（题型顺序、每题分值、考点）逐题仿真，组成一份新卷子，可导出带答案的 Markdown。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace

from sqlmodel import Session, select

from ..db import engine
from ..models import Document, ExamQuestion, KnowledgeNode, Paper, PaperItem, Question
from . import questions as qsvc
from . import similarity
from .knowledge import parse_json, to_int
from .translate import TranslateError, call_llm

log = logging.getLogger("app.simulate")

MAX_TRIES = 3
CONCURRENCY = 3
ZH_NUM = "一二三四五六七八九十"

SIM_SYSTEM = """你是理工科课程的命题老师。下面给出一道往年真题的「命题规格」和真题原文，请出一道全新的仿真题：考查同样的考点、同样的能力层级、相近的难度和篇幅，但题目本身必须是原创的。
硬性要求：
1. 考点和能力不变：必须考查「规格」里列出的考点，解题所需的步骤和思维层级与真题相当；
2. 外观必须完全不同：换一个背景场景或对象，换掉全部数据与参数；叙述框架也要换——不要沿用真题的句式顺序、已知条件的排列和小问的提法（例如真题先给线路参数再给负荷，你就先给负荷或运行工况；小问换一种问法、换一种顺序，但考查的步骤相当）；不得沿用真题里的句子、短语、已知条件或数值；
3. 题型、小问数量与真题一致；
4. 必须有确定的答案，答案和解析要自洽、正确。
真题原文只是让你了解考法，不是让你改写它。
""" + qsvc.FORMAT_RULES


# ----------------------------------------------------------------- 考情画像
def _sub_count(text: str) -> int:
    n = len(set(re.findall(r"[（(]\s*(\d{1,2})\s*[)）]", text)))
    return n if n >= 2 else 1


def sections_of(qs: list[ExamQuestion]) -> list[dict]:
    """试卷结构：按出现顺序把相邻的同题型题归成一大题。"""
    out: list[dict] = []
    for q in qs:
        if out and out[-1]["qtype"] == q.qtype:
            out[-1]["count"] += 1
            out[-1]["points"] += q.points or 0
        else:
            out.append({"qtype": q.qtype, "count": 1, "points": q.points or 0})
    return out


def exam_profile(s: Session, course_id: int) -> dict:
    qs = list(s.exec(select(ExamQuestion).where(ExamQuestion.course_id == course_id).order_by(ExamQuestion.document_id, ExamQuestion.id)).all())
    docs = {d.id: d.filename for d in s.exec(select(Document).where(Document.course_id == course_id, Document.kind == "exam")).all()}
    nodes = {n.id: n for n in s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == course_id)).all()}
    freq: Counter = Counter()
    for q in qs:
        for nid in json.loads(q.node_ids_json or "[]"):
            freq[nid] += 1
    exams = []
    for did, name in docs.items():
        mine = [q for q in qs if q.document_id == did]
        if mine:
            exams.append({"document_id": did, "name": re.sub(r"\.\w+$", "", name), "questions": len(mine),
                          "total_points": sum(q.points or 0 for q in mine), "sections": sections_of(mine)})
    pts = [q.points for q in qs if q.points]
    uncovered = [{"id": n.id, "title": n.title} for n in nodes.values() if n.level == 3 and n.importance >= 3 and freq[n.id] == 0][:15]
    return {
        "analyzed": len(qs),
        "by_type": dict(Counter(q.qtype or "未知" for q in qs)),
        "by_ability": dict(Counter(q.ability or "未知" for q in qs)),
        "by_difficulty": {str(k): v for k, v in sorted(Counter(q.difficulty for q in qs).items())},
        "avg_points": round(sum(pts) / len(pts), 1) if pts else None,
        "top_nodes": [{"id": nid, "title": nodes[nid].title, "count": c} for nid, c in freq.most_common(12) if nid in nodes],
        "uncovered_key_nodes": uncovered,  # 重点但真题里还没考过的——押题方向
        "exams": exams,
    }


# ----------------------------------------------------------------- 仿真一道题
def _full_text(c: dict) -> str:
    """用来比较相似度的文本：题干 + 选项。"""
    opts = json.loads(c.get("options_json") or "[]")
    return c["stem"] + "\n" + "\n".join(opts)


def build_sim_messages(eq, node_ctx: str, want: str, feedback: str = "") -> list[dict]:
    topics = "、".join(json.loads(eq.topics_json or "[]")) or "（见知识点）"
    spec = [f"题型：{qsvc.QTYPE_NAME[want]}（真题题型：{eq.qtype or '未知'}）",
            f"考点：{topics}", f"能力层级：{eq.ability or '应用'}", f"难度：{eq.difficulty}/5",
            f"分值：{eq.points if eq.points else '未知'}", f"小问数：{_sub_count(eq.text)}"]
    user = ("【命题规格】\n" + "\n".join(spec) + (f"\n\n【对应知识点】\n{node_ctx}" if node_ctx else "") +
            f"\n\n【真题原文（只用于了解考法，禁止沿用其措辞、场景和数据）】\n{eq.text[:1200]}\n\n"
            f"出题要求（共 1 题）：\n1. 题型 {want}（{qsvc.QTYPE_NAME[want]}），难度 {eq.difficulty}")
    if feedback:
        user += f"\n\n【上一版被退回】{feedback}"
    return [{"role": "system", "content": SIM_SYSTEM}, {"role": "user", "content": user}]


def node_context(s: Session, eq, cache: dict) -> tuple[int | None, str]:
    ids = json.loads(eq.node_ids_json or "[]")
    parts, first = [], None
    for nid in ids[:2]:
        n = s.get(KnowledgeNode, nid)
        if not n:
            continue
        first = first or n.id
        pts = json.loads(n.points_json or "[]")
        ex = qsvc.source_excerpt(s, n, cache)
        parts.append(f"{n.title}：{n.summary}" + ("\n要点：" + "；".join(pts) if pts else "") + (f"\n课件摘录：{ex[:600]}" if ex else ""))
    return first, "\n\n".join(parts)


async def simulate_question(eq_id: int, references: list[tuple[int, str]], cache: dict) -> tuple[dict | None, str]:
    """针对一道真题出一道仿真题。返回 (题目字段 | None, 失败原因)。"""
    with Session(engine) as s:
        eq = s.get(ExamQuestion, eq_id)
        if not eq:
            return None, "真题不存在"
        snap = SimpleNamespace(**{k: getattr(eq, k) for k in ("id", "qtype", "text", "points", "ability", "difficulty", "topics_json", "node_ids_json")})
        node_id, ctx = node_context(s, snap, cache)
    eq = snap  # 之后只用这份快照（会话里的对象在 commit 后会过期）
    want = qsvc.qtype_of_exam(eq.qtype, eq.text)
    feedback, last = "", "没有得到合格的题目"
    for attempt in range(MAX_TRIES):
        raw, _ = await call_llm(build_sim_messages(eq, ctx, want, feedback), json_mode=True)
        data = parse_json(raw) or {}
        items = data.get("questions", []) if isinstance(data, dict) else []
        c = qsvc.clean_generated(items[0], want) if items else None
        if not c:
            last, feedback = "题目格式不合格", "上一版格式不合格（选项数、答案或空数不对），请严格按格式规则重出。"
            continue
        res = similarity.check_against(_full_text(c), references)
        if res["too_similar"]:
            d = res["detail"]
            last = f"与真题太像（措辞 {d['ngram']:.0%}，序列 {d['seq']:.0%}，数据重合 {d['numbers']:.0%}）"
            feedback = (f"和真题太像了（措辞相似 {d['ngram']:.0%}、整体相似 {d['seq']:.0%}、数据重合 {d['numbers']:.0%}）。"
                        "请换一个完全不同的背景场景、换掉全部数据、换一种叙述方式再出，考点和能力不变。")
            continue
        c.update(node_id=node_id, origin="simulated", based_on=eq.id, similarity=res["score"],
                 points=eq.points or 0, difficulty=eq.difficulty or c["difficulty"], ability=eq.ability or c["ability"])
        return c, ""
    return None, last


def real_references(s: Session, course_id: int) -> list[tuple[int, str]]:
    return [(q.id, q.text) for q in s.exec(select(ExamQuestion).where(ExamQuestion.course_id == course_id)).all()]


# ----------------------------------------------------------------- 批量仿真任务
@dataclass
class SimJob:
    course_id: int
    state: str = "running"
    total: int = 0
    done: int = 0
    created: int = 0
    rejected: int = 0
    error: str = ""
    task: asyncio.Task | None = None


JOBS: dict[int, SimJob] = {}


def _slots(s: Session, course_id: int, exam_document_id: int | None, question_ids: list[int] | None) -> list[ExamQuestion]:
    stmt = select(ExamQuestion).where(ExamQuestion.course_id == course_id).order_by(ExamQuestion.document_id, ExamQuestion.id)
    if question_ids:
        stmt = stmt.where(ExamQuestion.id.in_(question_ids))  # type: ignore[attr-defined]
    elif exam_document_id:
        stmt = stmt.where(ExamQuestion.document_id == exam_document_id)
    return list(s.exec(stmt).all())


def start_simulation(course_id: int, exam_document_id: int | None, question_ids: list[int] | None, variants: int) -> SimJob:
    old = JOBS.get(course_id)
    if old and old.state == "running":
        return old
    with Session(engine) as s:
        slots = _slots(s, course_id, exam_document_id, question_ids)
        if not slots:
            raise TranslateError("还没有分析过的真题：先在「试卷考点」里上传并分析一份试卷。")
        ids = [q.id for q in slots][:60]
    job = SimJob(course_id=course_id, total=len(ids) * max(1, min(variants, 3)))
    JOBS[course_id] = job
    job.task = asyncio.create_task(_run(job, ids, max(1, min(variants, 3))), name=f"simulate-{course_id}")
    return job


def cancel_simulation(course_id: int) -> bool:
    j = JOBS.get(course_id)
    if j and j.state == "running" and j.task:
        j.task.cancel()
        return True
    return False


async def _run(job: SimJob, eq_ids: list[int], variants: int) -> None:
    try:
        with Session(engine) as s:
            refs = real_references(s, job.course_id)
        sem = asyncio.Semaphore(CONCURRENCY)
        cache: dict = {}
        reasons: list[str] = []

        async def one(eq_id: int):
            async with sem:
                try:
                    c, why = await simulate_question(eq_id, refs, cache)
                except TranslateError as e:
                    reasons.append(str(e))
                    return
                if not c:
                    job.rejected += 1
                    reasons.append(why)
                    return
                with Session(engine) as s:
                    have = {qsvc.stem_key(x) for x in s.exec(select(Question.stem).where(Question.course_id == job.course_id)).all()}
                    if qsvc.stem_key(c["stem"]) in have:
                        job.rejected += 1
                        return
                    s.add(Question(course_id=job.course_id, **c))
                    s.commit()
                job.created += 1

        async def task(eq_id: int):
            await one(eq_id)
            job.done += 1

        await asyncio.gather(*(task(i) for i in eq_ids for _ in range(variants)))
        if job.created == 0 and reasons:
            raise TranslateError(reasons[0])
        job.state = "done"
        if job.rejected:
            job.error = f"{job.rejected} 道题没出成（太像真题或格式不合格，已丢弃）"
    except asyncio.CancelledError:
        job.state = "cancelled"
        raise
    except Exception as e:  # noqa: BLE001
        job.state, job.error = "error", str(e)[:300]


def sim_status(course_id: int) -> dict:
    j = JOBS.get(course_id)
    if not j:
        return {"state": "idle", "total": 0, "done": 0, "created": 0, "rejected": 0, "error": ""}
    return {"state": j.state, "total": j.total, "done": j.done, "created": j.created, "rejected": j.rejected, "error": j.error}


# ----------------------------------------------------------------- 模拟卷
@dataclass
class PaperJob:
    course_id: int
    state: str = "running"
    total: int = 0
    done: int = 0
    paper_id: int | None = None
    error: str = ""
    task: asyncio.Task | None = None


PAPER_JOBS: dict[int, PaperJob] = {}


def start_paper(course_id: int, exam_document_id: int | None, title: str = "") -> PaperJob:
    old = PAPER_JOBS.get(course_id)
    if old and old.state == "running":
        return old
    with Session(engine) as s:
        if not exam_document_id:  # 默认参照题目最多的那份真题
            cnt = Counter(q.document_id for q in s.exec(select(ExamQuestion).where(ExamQuestion.course_id == course_id)).all())
            if not cnt:
                raise TranslateError("还没有分析过的真题：先在「试卷考点」里上传并分析一份试卷。")
            exam_document_id = cnt.most_common(1)[0][0]
        slots = _slots(s, course_id, exam_document_id, None)
        if not slots:
            raise TranslateError("这份试卷还没有分析出题目。")
        doc = s.get(Document, exam_document_id)
        name = re.sub(r"\.\w+$", "", doc.filename) if doc else "真题"
        ids = [q.id for q in slots]
    job = PaperJob(course_id=course_id, total=len(ids))
    PAPER_JOBS[course_id] = job
    job.task = asyncio.create_task(_run_paper(job, ids, exam_document_id, title or f"{name} · 仿真卷"), name=f"paper-{course_id}")
    return job


async def _run_paper(job: PaperJob, eq_ids: list[int], exam_document_id: int, title: str) -> None:
    try:
        with Session(engine) as s:
            refs = real_references(s, job.course_id)
            used = {i.question_id for i in s.exec(select(PaperItem)).all()}
            reuse: dict[int, int] = {}  # 真题 id → 可复用的、还没进过卷子的仿真题
            for q in s.exec(select(Question).where(Question.course_id == job.course_id, Question.origin == "simulated")).all():
                if q.based_on and q.id not in used and q.based_on not in reuse:
                    reuse[q.based_on] = q.id
        sem = asyncio.Semaphore(CONCURRENCY)
        cache: dict = {}
        slot_q: dict[int, int | None] = {}
        reasons: list[str] = []

        async def fill(eq_id: int):
            async with sem:
                try:
                    if eq_id in reuse:
                        slot_q[eq_id] = reuse[eq_id]
                    else:
                        c, why = await simulate_question(eq_id, refs, cache)
                        if not c:
                            reasons.append(why)
                            slot_q[eq_id] = None
                        else:
                            with Session(engine) as s:
                                q = Question(course_id=job.course_id, **c)
                                s.add(q)
                                s.commit()
                                slot_q[eq_id] = q.id
                except TranslateError as e:
                    reasons.append(str(e))
                    slot_q[eq_id] = None
                job.done += 1

        await asyncio.gather(*(fill(i) for i in eq_ids))
        got = [(i, slot_q[i]) for i in eq_ids if slot_q.get(i)]
        if not got:
            raise TranslateError(reasons[0] if reasons else "没有出成任何题目")
        with Session(engine) as s:
            paper = Paper(course_id=job.course_id, title=title, based_on_document=exam_document_id)
            s.add(paper)
            s.flush()
            sec_no, last_t = 0, None
            for order, (eq_id, qid) in enumerate(got, 1):
                eq = s.get(ExamQuestion, eq_id)
                if eq.qtype != last_t:
                    sec_no += 1
                    last_t = eq.qtype
                s.add(PaperItem(paper_id=paper.id, question_id=qid, order=order,
                                section=f"{ZH_NUM[min(sec_no, 10) - 1]}、{eq.qtype or '综合'}题", points=eq.points or 0))
            s.commit()
            job.paper_id = paper.id
        job.state = "done"
        if len(got) < len(eq_ids):
            job.error = f"有 {len(eq_ids) - len(got)} 道题没出成（太像真题或格式不合格），卷子里已跳过"
    except asyncio.CancelledError:
        job.state = "cancelled"
        raise
    except Exception as e:  # noqa: BLE001
        job.state, job.error = "error", str(e)[:300]


def cancel_paper(course_id: int) -> bool:
    j = PAPER_JOBS.get(course_id)
    if j and j.state == "running" and j.task:
        j.task.cancel()
        return True
    return False


def paper_status(course_id: int) -> dict:
    j = PAPER_JOBS.get(course_id)
    if not j:
        return {"state": "idle", "total": 0, "done": 0, "paper_id": None, "error": ""}
    return {"state": j.state, "total": j.total, "done": j.done, "paper_id": j.paper_id, "error": j.error}


def paper_detail(s: Session, paper: Paper, reveal: bool) -> dict:
    items = s.exec(select(PaperItem).where(PaperItem.paper_id == paper.id).order_by(PaperItem.order)).all()
    qs = {q.id: q for q in s.exec(select(Question).where(Question.id.in_([i.question_id for i in items]))).all()}  # type: ignore[attr-defined]
    return {"id": paper.id, "title": paper.title, "duration_min": paper.duration_min, "created_at": paper.created_at.isoformat(),
            "total_points": sum(i.points for i in items),
            "items": [{"order": i.order, "section": i.section, "points": i.points, **qsvc.question_out(qs[i.question_id], reveal)}
                      for i in items if i.question_id in qs]}


def paper_markdown(s: Session, paper: Paper, with_answers: bool) -> str:
    d = paper_detail(s, paper, with_answers)
    out = [f"# {d['title']}", "", f"满分 {d['total_points']:g} 分 · 建议用时 {d['duration_min']} 分钟 · 题目为 AI 依真题考点原创，非真题原文", ""]
    sec = None
    for it in d["items"]:
        if it["section"] != sec:
            sec = it["section"]
            out += [f"## {sec}", ""]
        pts = f"（{it['points']:g} 分）" if it["points"] else ""
        out.append(f"**{it['order']}.** {pts}{it['stem']}")
        for i, o in enumerate(it["options"]):
            out.append(f"   - {qsvc.LETTERS[i]}. {o}")
        out.append("")
    if with_answers:
        out += ["---", "", "## 参考答案与解析", ""]
        for it in d["items"]:
            line = f"**{it['order']}.** {it['answer']}"
            if it["explanation"]:
                line += f"　{it['explanation']}"
            out.append(line)
            if it["key_points"]:
                out.append("   - 评分要点：" + "；".join(it["key_points"]))
            out.append("")
    return "\n".join(out)


async def shutdown() -> None:
    from .translate import cancel_tasks

    await cancel_tasks([j.task for j in list(JOBS.values())] + [j.task for j in list(PAPER_JOBS.values())])
