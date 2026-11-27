"""考前冲刺计划接口。考试日期和每日学习时间通过 PATCH /api/courses/{id} 设置。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..models import Course
from ..services import gamification, sprint

router = APIRouter(prefix="/api", tags=["sprint"])


@router.get("/courses/{cid}/sprint")
def get_sprint(cid: int, s: Session = Depends(get_session)):
    if not s.get(Course, cid):
        raise HTTPException(404, "课程不存在")
    return sprint.build_plan(s, cid)


class DoneIn(BaseModel):
    key: str
    done: bool = True


@router.post("/courses/{cid}/sprint/done")
def mark_done(cid: int, body: DoneIn, s: Session = Depends(get_session)):
    if not s.get(Course, cid):
        raise HTTPException(404, "课程不存在")
    if not re.fullmatch(r"(learn|review|cards|wrong|mock):[\w\-]{1,20}", body.key):
        raise HTTPException(400, "任务标识不合法")
    fresh = sprint.set_done(s, cid, body.key, body.done)
    reward = gamification.emit(s, "SPRINT_TASK_DONE", cid, {"key": body.key}) if fresh else None
    return {"ok": True, "reward": reward}
