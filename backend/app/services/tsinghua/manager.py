"""清华模型 token 生命周期管理（只在项目运行期间工作，不注册任何系统计划任务）。

- 启动：从 DPAPI 存储恢复 token 记录；仍有效就只排程，不打开浏览器。
- 排程：在 token 到期前 30 分钟（即签发后约 4.5 小时）重新认证；启动时 token 已用过一段时间则按剩余时间提前。
- 失败：退避 5/10/20/40/60 分钟重试；旧 token 未过期时继续使用；绝不把失败报告成成功。
- 同一个旧 token 被再次读出不算“更新成功”，不延长有效期。
- 密码自动提交未通过（疑似密码错误）后暂停自动提交，直到用户在本机更新凭据，避免反复提交导致锁号。
- 需要验证码/二次验证时打开可见窗口交给用户；超时未完成则只在后台做“无密码的会话复用”尝试，等用户手动触发。
- 退出：stop() 取消后台任务并关闭浏览器进程。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import policy, redact
from .auth_worker import AuthResult
from .secure_store import SecureStore, SecureStoreError

log = logging.getLogger("app.tsinghua.manager")

CREDENTIALS = "credentials"
SESSION = "browser_session"
TOKEN = "token"
META = "auth_meta"


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class Worker(Protocol):
    def collect(self, *, saved_state, renew, credentials, allow_password, interactive, on_status=None) -> AuthResult: ...
    def close(self) -> None: ...
    def reset(self) -> None: ...


class TokenUnavailable(RuntimeError):
    pass


@dataclass
class Status:
    state: str = "not_configured"  # not_configured / valid / refreshing / needs_user / credentials_invalid / error / expired / stopped
    message: str = "尚未登录清华大模型系统"
    obtained_at: float | None = None
    expires_at: float | None = None
    next_run_at: float | None = None
    consecutive_failures: int = 0
    last_attempt_at: float | None = None
    last_success_at: float | None = None
    last_outcome: str = ""
    history: list[dict] = field(default_factory=list)
    last_detail: dict = field(default_factory=dict)


class TokenManager:
    def __init__(self, store: SecureStore, worker: Worker, clock: Clock | None = None):
        self.store = store
        self.worker = worker
        self.clock = clock or SystemClock()
        self.status = Status()
        self._token: str | None = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._stopped = False
        self._lock = asyncio.Lock()
        self._interactive_pending = False  # 用户手动请求打开登录窗口
        self._auto_interactive_used = False  # 每次“需要用户”事件只自动弹一次窗口

    # ---------------- 持久化 ----------------
    def _meta(self) -> dict:
        try:
            return self.store.load(META) or {}
        except SecureStoreError:
            return {}

    def _save_meta(self, **kw) -> None:
        m = self._meta()
        m.update(kw)
        self.store.save(META, m)

    def has_credentials(self) -> bool:
        return self.store.exists(CREDENTIALS)

    def configured(self) -> bool:
        return bool(self._token) or self.store.exists(SESSION) or self.has_credentials()

    def password_blocked(self) -> bool:
        return bool(self._meta().get("password_blocked"))

    def set_credentials(self, username: str, password: str) -> None:
        """更新本机凭据（改密码后调用），同时解除“暂停自动提交”。"""
        if not username or not password:
            raise ValueError("账号和密码都不能为空")
        self.store.save(CREDENTIALS, {"username": username, "password": password})
        self._save_meta(password_blocked=False, blocked_reason="")

    def clear_credentials(self) -> None:
        self.store.delete(CREDENTIALS)

    def logout(self) -> None:
        """清除 token 和浏览器登录状态（保留账号密码，除非另行清除）。"""
        redact.unregister(self._token)
        self._token = None
        self.store.delete(TOKEN)
        self.store.delete(SESSION)
        self.status = Status(state="not_configured", message="已清除本机登录状态")
        self._wake.set()

    def _load_credentials(self) -> dict | None:
        try:
            c = self.store.load(CREDENTIALS)
        except SecureStoreError:
            return None
        if c and isinstance(c.get("username"), str) and isinstance(c.get("password"), str):
            redact.register(c["password"])
            return c
        return None

    # ---------------- token 读取 ----------------
    def restore(self) -> None:
        try:
            rec = self.store.load(TOKEN)
        except SecureStoreError as e:
            self.status.state, self.status.message = "error", str(e)
            return
        if not rec or not policy.looks_like_token(rec.get("token")):
            self.status.state = "not_configured" if not self.configured() else "expired"
            self.status.message = "尚未取得 token" if self.status.state == "expired" else self.status.message
            return
        self._token = rec["token"]
        redact.register(self._token)
        self.status.obtained_at = rec.get("obtained_at")
        self.status.expires_at = rec.get("expires_at")
        if self.clock.now() < (self.status.expires_at or 0):
            self.status.state, self.status.message = "valid", "已恢复上次的登录状态"
        else:
            self.status.state, self.status.message = "expired", "上次的 token 已过期，需要重新认证"

    def token_valid(self) -> bool:
        return bool(self._token and self.status.expires_at and self.clock.now() < self.status.expires_at - 30)

    def get_token(self) -> str:
        if self.token_valid():
            return self._token  # type: ignore[return-value]
        self._wake.set()  # 让后台立即尝试更新
        reason = self.status.message if self.status.state not in ("valid",) else "token 已过期"
        raise TokenUnavailable(f"清华模型 token 不可用：{reason}")

    def report_auth_failure(self) -> None:
        """上游返回 401：当前 token 作废，立即安排重新认证。"""
        if self._token:
            self.status.expires_at = self.clock.now()
            self.status.state, self.status.message = "expired", "上游拒绝了当前 token（HTTP 401），正在重新认证"
        self._wake.set()

    # ---------------- 排程 ----------------
    def plan_next(self) -> float | None:
        """下一次尝试刷新的时间戳；None 表示不需要自动处理（未配置 / 等用户操作）。"""
        now = self.clock.now()
        if self._stopped:
            return None
        if self._interactive_pending:
            return now
        if not self.configured():
            return None
        if self.status.consecutive_failures > 0:
            last = self.status.last_attempt_at or now
            return last + policy.backoff_delay(self.status.consecutive_failures)
        if self._token and self.status.expires_at:
            return max(now, policy.refresh_at(self.status.expires_at))
        return now

    async def _wait_until(self, when: float | None) -> None:
        timeout = None if when is None else max(0.0, when - self.clock.now())
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()  # 唤醒后再清除，避免丢掉等待前就到达的唤醒信号

    async def _loop(self) -> None:
        while not self._stopped:
            when = self.plan_next()
            self.status.next_run_at = when
            if when is None or when > self.clock.now():
                await self._wait_until(when)
                if self._stopped:
                    break
                when = self.plan_next()
                if when is None or when > self.clock.now() + 0.5:
                    continue
            await self.refresh_once()

    def start(self) -> None:
        self.restore()
        self._stopped = False
        self.worker.reset()
        self._task = asyncio.create_task(self._loop(), name="tsinghua-token-manager")

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        await asyncio.to_thread(self.worker.close)  # 先关浏览器，让进行中的登录尽快退出
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=20)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:  # noqa: BLE001
                log.exception("token 管理任务退出异常")
        self._task = None
        self.status.state, self.status.next_run_at = "stopped", None

    def request_refresh(self, interactive: bool = False) -> None:
        if interactive:
            self._interactive_pending = True
        else:
            self.status.consecutive_failures = 0  # 手动触发：不再等退避
        self._wake.set()

    # ---------------- 刷新 ----------------
    def _record(self, outcome: str, message: str) -> None:
        self.status.last_outcome = outcome
        self.status.history = ([{"at": self.clock.now(), "outcome": outcome, "message": message}] + self.status.history)[:20]

    def _fail(self, outcome: str, message: str, state: str | None = None) -> None:
        self.status.consecutive_failures += 1
        self._record(outcome, message)
        if state:
            self.status.state = state
        elif self.token_valid():
            self.status.state = "valid"
            message += "；旧 token 仍在有效期内，继续使用"
        else:
            self.status.state = "expired" if self._token else "error"
        self.status.message = message
        log.warning("清华 token 更新失败：%s（第 %d 次，%s 后重试）", outcome, self.status.consecutive_failures,
                    f"{int(policy.backoff_delay(self.status.consecutive_failures) // 60)} 分钟")

    async def refresh_once(self) -> bool:
        """执行一次登录/取 token。返回是否真的拿到了新 token。"""
        async with self._lock:
            if self._stopped:
                return False
            interactive = self._interactive_pending or not self._auto_interactive_used
            self._interactive_pending = False
            allow_password = not self.password_blocked()
            try:
                saved = self.store.load(SESSION)
            except SecureStoreError:
                saved = None
            self.status.state, self.status.message = "refreshing", "正在重新认证并获取新 token"
            self.status.last_attempt_at = self.clock.now()

            def on_status(state: str, msg: str):
                self.status.message = msg
                if state == "needs_user":
                    self.status.state = "needs_user"

            res: AuthResult = await asyncio.to_thread(
                self.worker.collect, saved_state=saved, renew=bool(self._token), credentials=self._load_credentials,
                allow_password=allow_password, interactive=interactive, on_status=on_status)
            if self._stopped or res.outcome == "stopped":
                self.status.state = "stopped"
                return False
            self.status.last_detail = res.detail or {}
            if res.outcome != "ok" and res.storage_state:
                self.store.save(SESSION, res.storage_state)  # 登录成功但取 token 失败：保留会话，避免重复二次验证

            if res.outcome == "ok":
                tok = res.token
                if not policy.looks_like_token(tok):
                    self._fail("invalid_token", "网站返回的内容不像有效 token")
                    return False
                if tok == self._token:
                    # 读到的仍是旧 token：不算更新成功，不延长有效期
                    self._fail("same_token", "重新认证后读到的仍是旧 token，未确认更新")
                    return False
                obtained = self.clock.now()
                expires = policy.token_expiry(tok, obtained)
                if expires <= obtained + 60:
                    self._fail("expired_token", "网站返回的 token 已过期或即将过期")
                    return False
                redact.register(tok)
                old = self._token
                self._token = tok
                self.store.save(TOKEN, {"token": tok, "obtained_at": obtained, "expires_at": expires})
                if res.storage_state:
                    self.store.save(SESSION, res.storage_state)
                if old:
                    redact.unregister(old)
                self.status.obtained_at, self.status.expires_at = obtained, expires
                self.status.consecutive_failures = 0
                self.status.last_success_at = obtained
                self.status.state = "valid"
                self.status.message = "已取得新 token"
                self._auto_interactive_used = False
                self._record("ok", "已取得新 token")
                log.info("清华 token 已更新，%.1f 小时后再次更新", (policy.refresh_at(expires) - obtained) / 3600)
                return True

            if res.outcome == "bad_credentials":
                self._save_meta(password_blocked=True, blocked_reason=res.message)
                self._fail(res.outcome, res.message, state=None if self.token_valid() else "credentials_invalid")
                if self.status.state == "valid":
                    self.status.message = res.message + "；旧 token 仍在有效期内"
                return False
            if res.outcome == "needs_user":
                if interactive:
                    self._auto_interactive_used = True  # 自动弹过窗了，之后只等用户手动触发
                self._fail(res.outcome, res.message, state=None if self.token_valid() else "needs_user")
                return False
            self._fail(res.outcome, res.message)
            return False

    # ---------------- 对外状态 ----------------
    def public_status(self) -> dict:
        s = self.status
        now = self.clock.now()
        return {
            "state": s.state,
            "message": s.message,
            "token_valid": self.token_valid(),
            "obtained_at": s.obtained_at,
            "expires_at": s.expires_at,
            "expires_in_s": max(0, int(s.expires_at - now)) if s.expires_at else None,
            "next_run_at": s.next_run_at,
            "consecutive_failures": s.consecutive_failures,
            "last_attempt_at": s.last_attempt_at,
            "last_success_at": s.last_success_at,
            "has_credentials": self.has_credentials(),
            "has_session": self.store.exists(SESSION),
            "password_blocked": self.password_blocked(),
            "history": s.history[:8],
            "last_detail": s.last_detail,
            "running": self._task is not None and not self._task.done(),
        }
