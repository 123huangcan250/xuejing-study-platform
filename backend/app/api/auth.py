"""登录 / 退出 / 登录状态。密码只能在本机用命令行设置，没有任何接口可以设置或修改它。"""
from __future__ import annotations

from http.cookies import SimpleCookie

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from ..services import access

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_key(req: Request) -> str:
    """限速用的来源标识：经过代理时取代理追加的最后一跳（前面的可能是伪造的）。"""
    xff = req.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return req.client.host if req.client else "unknown"


def _authenticated(req: Request) -> bool:
    c = SimpleCookie(req.headers.get("cookie", ""))
    return access.verify_token(c[access.COOKIE].value if access.COOKIE in c else None)


@router.get("/status")
def status(req: Request):
    required = access.configured()
    # 没设密码却不是从本机访问：后端会拒绝一切请求，前端据此显示说明而不是一堆报错
    blocked = (not required) and not access.host_is_loopback(access.effective_host(req.headers))
    return {"required": required, "authenticated": _authenticated(req) if required else not blocked,
            "remote_enabled": required, "local_only": not required, "blocked": blocked}


class LoginIn(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginIn, req: Request, resp: Response):
    if not access.configured():
        resp.status_code = 400
        return {"detail": "还没有设置访问密码。请在运行本程序的电脑上运行 scripts\\remote.ps1 或 access_cli set。", "code": "not_configured"}
    key = _client_key(req)
    wait = access.LIMITER.locked_for(key)
    if wait:
        resp.status_code = 429
        return {"detail": f"尝试次数过多，请 {wait // 60 + 1} 分钟后再试", "code": "rate_limited"}
    if not access.check_password(body.password):
        access.LIMITER.failed(key)
        resp.status_code = 401
        return {"detail": "密码不正确", "code": "wrong_password"}
    access.LIMITER.ok(key)
    secure = req.url.scheme == "https" or req.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(access.COOKIE, access.make_token(), max_age=access.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/")
    return {"ok": True}


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(access.COOKIE, path="/")
    return {"ok": True}
