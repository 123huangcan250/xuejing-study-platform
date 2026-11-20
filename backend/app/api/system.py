"""设置、Token 用量、学习者档案（XP/连胜/成就/热力图）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..models import LearningEvent, TokenUsage
from ..services import gamification, llm, settings_store

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/settings")
def get_settings():
    return settings_store.public_config()


@router.put("/settings")
def put_settings(body: dict[str, Any]):
    settings_store.save_config(body)
    from ..services import rag

    rag._collection.cache_clear()
    return settings_store.public_config()


@router.post("/settings/test/{tier}")
async def test_settings(tier: str):
    if tier not in settings_store.TIERS:
        raise HTTPException(400, "未知档位")
    try:
        return await llm.test_tier(tier)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)[:300]}


@router.get("/usage")
def usage(s: Session = Depends(get_session)):
    start = datetime.combine(date.today().replace(day=1), datetime.min.time())
    rows = s.exec(select(TokenUsage.tier, TokenUsage.model,
                         func.sum(TokenUsage.prompt_tokens), func.sum(TokenUsage.completion_tokens),
                         func.count()).where(TokenUsage.created_at >= start)
                  .group_by(TokenUsage.tier, TokenUsage.model)).all()
    cfg = settings_store.load_config()
    items, total_cost, total_tokens = [], 0.0, 0
    for tier, model, p, c, n in rows:
        t = cfg["tiers"].get(tier, {})
        cost = (p or 0) / 1e6 * float(t.get("price_in") or 0) + (c or 0) / 1e6 * float(t.get("price_out") or 0)
        total_cost += cost
        total_tokens += (p or 0) + (c or 0)
        items.append({"tier": tier, "model": model, "prompt_tokens": p or 0, "completion_tokens": c or 0,
                      "calls": n, "cost": round(cost, 4)})
    return {"month": start.strftime("%Y-%m"), "items": items, "total_tokens": total_tokens,
            "total_cost": round(total_cost, 4), "currency": cfg.get("currency", "USD")}


@router.get("/profile")
def profile(s: Session = Depends(get_session)):
    return gamification.profile_summary(s)


@router.get("/heatmap")
def heatmap(days: int = 365, s: Session = Depends(get_session)):
    return gamification.heatmap(s, min(max(days, 7), 730))


@router.get("/events")
def events(limit: int = 30, s: Session = Depends(get_session)):
    rows = s.exec(select(LearningEvent).order_by(LearningEvent.created_at.desc()).limit(min(limit, 200))).all()
    return [r.model_dump() | {"created_at": r.created_at.isoformat()} for r in rows]
