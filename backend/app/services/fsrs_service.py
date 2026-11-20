"""FSRS 间隔重复调度（py-fsrs）。"""
from __future__ import annotations

from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler

_scheduler = Scheduler(desired_retention=0.9, enable_fuzzing=True)


def new_card_json() -> tuple[str, datetime]:
    c = Card()
    return c.to_json(), _local(c.due)


def _local(dt: datetime) -> datetime:
    """FSRS 内部用 UTC，数据库统一存本地朴素时间，方便和“今天”比较。"""
    return dt.astimezone().replace(tzinfo=None)


def review(card_json: str, rating: int) -> tuple[str, datetime, int]:
    card = Card.from_json(card_json) if card_json else Card()
    card, _ = _scheduler.review_card(card, Rating(rating), review_datetime=datetime.now(timezone.utc))
    return card.to_json(), _local(card.due), int(card.state)


def preview(card_json: str) -> dict[int, str]:
    """给四个评分按钮显示“下次复习间隔”。"""
    now = datetime.now(timezone.utc)
    out = {}
    for r in Rating:
        card = Card.from_json(card_json) if card_json else Card()
        nxt, _ = _scheduler.review_card(card, r, review_datetime=now)
        out[int(r)] = _humanize((nxt.due - now).total_seconds())
    return out


def _humanize(sec: float) -> str:
    if sec < 3600:
        return f"{max(1, round(sec / 60))} 分钟"
    if sec < 86400:
        return f"{round(sec / 3600)} 小时"
    days = sec / 86400
    if days < 30:
        return f"{round(days)} 天"
    if days < 365:
        return f"{round(days / 30, 1)} 个月"
    return f"{round(days / 365, 1)} 年"
