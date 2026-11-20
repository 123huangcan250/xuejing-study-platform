"""FSRS 闪卡：手动/AI 生成、到期调度、评分复习。"""
from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Course, Flashcard, ReviewLog
from ..services import fsrs_service, gamification, llm

router = APIRouter(prefix="/api", tags=["flashcards"])


class CardIn(BaseModel):
    front: str
    back: str
    card_type: str = "basic"
    source: str = "manual"
    source_ref: str = ""


class CardPatch(BaseModel):
    front: str | None = None
    back: str | None = None
    card_type: str | None = None


class GenerateIn(BaseModel):
    text: str
    context: str = ""
    count: int = 3


class ReviewIn(BaseModel):
    rating: int  # 1 Again 2 Hard 3 Good 4 Easy


def card_out(c: Flashcard, with_preview: bool = False) -> dict:
    d = c.model_dump(exclude={"fsrs_json"})
    d["due"] = c.due.isoformat()
    d["created_at"] = c.created_at.isoformat()
    if with_preview:
        d["preview"] = fsrs_service.preview(c.fsrs_json)
    return d


@router.get("/courses/{cid}/flashcards")
def list_cards(cid: int, s: Session = Depends(get_session)):
    cards = s.exec(select(Flashcard).where(Flashcard.course_id == cid).order_by(Flashcard.created_at.desc())).all()
    return [card_out(c) for c in cards]


@router.post("/courses/{cid}/flashcards")
def create_cards(cid: int, body: list[CardIn], s: Session = Depends(get_session)):
    if not s.get(Course, cid):
        raise HTTPException(404, "课程不存在")
    created = []
    for item in body:
        if not item.front.strip() or not item.back.strip():
            continue
        fj, due = fsrs_service.new_card_json()
        c = Flashcard(course_id=cid, front=item.front.strip(), back=item.back.strip(), card_type=item.card_type,
                      source=item.source, source_ref=item.source_ref, fsrs_json=fj, due=due)
        s.add(c)
        created.append(c)
    s.commit()
    for c in created:
        s.refresh(c)
    out = [card_out(c) for c in created]
    reward = None
    if created:
        reward = gamification.emit(s, "FLASHCARD_CREATED", cid, {"count": len(created)},
                                   xp_override=2 * len(created))
    return {"cards": out, "reward": reward}


@router.patch("/flashcards/{fid}")
def patch_card(fid: int, body: CardPatch, s: Session = Depends(get_session)):
    c = s.get(Flashcard, fid)
    if not c:
        raise HTTPException(404, "闪卡不存在")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    s.add(c)
    s.commit()
    s.refresh(c)
    return card_out(c)


@router.delete("/flashcards/{fid}")
def delete_card(fid: int, s: Session = Depends(get_session)):
    c = s.get(Flashcard, fid)
    if c:
        s.delete(c)
        s.commit()
    return {"ok": True}


@router.get("/flashcards/due")
def due_cards(course_id: int | None = None, limit: int = 50, s: Session = Depends(get_session)):
    q = select(Flashcard).where(Flashcard.due <= datetime.now())
    if course_id is not None:
        q = q.where(Flashcard.course_id == course_id)
    cards = s.exec(q.order_by(Flashcard.due).limit(limit)).all()
    return [card_out(c, with_preview=True) for c in cards]


@router.post("/flashcards/{fid}/review")
def review_card(fid: int, body: ReviewIn, s: Session = Depends(get_session)):
    if body.rating not in (1, 2, 3, 4):
        raise HTTPException(400, "评分必须是 1~4")
    c = s.get(Flashcard, fid)
    if not c:
        raise HTTPException(404, "闪卡不存在")
    was_due = c.due <= datetime.now()
    c.fsrs_json, c.due, c.state = fsrs_service.review(c.fsrs_json, body.rating)
    c.reps += 1
    if body.rating == 1:
        c.lapses += 1
    s.add(c)
    s.add(ReviewLog(flashcard_id=c.id, course_id=c.course_id, rating=body.rating))
    s.commit()
    s.refresh(c)
    out = card_out(c)
    # 只有复习“到期卡”才给 XP，防止提前刷卡刷分；忘记（Again）也给 1 点，鼓励诚实评分
    xp = ({1: 1, 2: 2, 3: 3, 4: 3}[body.rating]) if was_due else 0
    reward = gamification.emit(s, "FLASHCARD_REVIEWED", c.course_id, {"card_id": c.id, "rating": body.rating},
                               xp_override=xp)
    return {"card": out, "reward": reward}


GEN_PROMPT = """你是闪卡制作专家。根据学生选中的学习材料，制作 {count} 张高质量的间隔重复闪卡。
原则：一张卡只考一个知识点；正面是具体的问题（不要“请解释…”这种宽泛问法），背面简洁准确（≤80 字）；
可以出概念卡、填空卡（正面用 ____ 挖空）或公式卡（LaTeX 用 $...$）。
只输出 JSON：{{"cards":[{{"front":"...","back":"...","card_type":"basic|cloze|formula"}}]}}"""


@router.post("/courses/{cid}/flashcards/generate")
async def generate_cards(cid: int, body: GenerateIn):
    count = min(max(body.count, 1), 8)
    user = f"材料：\n{body.text[:4000]}"
    if body.context:
        user += f"\n\n补充上下文：\n{body.context[:2000]}"
    try:
        raw = await llm.chat("fast", [{"role": "system", "content": GEN_PROMPT.format(count=count)},
                                      {"role": "user", "content": user}], json_mode=True)
    except llm.LLMNotConfigured as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"模型调用失败：{str(e)[:300]}") from e
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0) if m else raw)
        cards = [c for c in data.get("cards", []) if c.get("front") and c.get("back")]
    except (ValueError, AttributeError):
        raise HTTPException(502, "模型返回的不是合法 JSON，请重试") from None
    return {"cards": [{"front": c["front"], "back": c["back"], "card_type": c.get("card_type", "basic")} for c in cards]}
