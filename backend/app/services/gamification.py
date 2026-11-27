"""游戏化结算中心：所有学习模块只上报标准事件，由这里统一结算 XP / 金币 / 连胜 / 成就。

防“伪游戏化”：
- 只有主动行为（提问、复习到期卡、做卡、上传资料）产生 XP；
- 同类事件有每日 XP 上限，刷同一种动作收益递减为 0；
- 断签不扣分，只是连胜归零（后续阶段加入冻结卡）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from sqlalchemy import func
from sqlmodel import Session, select

from ..models import (AchievementUnlock, CanvasNode, DailyStat, Flashcard,
                      LearningEvent, Profile, ReviewLog)

DAILY_GOAL_XP = 20  # 极易启动：约等于复习 7~10 张到期卡，或在画布上认真问 3~4 个问题

# 事件类型 → (基础 XP, 每日该类 XP 上限)
EVENT_RULES: dict[str, tuple[int, int]] = {
    "DOC_UPLOADED": (10, 30),
    "HIGHLIGHT_ASKED": (6, 60),
    "NODE_ASKED": (4, 60),
    "TERM_EXPLORED": (3, 30),
    "MERGE_ASKED": (8, 40),
    "NOTE_CREATED": (2, 10),
    "FLASHCARD_CREATED": (2, 30),
    "FLASHCARD_REVIEWED": (3, 150),
    "SPRINT_TASK_DONE": (5, 40),
    "QUESTION_ANSWERED": (4, 120),  # 实际 XP 由题库按“首次做对 / 难度”算好传入，这里只管每日上限
}
QUESTION_EVENTS = {"HIGHLIGHT_ASKED", "NODE_ASKED", "TERM_EXPLORED", "MERGE_ASKED"}


def xp_for_level(level: int) -> int:
    """从 level 升到 level+1 需要的 XP。"""
    return 100 + 50 * (level - 1)


def level_info(total_xp: int) -> dict:
    level, rest = 1, total_xp
    while rest >= xp_for_level(level):
        rest -= xp_for_level(level)
        level += 1
    return {"level": level, "xp_in_level": rest, "xp_to_next": xp_for_level(level)}


def today_str() -> str:
    return date.today().isoformat()


# ---------------- 成就 ----------------
@dataclass
class Achievement:
    key: str
    name: str
    desc: str
    icon: str
    category: str
    check: Callable[[Session], bool]


def _count(s: Session, stmt) -> int:
    return s.exec(stmt).one() or 0


def _max_streak_now(s: Session) -> int:
    return streak_info(s)["current"]


ACHIEVEMENTS: list[Achievement] = [
    Achievement("first_upload", "学有所藏", "上传第一份课程资料", "📚", "探索",
                lambda s: _count(s, select(func.count()).select_from(LearningEvent).where(LearningEvent.type == "DOC_UPLOADED")) >= 1),
    Achievement("first_fog", "初次破雾", "第一次对原文划词分叉提问", "🌫️", "探索",
                lambda s: _count(s, select(func.count()).select_from(LearningEvent).where(LearningEvent.type == "HIGHLIGHT_ASKED")) >= 1),
    Achievement("deep_dive", "刨根问底", "单条分支连续追问到第 5 层", "⛏️", "探索",
                lambda s: _count(s, select(func.max(CanvasNode.depth)).where(CanvasNode.type == "qa")) >= 5),
    Achievement("cross_link", "跨域联结", "把 3 个及以上节点连到同一个问题", "🔗", "探索",
                lambda s: _has_merge_of(s, 3)),
    Achievement("curious_50", "好奇心爆棚", "累计提出 50 个问题", "💡", "探索",
                lambda s: _count(s, select(func.count()).select_from(LearningEvent).where(LearningEvent.type.in_(QUESTION_EVENTS))) >= 50),
    Achievement("card_maker", "闪卡工匠", "累计制作 30 张闪卡", "🃏", "复习",
                lambda s: _count(s, select(func.count()).select_from(Flashcard)) >= 30),
    Achievement("hundred_cards", "百卡斩", "累计复习 100 次闪卡", "⚔️", "复习",
                lambda s: _count(s, select(func.count()).select_from(ReviewLog)) >= 100),
    Achievement("first_quiz", "初试牛刀", "第一次做题库里的题", "📝", "复习",
                lambda s: _count(s, select(func.count()).select_from(LearningEvent).where(LearningEvent.type == "QUESTION_ANSWERED")) >= 1),
    Achievement("quiz_100", "题海拾贝", "累计作答 100 道题", "🌊", "复习",
                lambda s: _count(s, select(func.count()).select_from(LearningEvent).where(LearningEvent.type == "QUESTION_ANSWERED")) >= 100),
    Achievement("streak_3", "三日热身", "连续学习 3 天", "🔥", "坚持", lambda s: _max_streak_now(s) >= 3),
    Achievement("streak_7", "七日不断电", "连续学习 7 天", "⚡", "坚持", lambda s: _max_streak_now(s) >= 7),
    Achievement("streak_30", "月度常驻", "连续学习 30 天", "🏔️", "坚持", lambda s: _max_streak_now(s) >= 30),
    Achievement("level_5", "笔记学徒", "等级达到 5 级", "🎓", "成长",
                lambda s: level_info((s.get(Profile, 1) or Profile()).xp_total)["level"] >= 5),
]
ACH_BY_KEY = {a.key: a for a in ACHIEVEMENTS}


def _has_merge_of(s: Session, n: int) -> bool:
    rows = s.exec(select(LearningEvent.payload_json).where(LearningEvent.type == "MERGE_ASKED")).all()
    for p in rows:
        try:
            if json.loads(p or "{}").get("parents", 0) >= n:
                return True
        except ValueError:
            pass
    return False


# ---------------- 连胜 ----------------
def streak_info(s: Session) -> dict:
    lit = set(s.exec(select(DailyStat.date).where(DailyStat.xp >= DAILY_GOAL_XP)).all())
    today = date.today()
    cur = 0
    day = today if today.isoformat() in lit else today - timedelta(days=1)  # 今天还没达标不算断
    while day.isoformat() in lit:
        cur += 1
        day -= timedelta(days=1)
    longest, run, prev = 0, 0, None
    for d in sorted(lit):
        dd = date.fromisoformat(d)
        run = run + 1 if prev and (dd - prev).days == 1 else 1
        longest = max(longest, run)
        prev = dd
    return {"current": cur, "longest": longest, "today_lit": today.isoformat() in lit}


# ---------------- 事件结算 ----------------
def emit(s: Session, event_type: str, course_id: int | None = None, payload: dict | None = None,
         xp_override: int | None = None) -> dict:
    """记录事件并结算奖励，返回给前端用于弹窗/动效。调用方负责 commit 之前的业务数据。"""
    base, cap = EVENT_RULES.get(event_type, (0, 0))
    xp = base if xp_override is None else xp_override
    today = today_str()
    stat = s.get(DailyStat, today) or DailyStat(date=today)

    if cap:
        start = datetime.combine(date.today(), datetime.min.time())
        used = _count(s, select(func.coalesce(func.sum(LearningEvent.xp), 0)).where(
            LearningEvent.type == event_type, LearningEvent.created_at >= start))
        xp = max(0, min(xp, cap - used))
    coins = xp // 2

    profile = s.get(Profile, 1) or Profile(id=1)
    before_level = level_info(profile.xp_total)["level"]
    was_lit = stat.xp >= DAILY_GOAL_XP

    profile.xp_total += xp
    profile.coins += coins
    stat.xp += xp
    stat.events += 1
    if event_type == "FLASHCARD_REVIEWED":
        stat.reviews += 1
    if event_type in QUESTION_EVENTS:
        stat.questions += 1
    s.add(LearningEvent(type=event_type, course_id=course_id,
                        payload_json=json.dumps(payload or {}, ensure_ascii=False), xp=xp, coins=coins))
    s.add(profile)
    s.add(stat)
    s.flush()

    unlocked = set(s.exec(select(AchievementUnlock.key)).all())
    new_ach = []
    for a in ACHIEVEMENTS:
        if a.key not in unlocked and a.check(s):
            s.add(AchievementUnlock(key=a.key))
            new_ach.append({"key": a.key, "name": a.name, "desc": a.desc, "icon": a.icon})
    s.commit()

    lv = level_info(profile.xp_total)
    return {
        "event": event_type,
        "xp": xp,
        "coins": coins,
        "total_xp": profile.xp_total,
        **lv,
        "level_up": lv["level"] > before_level,
        "today_xp": stat.xp,
        "daily_goal": DAILY_GOAL_XP,
        "streak_lit_now": (not was_lit) and stat.xp >= DAILY_GOAL_XP,
        "streak": streak_info(s)["current"],
        "new_achievements": new_ach,
    }


def profile_summary(s: Session) -> dict:
    profile = s.get(Profile, 1) or Profile(id=1)
    stat = s.get(DailyStat, today_str()) or DailyStat(date=today_str())
    unlocked = {r.key: r.unlocked_at for r in s.exec(select(AchievementUnlock)).all()}
    return {
        "total_xp": profile.xp_total,
        "coins": profile.coins,
        **level_info(profile.xp_total),
        "today": {"xp": stat.xp, "reviews": stat.reviews, "questions": stat.questions, "goal": DAILY_GOAL_XP},
        "streak": streak_info(s),
        "achievements": [
            {"key": a.key, "name": a.name, "desc": a.desc, "icon": a.icon, "category": a.category,
             "unlocked": a.key in unlocked,
             "unlocked_at": unlocked[a.key].isoformat() if a.key in unlocked else None}
            for a in ACHIEVEMENTS
        ],
    }


def heatmap(s: Session, days: int = 365) -> list[dict]:
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = s.exec(select(DailyStat).where(DailyStat.date >= start)).all()
    return [{"date": r.date, "xp": r.xp, "reviews": r.reviews, "questions": r.questions} for r in rows]
