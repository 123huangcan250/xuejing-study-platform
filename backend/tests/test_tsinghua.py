"""清华接入的离线测试：模拟时钟 + 假登录工作器 + httpx MockTransport。不访问真实网络。"""
from __future__ import annotations

import os as _os
import tempfile as _tempfile

# 测试必须用自己的数据目录：否则单独运行时会读写用户真实的数据库（那里可能已设置访问密码、存着真实资料）
_os.environ.setdefault("APP_DATA_DIR", _tempfile.mkdtemp(prefix="alp-test-th-"))

import asyncio
import base64
import json
import logging
import sys
import time

import httpx
import pytest

from app.services.tsinghua import client as th
from app.services.tsinghua import policy, redact
from app.services.tsinghua.auth_worker import AuthResult, drop_site_state
from app.services.tsinghua.manager import CREDENTIALS, META, SESSION, TOKEN, TokenManager, TokenUnavailable
from app.services.tsinghua.secure_store import SecureStore, SecureStoreError, dpapi_protect, dpapi_unprotect

H = 3600.0
PASSWORD = "Pw-Test-9f8e7d!"


def jwt(iat: float, exp: float | None = None, salt: str = "a") -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    body = {"iat": int(iat), "sub": salt}
    if exp:
        body["exp"] = int(exp)
    return f"{enc({'alg': 'HS256'})}.{enc(body)}.sig{salt}xxxxxxxxxxxx"


class FakeClock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def now(self) -> float:
        return self.t

    def advance(self, s: float):
        self.t += s


class FakeWorker:
    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.results: list = []
        self.calls: list[dict] = []
        self.closed = 0

    def queue(self, *items):
        self.results.extend(items)

    def collect(self, *, saved_state, renew, credentials, allow_password, interactive, on_status=None):
        creds = credentials() if allow_password else None
        self.calls.append({"renew": renew, "allow_password": allow_password, "interactive": interactive,
                           "had_creds": bool(creds), "saved": saved_state})
        item = self.results.pop(0) if self.results else AuthResult("error", "no result queued")
        return item(self.clock) if callable(item) else item

    def close(self):
        self.closed += 1

    def reset(self):
        pass


def ok_token(salt: str):
    return lambda clock: AuthResult("ok", "ok", token=jwt(clock.now(), salt=salt), storage_state={"cookies": [{"name": "s"}], "origins": []})


@pytest.fixture
def env(tmp_path):
    clock = FakeClock()
    store = SecureStore(tmp_path / "secure")
    worker = FakeWorker(clock)
    m = TokenManager(store, worker, clock)
    return m, store, worker, clock


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------- 策略 ----------------
def test_expiry_and_refresh_schedule():
    t0 = 1_800_000_000.0
    assert policy.token_expiry(jwt(t0), t0) == t0 + 5 * H
    assert policy.token_expiry(jwt(t0, exp=t0 + 3 * H), t0) == t0 + 3 * H  # JWT 自带 exp 更早
    assert policy.token_expiry("not-a-jwt-token-but-long-enough", t0) == t0 + 5 * H
    # 已用 3 小时的 token：签发于 t0-3h → 到期 t0+2h → 应在 t0+1.5h 更新（签发后 4.5h）
    issued = t0 - 3 * H
    assert policy.refresh_at(policy.token_expiry(jwt(issued), t0)) - t0 == pytest.approx(1.5 * H)
    assert [policy.backoff_delay(i) / 60 for i in range(1, 8)] == [5, 10, 20, 40, 60, 60, 60]


def test_drop_site_state_keeps_sso_cookies():
    st = {"cookies": [{"name": "a", "domain": "madmodel.cs.tsinghua.edu.cn"}, {"name": "b", "domain": ".id.tsinghua.edu.cn"}],
          "origins": [{"origin": "https://madmodel.cs.tsinghua.edu.cn"}, {"origin": "https://id.tsinghua.edu.cn"}]}
    out = drop_site_state(st, "https://madmodel.cs.tsinghua.edu.cn")
    assert [c["name"] for c in out["cookies"]] == ["b"]
    assert [o["origin"] for o in out["origins"]] == ["https://id.tsinghua.edu.cn"]


# ---------------- 加密存储 ----------------
@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 仅 Windows")
def test_secure_store_is_encrypted(tmp_path):
    s = SecureStore(tmp_path)
    s.save(CREDENTIALS, {"username": "2026000000", "password": PASSWORD})
    raw = (tmp_path / "credentials.dpapi").read_bytes()
    assert PASSWORD.encode() not in raw and b"2026000000" not in raw
    assert PASSWORD.encode("utf-16-le") not in raw
    assert s.load(CREDENTIALS)["password"] == PASSWORD
    # 不同的附加熵（=其他应用）无法解密
    with pytest.raises(SecureStoreError):
        dpapi_unprotect(raw, entropy=b"other-app")
    assert dpapi_unprotect(dpapi_protect(b"x", entropy=None), entropy=None) == b"x"
    # 目录里没有任何明文文件
    for f in tmp_path.iterdir():
        assert PASSWORD.encode() not in f.read_bytes()


# ---------------- 启动恢复与排程 ----------------
def test_startup_restores_valid_token_without_browser(env):
    m, store, worker, clock = env
    tok = jwt(clock.now() - 1 * H)  # 1 小时前签发
    store.save(TOKEN, {"token": tok, "obtained_at": clock.now() - H, "expires_at": clock.now() + 4 * H})
    m.restore()
    assert m.status.state == "valid" and m.get_token() == tok
    assert m.plan_next() == pytest.approx(clock.now() + 3.5 * H)  # 签发后 4.5h
    assert worker.calls == []


def test_startup_with_old_token_refreshes_early_or_now(env):
    m, store, worker, clock = env
    # 已用 4 小时 40 分：已过 4.5h 点，应立即更新
    store.save(TOKEN, {"token": jwt(0), "obtained_at": clock.now() - 4.67 * H, "expires_at": clock.now() + 0.33 * H})
    m.restore()
    assert m.plan_next() == clock.now()
    # 已过期：状态为 expired，get_token 拒绝并说明原因
    store.save(TOKEN, {"token": jwt(1), "obtained_at": clock.now() - 6 * H, "expires_at": clock.now() - H})
    m2 = TokenManager(store, worker, clock)
    m2.restore()
    assert m2.status.state == "expired"
    with pytest.raises(TokenUnavailable, match="过期"):
        m2.get_token()


def test_not_configured_does_nothing(env):
    m, *_ = env
    m.restore()
    assert m.plan_next() is None


def test_refresh_success_schedules_4_5h_and_encrypts(env):
    m, store, worker, clock = env
    store.save(CREDENTIALS, {"username": "u", "password": PASSWORD})
    worker.queue(ok_token("first"))
    assert run(m.refresh_once()) is True
    assert m.status.state == "valid" and m.status.consecutive_failures == 0
    assert m.plan_next() == pytest.approx(clock.now() + 4.5 * H)
    assert store.load(TOKEN)["token"] == m.get_token()
    assert store.exists(SESSION)
    assert worker.calls[0]["had_creds"] and worker.calls[0]["renew"] is False

    # 4.5 小时后到点：再次认证得到新 token
    clock.advance(4.5 * H)
    assert m.plan_next() == pytest.approx(clock.now())
    worker.queue(ok_token("second"))
    old = m.get_token()
    assert run(m.refresh_once()) is True and m.get_token() != old
    assert worker.calls[1]["renew"] is True  # 重新认证会丢弃模型站点旧状态


def test_same_token_is_not_success(env):
    m, store, worker, clock = env
    store.save(CREDENTIALS, {"username": "u", "password": PASSWORD})
    worker.queue(ok_token("x"))
    run(m.refresh_once())
    tok, exp = m.get_token(), m.status.expires_at
    clock.advance(4.5 * H)
    worker.queue(lambda c: AuthResult("ok", "ok", token=tok, storage_state={}))
    assert run(m.refresh_once()) is False
    assert m.status.last_outcome == "same_token" and m.status.expires_at == exp  # 没有延长有效期
    assert m.status.state == "valid" and "旧 token 仍在有效期内" in m.status.message
    assert m.plan_next() == pytest.approx(clock.now() + 5 * 60)


def test_failures_backoff_keep_old_token_then_expire(env):
    m, store, worker, clock = env
    store.save(CREDENTIALS, {"username": "u", "password": PASSWORD})
    worker.queue(ok_token("a"))
    run(m.refresh_once())
    clock.advance(4.5 * H)
    delays = []
    for _ in range(4):
        worker.queue(AuthResult("timeout", "网络不可用"))
        assert run(m.refresh_once()) is False
        delays.append(m.plan_next() - clock.now())
        clock.advance(delays[-1])
    assert [d / 60 for d in delays] == [5, 10, 20, 40]
    # 4.5h + 75min > 5h：旧 token 已过期，不再提供，并给出原因
    assert not m.token_valid() and m.status.state == "expired"
    with pytest.raises(TokenUnavailable):
        m.get_token()
    # 恢复后计数清零
    worker.queue(ok_token("b"))
    assert run(m.refresh_once()) is True and m.status.consecutive_failures == 0


def test_bad_credentials_pause_password_until_updated(env):
    m, store, worker, clock = env
    store.save(CREDENTIALS, {"username": "u", "password": "wrong"})
    worker.queue(AuthResult("bad_credentials", "统一身份认证未通过", password_submitted=True))
    run(m.refresh_once())
    assert m.status.state == "credentials_invalid" and m.password_blocked()
    assert store.load(META)["password_blocked"] is True  # 持久化：重启后也不会再提交
    # 退避后再次尝试：不允许再提交密码（只尝试复用浏览器会话）
    clock.advance(m.plan_next() - clock.now())
    worker.queue(AuthResult("needs_user", "需要登录"))
    run(m.refresh_once())
    assert worker.calls[-1]["allow_password"] is False and worker.calls[-1]["had_creds"] is False
    # 用户在本机更新密码后解除暂停
    m.set_credentials("u", PASSWORD)
    assert not m.password_blocked()
    m.request_refresh()
    worker.queue(ok_token("c"))
    assert run(m.refresh_once()) is True
    assert worker.calls[-1]["allow_password"] is True


def test_two_factor_opens_window_once_then_waits_for_user(env):
    m, store, worker, clock = env
    store.save(CREDENTIALS, {"username": "u", "password": PASSWORD})
    worker.queue(AuthResult("needs_user", "清华统一认证要求二次验证"))
    run(m.refresh_once())
    assert worker.calls[0]["interactive"] is True  # 第一次自动打开可见窗口交给用户
    assert m.status.state == "needs_user" and "二次验证" in m.status.message
    clock.advance(m.plan_next() - clock.now())
    worker.queue(AuthResult("needs_user", "仍需验证"))
    run(m.refresh_once())
    assert worker.calls[1]["interactive"] is False  # 不反复弹窗打扰
    m.request_refresh(interactive=True)  # 用户点击“打开登录窗口”
    assert m.plan_next() == clock.now()
    worker.queue(ok_token("d"))
    assert run(m.refresh_once()) is True and worker.calls[2]["interactive"] is True


def test_report_401_triggers_reauth(env):
    m, store, worker, clock = env
    store.save(CREDENTIALS, {"username": "u", "password": PASSWORD})
    worker.queue(ok_token("e"))
    run(m.refresh_once())
    m.report_auth_failure()
    assert not m.token_valid() and m.plan_next() == pytest.approx(clock.now(), abs=1)


def test_background_loop_and_shutdown_cleanup(tmp_path):
    """真实事件循环：token 即将到点 → 后台自动更新；stop() 后任务结束、浏览器被关闭、不再执行。"""

    class RealClock:
        def now(self):
            return time.time()

    clock = RealClock()
    store = SecureStore(tmp_path / "s")
    worker = FakeWorker(clock)  # type: ignore[arg-type]
    now = time.time()
    # 让 4.5 小时点落在 0.3 秒后
    store.save(TOKEN, {"token": jwt(now), "obtained_at": now - 4.5 * H + 0.3, "expires_at": now + 0.5 * H + 0.3})
    store.save(CREDENTIALS, {"username": "u", "password": PASSWORD})
    worker.queue(lambda c: AuthResult("ok", "ok", token=jwt(time.time(), salt="loop"), storage_state={}))

    async def scenario():
        m = TokenManager(store, worker, clock)
        m.start()
        assert m.status.state == "valid"
        for _ in range(40):
            await asyncio.sleep(0.1)
            if worker.calls:
                break
        assert len(worker.calls) == 1 and m.status.last_outcome == "ok"
        assert m.plan_next() > time.time() + 4 * H
        await m.stop()
        assert m._task is None and worker.closed == 1 and m.status.state == "stopped"
        m.request_refresh()
        await asyncio.sleep(0.2)
        assert len(worker.calls) == 1  # 退出后不再运行

    run(scenario())


def test_logs_never_contain_secrets(env, caplog):
    m, store, worker, clock = env
    redact.install()
    caplog.handler.addFilter(redact.RedactFilter())
    store.save(CREDENTIALS, {"username": "u", "password": PASSWORD})
    worker.queue(ok_token("logcheck"), AuthResult("timeout", "x"))
    with caplog.at_level(logging.DEBUG):
        run(m.refresh_once())
        tok = m.get_token()
        logging.getLogger("app.test").warning("误把敏感值写进日志：%s %s", tok, PASSWORD)
        clock.advance(4.5 * H)
        run(m.refresh_once())
    text = caplog.text + json.dumps(m.public_status())
    assert tok not in text and PASSWORD not in text and "***" in caplog.text


# ---------------- 上游响应处理 ----------------
def _sse(*frames: str) -> bytes:
    return "".join(f"data: {f}\n\n" for f in frames).encode()


def _chunk(text: str, finish: str | None = None) -> str:
    return json.dumps({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}]}, ensure_ascii=False)


def transport(status=200, ctype="text/event-stream", body=b""):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        seen["url"] = str(req.url)
        return httpx.Response(status, headers={"content-type": ctype}, content=body)

    return httpx.MockTransport(handler), seen


async def collect(**kw):
    t = kw.pop("t")
    return "".join([p async for p in th.stream_chat("tok-123456789012345678901", kw.pop("model", "DeepSeek-V4.1-Flash"),
                                                    [{"role": "user", "content": "你好"}], transport=t, **kw)])


def test_stream_ok_and_request_shape():
    long = "你好！我是清华大模型，" * 10
    t, seen = transport(body=_sse(_chunk(long[:30]), _chunk(long[30:]), _chunk("", "stop"), "[DONE]"))
    assert run(collect(t=t)) == long
    assert seen["auth"] == "Bearer tok-123456789012345678901"
    assert seen["url"] == th.ENDPOINT
    assert seen["body"] == {"model": "DeepSeek-V4.1-Flash", "messages": [{"role": "user", "content": "你好"}],
                            "temperature": 0.6, "stream": True, "repetition_penalty": 1.2}


def test_short_normal_answer_passes():
    t, _ = transport(body=_sse(_chunk("在线"), _chunk("", "stop"), "[DONE]"))
    assert run(collect(t=t)) == "在线"


@pytest.mark.parametrize("status,ctype,body,match,auth", [
    (200, "application/json", '{"message":"模型不存在"}'.encode(), "不存在或已下线", False),
    (200, "application/json; charset=utf-8", '{"code":500,"msg":"服务器内部错误"}'.encode(), "业务错误", False),
    (200, "text/html", b"<html>login</html>", "网页", True),
    (200, "text/event-stream", _sse('{"error":{"message":"invalid token"}}'), "流式错误", True),
    (200, "text/event-stream", _sse(_chunk("模型不存在"), _chunk("", "stop"), "[DONE]"), "业务错误", False),
    (200, "text/event-stream", _sse(_chunk("回答到一半")), "意外中断", False),
    (200, "text/event-stream", _sse("not json"), "无法解析", False),
    (401, "application/json", b'{"message":"unauthorized"}', "401", True),
    (429, "application/json", b"{}", "429", False),
    (502, "text/plain", b"bad gateway", "502", False),
])
def test_upstream_errors(status, ctype, body, match, auth):
    t, _ = transport(status, ctype, body)
    with pytest.raises(th.UpstreamError, match=match) as ei:
        run(collect(t=t))
    assert ei.value.auth is auth


def test_reasoning_think_tags_are_stripped():
    t, _ = transport(body=_sse(_chunk("<thi"), _chunk("nk>推理过程</think>"), _chunk("最终答案：页表"), _chunk("", "stop"), "[DONE]"))
    assert run(collect(t=t, model="DeepSeek-R1-W8A8")) == "最终答案：页表"


def test_models_list():
    assert th.MODELS == ["DeepSeek-V4.1-Flash", "DeepSeek-R1-W8A8", "qwen3.8-27b"]
    assert not set(th.MODELS) & set(th.LEGACY_UNAVAILABLE)


# ---------------- 与平台模型网关、应用生命周期的集成 ----------------
def test_gateway_routes_tier_to_tsinghua_and_handles_401(tmp_path, monkeypatch):
    from app.services import llm, settings_store
    from app.services.tsinghua import runtime

    clock = FakeClock(time.time())
    store = SecureStore(tmp_path / "g")
    worker = FakeWorker(clock)
    m = TokenManager(store, worker, clock)
    tok = jwt(clock.now(), salt="gw")
    store.save(TOKEN, {"token": tok, "obtained_at": clock.now(), "expires_at": clock.now() + 5 * H})
    m.restore()
    runtime.set_manager(m)
    monkeypatch.setattr(settings_store, "resolve_tier", lambda t: {
        "provider": "tsinghua", "model": "qwen3.8-27b", "base_url": "", "api_key": "", "price_in": 0, "price_out": 0})
    monkeypatch.setattr(llm, "resolve_tier", settings_store.resolve_tier)
    monkeypatch.setattr(llm, "_record", lambda *a, **k: None)

    real = th.stream_chat
    state = {"status": 200}

    def fake_stream(token, model, messages, **kw):
        assert token == tok and model == "qwen3.8-27b"
        if state["status"] == 401:
            t, _ = transport(401, "application/json", b"{}")
        else:
            t, _ = transport(body=_sse(_chunk("在线"), _chunk("", "stop"), "[DONE]"))
        return real(token, model, messages, transport=t, **kw)

    monkeypatch.setattr(th, "stream_chat", fake_stream)
    try:
        assert run(llm.chat("strong", [{"role": "user", "content": "hi"}])) == "在线"
        state["status"] = 401
        with pytest.raises(RuntimeError, match="401"):
            run(llm.chat("strong", [{"role": "user", "content": "hi"}]))
        assert not m.token_valid()  # 401 后当前 token 作废并安排重新认证
        with pytest.raises(llm.LLMNotConfigured, match="不可用"):
            run(llm.chat("strong", [{"role": "user", "content": "hi"}]))
    finally:
        runtime.set_manager(None)


def test_app_exit_stops_manager_and_browser(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.tsinghua import runtime

    clock = FakeClock(time.time())
    worker = FakeWorker(clock)
    m = TokenManager(SecureStore(tmp_path / "life"), worker, clock)
    runtime.set_manager(m)
    try:
        with TestClient(app, base_url="http://localhost") as c:
            st = c.get("/api/tsinghua/status").json()
            assert st["running"] is True and st["state"] == "not_configured"
            assert "token" not in st  # 状态接口从不返回 token 本身
            assert c.get("/api/settings").status_code == 200
        assert m._task is None and worker.closed == 1 and m.status.state == "stopped"
    finally:
        runtime.set_manager(None)


def test_r1_implicit_reasoning_is_stripped():
    """实测格式：推理直接在 content 里，没有 <think>，只有 </think>。"""
    t, _ = transport(body=_sse(_chunk("嗯，用户问1+1，"), _chunk("答案是2。</thi"), _chunk("nk>\n\n2"), _chunk("", "stop"), "[DONE]"))
    assert run(collect(t=t, model="DeepSeek-R1-W8A8")) == "2"


def test_r1_without_close_tag_releases_everything():
    t, _ = transport(body=_sse(_chunk("直接回答：2"), _chunk("", "stop"), "[DONE]"))
    assert run(collect(t=t, model="DeepSeek-R1-W8A8")) == "直接回答：2"


def test_non_reasoning_model_streams_immediately():
    assert th.ThinkFilter(implicit_open=False).feed("在线") == "在线"
    assert th.REASONING_MODEL.search("DeepSeek-R1-W8A8") and not th.REASONING_MODEL.search("DeepSeek-V4.1-Flash")
    assert not th.REASONING_MODEL.search("qwen3.8-27b")


def test_no_response_times_out_with_clear_message():
    def handler(req):
        raise httpx.ReadTimeout("no data", request=req)

    with pytest.raises(th.UpstreamError, match="秒内没有响应"):
        run(collect(t=httpx.MockTransport(handler), idle_timeout=1))
