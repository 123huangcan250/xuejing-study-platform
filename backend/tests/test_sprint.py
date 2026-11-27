"""考前冲刺计划：优先级、排程、完成状态、时间不够时的取舍。"""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="alp-test-sp-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Attempt, Flashcard, Question  # noqa: E402
from app.services import sprint  # noqa: E402
from test_questions import seed_course  # noqa: E402

TODAY = date(2026, 12, 1)


@pytest.fixture()
def client():
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def plan(cid, exam: date, minutes=90, today=TODAY):
    with Session(engine) as s:
        from app.models import Course

        c = s.get(Course, cid)
        c.exam_date, c.sprint_minutes = exam.isoformat(), minutes
        s.add(c)
        s.commit()
        return sprint.build_plan(s, cid, today)


def test_not_configured_and_validation(client):
    ids = seed_course(client)
    cid = ids["cid"]
    assert client.get(f"/api/courses/{cid}/sprint").json() == {"configured": False, "exam_date": "", "daily_minutes": 90}
    assert client.patch(f"/api/courses/{cid}", json={"exam_date": "明天"}).status_code == 400
    r = client.patch(f"/api/courses/{cid}", json={"exam_date": "2099-01-02", "sprint_minutes": 5}).json()
    assert r["exam_date"] == "2099-01-02" and r["sprint_minutes"] == 20  # 时间下限 20
    assert client.patch(f"/api/courses/{cid}", json={"exam_date": ""}).json()["exam_date"] == ""


def test_plan_orders_by_priority_and_reserves_final_days(client):
    ids = seed_course(client)
    cid = ids["cid"]
    p = plan(cid, TODAY + timedelta(days=12))
    assert p["days_left"] == 12 and p["study_days"] == 12 and len(p["days"]) == 12
    learn = [t for d in p["days"] for t in d["tasks"] if t["kind"] == "learn"]
    assert [t["node_id"] for t in learn] == [ids["k1"], ids["k2"]]  # k1 考了 2 次 + 重点，排在前面
    assert "真题考过 2 次" in learn[0]["reason"] and learn[0]["priority"] > learn[1]["priority"]
    assert [d["final"] for d in p["days"]].count(True) == 2 and p["days"][-1]["final"]
    final_kinds = {t["kind"] for t in p["days"][-1]["tasks"]}
    assert {"wrong", "mock"} <= final_kinds and "learn" not in final_kinds
    assert not any(t["kind"] == "learn" for d in p["days"] if d["final"] for t in d["tasks"])
    reviews = [(d["date"], t) for d in p["days"] for t in d["tasks"] if t["kind"] == "review"]
    first_learn_day = next(d["date"] for d in p["days"] if any(t["kind"] == "learn" and t["node_id"] == ids["k1"] for t in d["tasks"]))
    k1_review = [d for d, t in reviews if t["node_id"] == ids["k1"]]
    assert k1_review and date.fromisoformat(k1_review[0]) >= date.fromisoformat(first_learn_day) + timedelta(days=2)
    assert p["coverage"] == 1.0 and p["skipped_count"] == 0
    assert all(d["minutes"] <= 90 for d in p["days"])
    assert p["days"][0]["label"] == "今天" and p["days"][1]["label"] == "明天"


def test_done_state_and_xp(client):
    ids = seed_course(client)
    cid = ids["cid"]
    plan(cid, TODAY + timedelta(days=8))
    client.patch(f"/api/courses/{cid}", json={"exam_date": (date.today() + timedelta(days=8)).isoformat()})
    r = client.post(f"/api/courses/{cid}/sprint/done", json={"key": f"learn:{ids['k1']}"}).json()
    assert r["reward"]["xp"] == 5
    assert client.post(f"/api/courses/{cid}/sprint/done", json={"key": f"learn:{ids['k1']}"}).json()["reward"] is None  # 重复点不再给
    live = client.get(f"/api/courses/{cid}/sprint").json()
    learn = [t for d in live["days"] for t in d["tasks"] if t["kind"] == "learn"]
    assert [t["node_id"] for t in learn] == [ids["k2"]] and live["nodes_done"] == 1  # 学过的不再重复排
    assert client.post(f"/api/courses/{cid}/sprint/done", json={"key": "../etc"}).status_code == 400
    client.post(f"/api/courses/{cid}/sprint/done", json={"key": f"learn:{ids['k1']}", "done": False})
    live = client.get(f"/api/courses/{cid}/sprint").json()
    assert live["nodes_done"] == 0


def test_mastery_lowers_priority_and_tight_time(client):
    ids = seed_course(client)
    cid = ids["cid"]
    before = {p["node_id"]: p for p in _pri(cid)}
    with Session(engine) as s:
        q = Question(course_id=cid, node_id=ids["k1"], qtype="judge", stem="一道判断题题干足够长", answer="对")
        s.add(q)
        s.flush()
        for _ in range(4):
            s.add(Attempt(question_id=q.id, course_id=cid, node_id=ids["k1"], score=1.0))
        s.commit()
    after = {p["node_id"]: p for p in _pri(cid)}
    assert after[ids["k1"]]["solid"] and after[ids["k1"]]["priority"] < before[ids["k1"]]["priority"] * 0.5
    assert after[ids["k2"]]["priority"] == before[ids["k2"]]["priority"]

    # 时间不够：只剩 1 天、每天 20 分钟，只能排下一部分，其余如实告知
    with Session(engine) as s:
        s.add(Flashcard(course_id=cid, front="f", back="b"))
        s.commit()
    p = plan(cid, TODAY + timedelta(days=1), minutes=20)
    assert p["study_days"] == 1 and p["skipped_count"] >= 1 and any("排不进去" in t for t in p["tips"])
    assert p["coverage"] < 1
    assert all(sum(t["minutes"] for t in d["tasks"] if t["kind"] == "learn") <= 20 or len([t for t in d["tasks"] if t["kind"] == "learn"]) == 1 for d in p["days"])


def test_passed_and_today(client):
    ids = seed_course(client)
    cid = ids["cid"]
    assert plan(cid, TODAY - timedelta(days=1))["passed"] is True
    p = plan(cid, TODAY)
    assert p["days_left"] == 0 and p["days"] == [] and any("今天" in t for t in p["tips"])


def _pri(cid):
    with Session(engine) as s:
        return sprint.priorities(s, cid)
