"""题库、作答、掌握度、真题仿真、模拟卷。"""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Attempt, Course, KnowledgeNode, Paper, Question
from ..services import gamification
from ..services import questions as q
from ..services import simulate as sim
from ..services.translate import TranslateError, configured_chain

router = APIRouter(prefix="/api", tags=["questions"])


def _course(s: Session, cid: int) -> Course:
    c = s.get(Course, cid)
    if not c:
        raise HTTPException(404, "课程不存在")
    return c


def _need_model():
    if not configured_chain():
        raise HTTPException(400, "还没有配置可用的模型：请在「设置」里配置「轻量快速」或「翻译」档位。")


def _last_attempts(s: Session, cid: int) -> dict[int, Attempt]:
    last: dict[int, Attempt] = {}
    for a in s.exec(select(Attempt).where(Attempt.course_id == cid).order_by(Attempt.created_at)).all():
        last[a.question_id] = a
    return last


# ----------------------------------------------------------------- 题库
@router.get("/courses/{cid}/questions")
def list_questions(cid: int, node_id: int | None = None, origin: str | None = None, status: str = Query("all", pattern="^(all|new|wrong|done)$"),
                   limit: int = 200, s: Session = Depends(get_session)):
    _course(s, cid)
    stmt = select(Question).where(Question.course_id == cid).order_by(Question.created_at.desc(), Question.id.desc())
    if origin:
        stmt = stmt.where(Question.origin == origin)
    rows = list(s.exec(stmt).all())
    if node_id:
        allowed = {n.id for n in q.leaves_for(s, cid, [node_id])}
        rows = [r for r in rows if r.node_id in allowed]
    last = _last_attempts(s, cid)
    if status == "new":
        rows = [r for r in rows if r.id not in last]
    elif status == "wrong":
        rows = [r for r in rows if r.id in last and last[r.id].score < q.WRONG_BELOW]
    elif status == "done":
        rows = [r for r in rows if r.id in last and last[r.id].score >= q.WRONG_BELOW]
    out = []
    for r in rows[: min(limit, 500)]:
        d = q.question_out(r)
        a = last.get(r.id)
        d["last_score"] = a.score if a else None
        out.append(d)
    return out


@router.get("/courses/{cid}/questions/stats")
def stats(cid: int, s: Session = Depends(get_session)):
    _course(s, cid)
    return q.question_stats(s, cid)


class GenerateIn(BaseModel):
    node_ids: list[int] | None = None
    per_node: int = 3
    types: list[str] = list(q.DEFAULT_TYPES)
    difficulty: int | None = None


@router.post("/courses/{cid}/questions/generate")
async def generate(cid: int, body: GenerateIn, s: Session = Depends(get_session)):
    _course(s, cid)
    _need_model()
    try:
        q.start_generation(cid, body.node_ids, body.per_node, body.types, body.difficulty)
    except TranslateError as e:
        raise HTTPException(400, str(e)) from None
    return q.gen_status(cid)


@router.get("/courses/{cid}/questions/generate")
def generate_status(cid: int):
    return q.gen_status(cid)


@router.post("/courses/{cid}/questions/generate/cancel")
async def generate_cancel(cid: int):
    q.cancel_generation(cid)
    return q.gen_status(cid)


@router.get("/courses/{cid}/practice")
def practice(cid: int, count: int = 10, node_id: int | None = None, origin: str | None = None, s: Session = Depends(get_session)):
    _course(s, cid)
    return [q.question_out(x) for x in q.practice_queue(s, cid, count, [node_id] if node_id else None, origin)]


@router.get("/questions/{qid}")
def get_question(qid: int, reveal: bool = False, s: Session = Depends(get_session)):
    x = s.get(Question, qid)
    if not x:
        raise HTTPException(404, "题目不存在")
    return q.question_out(x, reveal)


@router.delete("/questions/{qid}")
def delete_question(qid: int, s: Session = Depends(get_session)):
    x = s.get(Question, qid)
    if not x:
        raise HTTPException(404, "题目不存在")
    s.delete(x)
    s.commit()
    return {"ok": True}


# ----------------------------------------------------------------- 作答
class AttemptIn(BaseModel):
    answer: str = ""
    self_score: float | None = None  # 主观题自评：0 / 0.5 / 1


def _save_attempt(s: Session, x: Question, answer: str, score: float, by: str, feedback: str) -> dict:
    prev = list(s.exec(select(Attempt).where(Attempt.question_id == x.id)).all())
    ever_right = any(a.score >= q.WRONG_BELOW for a in prev)
    a = Attempt(question_id=x.id, course_id=x.course_id, node_id=x.node_id, answer=answer[:4000], score=score, graded_by=by, feedback=feedback)
    s.add(a)
    s.flush()
    card_id = q.ensure_flashcard(s, x) if score < q.WRONG_BELOW else None
    # 奖励：第一次做对最多；做错也算一次尝试给 1 点；已经做对过的题再刷不再给分
    if ever_right:
        xp = 0
    elif score >= q.WRONG_BELOW:
        xp = 3 + x.difficulty
    else:
        xp = 1
    reward = gamification.emit(s, "QUESTION_ANSWERED", x.course_id, {"question_id": x.id, "score": score}, xp_override=xp) if xp else (s.commit() or None)
    return {"attempt_id": a.id, "score": score, "correct": score >= q.WRONG_BELOW, "graded_by": by, "feedback": feedback,
            "flashcard_created": card_id is not None, **q.reference(x), "reward": reward}


@router.post("/questions/{qid}/attempt")
async def attempt(qid: int, body: AttemptIn, s: Session = Depends(get_session)):
    x = s.get(Question, qid)
    if not x:
        raise HTTPException(404, "题目不存在")
    if x.qtype in q.OBJECTIVE:
        score, fb = q.grade_objective(x, body.answer)
        return {"graded": True, **_save_attempt(s, x, body.answer, score, "auto", fb)}
    if body.self_score is not None:
        sc = max(0.0, min(1.0, float(body.self_score)))
        return {"graded": True, **_save_attempt(s, x, body.answer, sc, "self", "")}
    try:
        score, fb = await q.grade_subjective(x, body.answer)
    except Exception:  # noqa: BLE001  模型不可用：退回对照参考答案自评，不丢掉这次作答
        return {"graded": False, "need_self_grade": True, **q.reference(x)}
    return {"graded": True, **_save_attempt(s, x, body.answer, score, "llm", fb)}


class AttemptPatch(BaseModel):
    score: float


@router.patch("/attempts/{aid}")
def patch_attempt(aid: int, body: AttemptPatch, s: Session = Depends(get_session)):
    """对模型的评分不服：改成自评分数。"""
    a = s.get(Attempt, aid)
    if not a:
        raise HTTPException(404, "作答记录不存在")
    a.score, a.graded_by = max(0.0, min(1.0, body.score)), "self"
    s.add(a)
    x = s.get(Question, a.question_id)
    if x and a.score < q.WRONG_BELOW:
        q.ensure_flashcard(s, x)
    s.commit()
    return {"ok": True, "score": a.score}


# ----------------------------------------------------------------- 掌握度
@router.get("/courses/{cid}/mastery")
def mastery(cid: int, s: Session = Depends(get_session)):
    _course(s, cid)
    m = q.mastery_by_node(s, cid)
    nodes = s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == cid)).all()
    counts: dict[int, int] = {}
    for x in s.exec(select(Question.node_id).where(Question.course_id == cid)).all():
        if x:
            counts[x] = counts.get(x, 0) + 1
    out = [{"id": n.id, "parent_id": n.parent_id, "level": n.level, "title": n.title, "importance": n.importance,
            "questions": counts.get(n.id, 0), **(m.get(n.id) or {"mastery": None, "attempts": 0, "last": None})}
           for n in nodes if n.level == 3]
    seen = [o["mastery"] for o in out if o["mastery"] is not None]
    return {"nodes": out, "overall": round(sum(seen) / len(seen), 3) if seen else None, "covered": len(seen), "total": len(out)}


# ----------------------------------------------------------------- 真题仿真
@router.get("/courses/{cid}/exam-profile")
def exam_profile(cid: int, s: Session = Depends(get_session)):
    _course(s, cid)
    return sim.exam_profile(s, cid)


class SimulateIn(BaseModel):
    exam_document_id: int | None = None
    question_ids: list[int] | None = None
    variants: int = 1


@router.post("/courses/{cid}/simulate")
async def simulate(cid: int, body: SimulateIn, s: Session = Depends(get_session)):
    _course(s, cid)
    _need_model()
    try:
        sim.start_simulation(cid, body.exam_document_id, body.question_ids, body.variants)
    except TranslateError as e:
        raise HTTPException(400, str(e)) from None
    return sim.sim_status(cid)


@router.get("/courses/{cid}/simulate")
def simulate_status(cid: int):
    return sim.sim_status(cid)


@router.post("/courses/{cid}/simulate/cancel")
async def simulate_cancel(cid: int):
    sim.cancel_simulation(cid)
    return sim.sim_status(cid)


# ----------------------------------------------------------------- 模拟卷
class PaperIn(BaseModel):
    exam_document_id: int | None = None
    title: str = ""


@router.post("/courses/{cid}/papers")
async def make_paper(cid: int, body: PaperIn, s: Session = Depends(get_session)):
    _course(s, cid)
    _need_model()
    try:
        sim.start_paper(cid, body.exam_document_id, body.title.strip())
    except TranslateError as e:
        raise HTTPException(400, str(e)) from None
    return sim.paper_status(cid)


@router.post("/courses/{cid}/papers/cancel")
async def paper_cancel(cid: int):
    sim.cancel_paper(cid)
    return sim.paper_status(cid)


@router.get("/courses/{cid}/papers/status")
def paper_status(cid: int):
    return sim.paper_status(cid)


@router.get("/courses/{cid}/papers")
def list_papers(cid: int, s: Session = Depends(get_session)):
    _course(s, cid)
    rows = s.exec(select(Paper).where(Paper.course_id == cid).order_by(Paper.created_at.desc())).all()
    return [{"id": p.id, "title": p.title, "created_at": p.created_at.isoformat(), "duration_min": p.duration_min} for p in rows]


@router.get("/papers/{pid}")
def get_paper(pid: int, reveal: bool = False, s: Session = Depends(get_session)):
    p = s.get(Paper, pid)
    if not p:
        raise HTTPException(404, "试卷不存在")
    return sim.paper_detail(s, p, reveal)


@router.delete("/papers/{pid}")
def delete_paper(pid: int, s: Session = Depends(get_session)):
    p = s.get(Paper, pid)
    if not p:
        raise HTTPException(404, "试卷不存在")
    s.delete(p)
    s.commit()
    return {"ok": True}


@router.get("/papers/{pid}/export")
def export_paper(pid: int, answers: bool = False, s: Session = Depends(get_session)):
    p = s.get(Paper, pid)
    if not p:
        raise HTTPException(404, "试卷不存在")
    md = sim.paper_markdown(s, p, answers)
    name = quote(f"{p.title}{'（含答案）' if answers else ''}.md")
    return Response(md, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})


_ = json
