"""考前冲刺计划：把「真题考点频率 × 重要度 × 还没掌握的程度」排成优先级，按剩余天数和每天可用时间排进日程。

- 优先级（有真题数据时）= 0.4 × 考频 + 0.2 × 重要度 + 0.4 × 薄弱度；没有真题时 = 0.4 × 重要度 + 0.6 × 薄弱度。
  薄弱度 = 1 − 掌握度（还没练过按 0.65 算，不假装会）；已经稳稳掌握的（练过 ≥3 次、掌握度 ≥85%）优先级打 4 折。
- 学习任务按优先级从高到低排；每天先留 10 分钟过闪卡；高优先级的知识点在学完 2 天后安排一次回顾（间隔重复）。
- 最后 1~2 天不再学新内容：错题、限时仿真卷、闪卡。
- 计划不存死：每次打开都按最新的掌握度重排，只有“已完成”的状态单独保存，所以做了题、掌握度上来了，计划会自己调整。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from ..models import Course, ExamQuestion, Flashcard, KnowledgeNode, Paper, SprintDone
from . import questions as qsvc
from .knowledge import exam_counts

CARDS_MIN = 10
REVIEW_MIN = 10
REVIEW_TOP = 0.35  # 优先级前 35% 的知识点安排回顾


def priorities(s: Session, course_id: int) -> list[dict]:
    nodes = {n.id: n for n in s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == course_id)).all()}
    freq = exam_counts(s, course_id)
    mast = qsvc.mastery_by_node(s, course_id)
    top = max(freq.values(), default=0)
    out = []
    for n in nodes.values():
        if n.level != 3:
            continue
        sec = nodes.get(n.parent_id)
        ch = nodes.get(sec.parent_id) if sec else None
        m = mast.get(n.id)
        mastery = m["mastery"] if m else None
        weak = 1 - (mastery if mastery is not None else 0.35)
        f = freq.get(n.id, 0) / top if top else 0.0
        imp = ((n.importance or 2) - 1) / 2
        pr = 0.4 * f + 0.2 * imp + 0.4 * weak if top else 0.4 * imp + 0.6 * weak
        solid = bool(m and m["attempts"] >= 3 and mastery is not None and mastery >= 0.85)
        if solid:
            pr *= 0.4
        reasons = []
        if freq.get(n.id):
            reasons.append(f"真题考过 {freq[n.id]} 次")
        if (n.importance or 2) >= 3:
            reasons.append("重点")
        reasons.append("还没练过" if mastery is None else f"掌握度 {round(mastery * 100)}%")
        out.append({"node_id": n.id, "title": n.title, "chapter": ch.title if ch else "", "priority": round(pr, 3), "mastery": mastery,
                    "exam_count": freq.get(n.id, 0), "minutes": max(10, min(35, 5 * round((10 + 25 * pr) / 5))),
                    "reason": "，".join(reasons), "solid": solid})
    out.sort(key=lambda x: (-x["priority"], x["node_id"]))
    return out


def _task(kind: str, key: str, title: str, minutes: int, done: set[str], **kw) -> dict:
    return {"kind": kind, "key": key, "title": title, "minutes": minutes, "done": key in done, **kw}


def build_plan(s: Session, course_id: int, today: date | None = None) -> dict:
    c = s.get(Course, course_id)
    today = today or date.today()
    base = {"configured": bool(c and c.exam_date), "exam_date": c.exam_date if c else "", "daily_minutes": c.sprint_minutes if c else 90}
    if not base["configured"]:
        return base
    exam = date.fromisoformat(c.exam_date)
    days_left = (exam - today).days
    base["days_left"] = days_left
    if days_left < 0:
        return base | {"passed": True, "days": []}
    done = {d.key for d in s.exec(select(SprintDone).where(SprintDone.course_id == course_id)).all()}
    pri = priorities(s, course_id)
    daily = c.sprint_minutes
    study = [today + timedelta(days=i) for i in range(days_left)]  # 考试当天不排学习
    final_n = 0 if len(study) <= 3 else 1 if len(study) < 10 else 2
    learn_days = study[: len(study) - final_n]
    final_days = study[len(study) - final_n:]
    has_exam = s.exec(select(ExamQuestion.id).where(ExamQuestion.course_id == course_id)).first() is not None
    n_cards = s.exec(select(Flashcard.id).where(Flashcard.course_id == course_id)).first() is not None
    has_paper = s.exec(select(Paper.id).where(Paper.course_id == course_id)).first() is not None

    days: dict[date, list[dict]] = {d: [] for d in study}
    used: dict[date, int] = {d: 0 for d in study}

    def add(d: date, t: dict):
        days[d].append(t)
        used[d] += t["minutes"]

    # 每天先留闪卡时间
    if n_cards:
        for d in study:
            add(d, _task("cards", f"cards:{d.isoformat()}", "过一遍到期闪卡", CARDS_MIN, done))

    # 第一遍：按优先级学新知识点
    todo = [p for p in pri if f"learn:{p['node_id']}" not in done]
    where: dict[int, date] = {}
    di = 0
    for p in todo:
        while di < len(learn_days) and used[learn_days[di]] + p["minutes"] > daily and any(t["kind"] == "learn" for t in days[learn_days[di]]):
            di += 1
        if di >= len(learn_days):
            break
        d = learn_days[di]
        add(d, _task("learn", f"learn:{p['node_id']}", p["title"], p["minutes"], done, node_id=p["node_id"], chapter=p["chapter"],
                     reason=p["reason"], priority=p["priority"]))
        where[p["node_id"]] = d
    scheduled = len(where)
    skipped = [p for p in todo if p["node_id"] not in where]

    # 第二遍：高优先级知识点学完 2 天后回顾一次（挑当天最空的一天）
    cutoff = max(1, round(len(pri) * REVIEW_TOP))
    for p in pri[:cutoff]:
        start = (where.get(p["node_id"]) or today) + timedelta(days=2)
        cand = [d for d in study if d >= start and used[d] + REVIEW_MIN <= daily]
        if not cand:
            continue
        d = min(cand, key=lambda x: (used[x], x))
        add(d, _task("review", f"review:{p['node_id']}", f"回顾：{p['title']}", REVIEW_MIN, done, node_id=p["node_id"], reason="间隔回顾"))

    # 最后几天：错题、仿真卷
    for i, d in enumerate(final_days):
        add(d, _task("wrong", f"wrong:{d.isoformat()}", "把做错的题再过一遍", 40, done))
        if has_exam or has_paper:
            first = i == 0
            add(d, _task("mock", f"mock:{d.isoformat()}", "限时做一份仿真卷" if first else "回看仿真卷里错的和不确定的", min(90, max(30, daily - 50)) if first else 40, done))
    if not final_days and study:  # 时间太紧：最后一个学习日也留点时间给错题
        add(study[-1], _task("wrong", f"wrong:{study[-1].isoformat()}", "把做错的题再过一遍", 20, done))

    out_days = []
    for d in study:
        label = "今天" if d == today else "明天" if d == today + timedelta(days=1) else f"{d.month}/{d.day}"
        out_days.append({"date": d.isoformat(), "label": label, "final": d in final_days, "minutes": used[d], "tasks": days[d]})
    total_learn = len(todo) + len([p for p in pri if f"learn:{p['node_id']}" in done])
    return base | {
        "passed": False, "days": out_days, "study_days": len(study), "nodes_total": len(pri),
        "nodes_done": total_learn - len(todo), "scheduled": scheduled,
        "coverage": round((total_learn - len(todo) + scheduled) / total_learn, 3) if total_learn else None,
        "skipped": [{"node_id": p["node_id"], "title": p["title"], "reason": p["reason"]} for p in skipped[:10]], "skipped_count": len(skipped),
        "no_knowledge": not pri,
        "tips": _tips(days_left, len(skipped), has_exam, bool(pri)),
    }


def _tips(days_left: int, skipped: int, has_exam: bool, has_nodes: bool) -> list[str]:
    tips = []
    if not has_nodes:
        tips.append("还没有知识点：先在「知识梳理」里对课件做梳理，冲刺计划才排得出来。")
    if not has_exam and has_nodes:
        tips.append("还没有分析过真题：上传往年试卷并分析后，计划会优先排真题考得多的知识点。")
    if skipped:
        tips.append(f"时间不够，还有 {skipped} 个优先级较低的知识点排不进去——可以加每天的学习时间，或只在最后扫一眼它们的概要。")
    if days_left == 0:
        tips.append("考试就在今天，不再排新任务。祝顺利！")
    return tips


def set_done(s: Session, course_id: int, key: str, done: bool) -> bool:
    """标记完成 / 取消。返回这次是不是新完成（用来发奖励）。"""
    row = s.exec(select(SprintDone).where(SprintDone.course_id == course_id, SprintDone.key == key)).first()
    if done and not row:
        s.add(SprintDone(course_id=course_id, key=key))
        s.commit()
        return True
    if not done and row:
        s.delete(row)
        s.commit()
    return False


_ = (json, datetime)
