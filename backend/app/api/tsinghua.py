"""清华大模型服务：登录状态、手动更新、打开登录窗口、模型可用性检测。

注意：这里没有“提交账号密码”的接口——账号密码只能在本机终端用 scripts/tsinghua-setup.ps1 输入。
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.tsinghua import client as th
from ..services.tsinghua.manager import TokenUnavailable
from ..services.tsinghua.runtime import get_manager

router = APIRouter(prefix="/api/tsinghua", tags=["tsinghua"])


@router.get("/status")
def status():
    return get_manager().public_status() | {"models": th.MODELS, "legacy_unavailable": th.LEGACY_UNAVAILABLE}


class RefreshIn(BaseModel):
    interactive: bool = False


@router.post("/refresh")
def refresh(body: RefreshIn):
    """interactive=True 会打开可见的浏览器窗口，由用户本人完成登录 / 验证码 / 二次验证。"""
    m = get_manager()
    m.request_refresh(interactive=body.interactive)
    return m.public_status()


@router.post("/logout")
def logout():
    get_manager().logout()
    return {"ok": True}


@router.delete("/credentials")
def clear_credentials():
    get_manager().clear_credentials()
    return {"ok": True}


class CheckIn(BaseModel):
    models: list[str] | None = None


@router.post("/models/check")
async def check_models(body: CheckIn):
    """对每个模型做一次最小真实流式调用（会消耗少量学校额度）。"""
    m = get_manager()
    try:
        token = m.get_token()
    except TokenUnavailable as e:
        raise HTTPException(400, str(e)) from None
    async def one(model: str) -> dict:
        t0 = time.monotonic()
        try:
            text = await th.chat(token, model, [{"role": "user", "content": "你好，只回复“在线”两个字"}],
                                 max_tokens=600, idle_timeout=60)  # 推理模型要先输出推理过程，太小会在回答前被截断
            return {"model": model, "ok": True, "reply": text.strip()[:40], "ms": int((time.monotonic() - t0) * 1000)}
        except th.UpstreamError as e:
            if e.auth:
                m.report_auth_failure()
            return {"model": model, "ok": False, "error": str(e), "ms": int((time.monotonic() - t0) * 1000)}

    # 并发检测，单个模型最多等 60 秒无数据，整体不会被一个卡住的模型拖住
    results = list(await asyncio.gather(*(one(x) for x in (body.models or th.MODELS)[:6])))
    return {"results": results, "checked_at": time.time()}
