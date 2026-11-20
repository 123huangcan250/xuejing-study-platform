"""远程访问的访问控制：未设密码拒绝远程、设了密码所有访问都要登录、限速、会话失效、同源检查。"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="alp-test-ac-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import access  # noqa: E402

PW = "Remote-pass-93417"
REMOTE = "http://desktop.tail1234.ts.net"


@pytest.fixture
def env():
    from app.db import init_db

    init_db()
    access.disable()
    access.LIMITER.fails.clear()
    access.LIMITER.global_fails.clear()
    with TestClient(app, base_url="http://localhost") as local, TestClient(app, base_url=REMOTE) as remote:
        yield local, remote
    access.disable()


def test_unconfigured_only_loopback_allowed(env):
    local, remote = env
    assert local.get("/api/courses").status_code == 200
    r = remote.get("/api/courses")
    assert r.status_code == 403 and r.json()["code"] == "remote_disabled"
    # 伪造 Host 头为 localhost 也只在“没有任何远程入口”的前提下才安全——这里至少验证 127.0.0.1 / ::1 的写法都算本机
    for host in ("127.0.0.1:3400", "localhost:8400", "[::1]:3400"):
        assert local.get("/api/courses", headers={"host": host}).status_code == 200, host
    assert remote.get("/api/health").status_code == 200  # 健康检查不受限
    st = remote.get("/api/auth/status").json()
    assert st["required"] is False and st["local_only"] is True and st["blocked"] is True
    assert local.get("/api/auth/status").json()["blocked"] is False
    assert remote.post("/api/auth/login", json={"password": "x" * 12}).json()["code"] == "not_configured"


def test_proxy_forwarded_host_decides(env):
    """经前端代理时 Host 会被改成后端地址，必须以代理转发的 x-alp-host（浏览器看到的 Host）为准。"""
    local, _ = env
    assert local.get("/api/courses", headers={"x-alp-host": "localhost:3400"}).status_code == 200
    r = local.get("/api/courses", headers={"x-alp-host": "desktop.tail1234.ts.net"})
    assert r.status_code == 403 and r.json()["code"] == "remote_disabled"
    assert local.get("/api/auth/status", headers={"x-alp-host": "desktop.tail1234.ts.net"}).json()["blocked"] is True
    # Origin 同源检查也以浏览器看到的 Host 为准
    access.set_password(PW)
    local.post("/api/auth/login", json={"password": PW})
    h = {"x-alp-host": "desktop.tail1234.ts.net"}
    assert local.post("/api/courses", json={"name": "x"}, headers={**h, "origin": "https://desktop.tail1234.ts.net"}).status_code == 200
    assert local.post("/api/courses", json={"name": "x"}, headers={**h, "origin": "https://evil.example.com"}).status_code == 403


def test_password_required_everywhere_and_login_flow(env):
    local, remote = env
    access.set_password(PW)
    # 设了密码：本机也要登录（不靠来源或 Host 判断）
    for c in (local, remote):
        r = c.get("/api/courses")
        assert r.status_code == 401 and r.json()["code"] == "auth_required"
        assert c.get("/api/documents/1/file").status_code == 401
        assert c.get("/api/auth/status").json() == {"required": True, "authenticated": False, "remote_enabled": True, "local_only": False, "blocked": False}
    assert remote.get("/api/health").status_code == 200

    r = remote.post("/api/auth/login", json={"password": "wrong-password"})
    assert r.status_code == 401 and r.json()["code"] == "wrong_password"
    assert remote.get("/api/courses").status_code == 401
    r = remote.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 200
    sc = r.headers["set-cookie"]
    assert "alp_session=" in sc and "HttpOnly" in sc and "SameSite=lax" in sc and PW not in sc
    assert remote.get("/api/courses").status_code == 200
    assert remote.get("/api/auth/status").json()["authenticated"] is True
    assert local.get("/api/courses").status_code == 401  # 登录状态只属于这个浏览器

    remote.post("/api/auth/logout")
    assert remote.get("/api/courses").status_code == 401


def test_https_cookie_is_secure(env):
    _, remote = env
    access.set_password(PW)
    r = remote.post("/api/auth/login", json={"password": PW}, headers={"x-forwarded-proto": "https"})
    assert "Secure" in r.headers["set-cookie"]


def test_rate_limit_lockout_and_forwarded_for_last_hop(env):
    _, remote = env
    access.set_password(PW)
    # 伪造的前缀不影响限速：以代理追加的最后一跳为准
    for i in range(5):
        r = remote.post("/api/auth/login", json={"password": f"bad-{i}-password"}, headers={"x-forwarded-for": f"1.2.3.{i}, 100.64.0.9"})
        assert r.status_code == 401
    r = remote.post("/api/auth/login", json={"password": PW}, headers={"x-forwarded-for": "9.9.9.9, 100.64.0.9"})
    assert r.status_code == 429 and "分钟" in r.json()["detail"]  # 同一来源被锁，连正确密码也先不收
    # 另一个来源不受影响
    assert remote.post("/api/auth/login", json={"password": PW}, headers={"x-forwarded-for": "100.64.0.77"}).status_code == 200
    # 锁定时间递增：第 6 次失败是 10 分钟
    lim = access.Limiter()
    for _ in range(5):
        lim.failed("k", 1000.0)
    assert lim.locked_for("k", 1000.0) == 300 + 1
    lim.failed("k", 2000.0)
    assert lim.locked_for("k", 2000.0) == 600 + 1
    lim.ok("k")
    assert lim.locked_for("k", 2000.0) == 0


def _status_with_cookie(token: str) -> int:
    """用指定的 Cookie 值直接请求（不带任何已保存的 Cookie），返回状态码。"""
    with TestClient(app, base_url=REMOTE) as c:
        return c.get("/api/courses", headers={"cookie": f"alp_session={token}"}).status_code


def test_session_invalidation_and_tampering(env):
    _, remote = env
    access.set_password(PW)
    remote.post("/api/auth/login", json={"password": PW})
    good = remote.cookies.get("alp_session")
    assert _status_with_cookie(good) == 200
    # 篡改：改过期时间、换签名、乱写
    payload, sig = good.rsplit(".", 1)
    parts = payload.split(".")
    for bad in (f"v1.{int(parts[1]) + 99999}.{parts[2]}.{parts[3]}.{sig}", f"{payload}.{'0' * 64}", "garbage", f"v1.1.1.x.{sig}"):
        assert _status_with_cookie(bad) == 401, bad
    assert access.verify_token(good, now=time.time() + 31 * 86400) is False  # 过期
    # 登出所有设备 / 改密码：旧 Cookie 立即失效
    access.logout_all()
    assert _status_with_cookie(good) == 401
    remote.post("/api/auth/login", json={"password": PW})
    old = remote.cookies.get("alp_session")
    assert _status_with_cookie(old) == 200
    access.set_password(PW + "-changed")
    assert _status_with_cookie(old) == 401
    assert access.check_password(PW + "-changed") and not access.check_password(PW)


def test_origin_check_on_unsafe_methods(env):
    _, remote = env
    access.set_password(PW)
    remote.post("/api/auth/login", json={"password": PW})
    body = {"name": "x"}
    assert remote.post("/api/courses", json=body, headers={"origin": "https://evil.example.com"}).status_code == 403
    assert remote.post("/api/courses", json=body, headers={"origin": REMOTE}).status_code == 200
    assert remote.post("/api/courses", json=body).status_code == 200  # 没有 Origin（非浏览器）按 Cookie 判断


def test_password_storage_and_policy(env):
    access.set_password(PW)
    with pytest.raises(ValueError):
        access.set_password("short")
    from app.db import DATA_DIR  # 用应用实际使用的目录（各测试模块设置的环境变量会互相覆盖）

    for f in DATA_DIR.rglob("*"):
        if f.is_file() and f.stat().st_size < 50_000_000 and f.suffix != ".dpapi":
            assert PW.encode() not in f.read_bytes(), f
    raw = (access.SECURE_DIR / "access.dpapi").read_bytes()
    assert PW.encode() not in raw
    # 两次设置同一个密码，哈希和盐都不同
    h1 = access._record()["hash"]
    access.set_password(PW)
    assert access._record()["hash"] != h1
