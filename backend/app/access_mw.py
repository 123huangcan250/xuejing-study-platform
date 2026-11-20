"""访问控制中间件（纯 ASGI，不会干扰 SSE 流式响应）。规则见 services/access.py 顶部说明。"""
from __future__ import annotations

import json
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from .services import access

OPEN_PREFIXES = ("/api/auth/",)
OPEN_PATHS = {"/api/health"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


async def _deny(send, status: int, detail: str, code: str):
    body = json.dumps({"detail": detail, "code": code}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                                                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class AccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        host = access.effective_host(headers)

        # 浏览器带 Origin 的写请求必须同源，防跨站请求
        origin = headers.get("origin")
        if origin and scope["method"] in UNSAFE and urlparse(origin).netloc.lower() != host.lower():
            return await _deny(send, 403, "请求来源不一致，已拒绝", "bad_origin")

        if path in OPEN_PATHS or any(path.startswith(p) for p in OPEN_PREFIXES):
            return await self.app(scope, receive, send)

        if access.configured():
            cookies = SimpleCookie(headers.get("cookie", ""))
            token = cookies[access.COOKIE].value if access.COOKIE in cookies else None
            if not access.verify_token(token):
                return await _deny(send, 401, "需要登录", "auth_required")
        elif not access.host_is_loopback(host):
            return await _deny(send, 403, "远程访问未启用：请先在运行本程序的电脑上设置访问密码（scripts\\remote.ps1 会引导你）", "remote_disabled")
        return await self.app(scope, receive, send)
