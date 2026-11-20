"""访问密码与登录会话（给远程访问用）。

模型（失败即关闭，fail-closed）
- 没设置访问密码：只接受 Host 为 localhost / 127.0.0.1 / ::1 的请求；其他一律 403。
  （服务本身只监听本机，远程入口只能是我们自己的 remote.ps1 起的 Tailscale Serve；没设密码时脚本拒绝启动它。）
- 设置了访问密码：**所有** /api 请求都要先登录（包括本机，不靠来源地址或 Host 判断，避免被伪造）。
  登录后发一个签名 Cookie（HttpOnly、SameSite=Lax），默认 30 天有效。
- 密码只能在本机用命令行设置（app.access_cli）；以 scrypt 哈希保存，原文不落盘；改密码会让所有已登录设备失效。
- 登录失败有限速，防暴力破解。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

from .tsinghua.runtime import SECURE_DIR
from .tsinghua.secure_store import SecureStore, SecureStoreError

NAME = "access"
COOKIE = "alp_session"
SESSION_DAYS = 30
MIN_PASSWORD_LEN = 10
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

_lock = threading.Lock()
_cache: dict = {"stamp": None, "rec": None}


def _store() -> SecureStore:
    return SecureStore(SECURE_DIR)


def _stamp():
    p = SECURE_DIR / f"{NAME}.dpapi"
    try:
        s = p.stat()
        return (s.st_mtime_ns, s.st_size)
    except FileNotFoundError:
        return None


def _record() -> dict | None:
    """带缓存地读取配置（文件没变就不重复解密，每个请求都会调用）。"""
    st = _stamp()
    with _lock:
        if _cache["stamp"] == st and (st is None or _cache["rec"] is not None):
            return _cache["rec"]
    try:
        rec = _store().load(NAME) if st else None
    except SecureStoreError:
        rec = None
    with _lock:
        _cache.update(stamp=st, rec=rec)
    return rec


def configured() -> bool:
    return _record() is not None


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def set_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"访问密码至少 {MIN_PASSWORD_LEN} 位")
    old = _record() or {}
    salt = secrets.token_bytes(16)
    rec = {
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(_hash(password, salt)).decode(),
        "secret": base64.b64encode(secrets.token_bytes(32)).decode(),  # 每次改密码都换签名密钥 → 旧会话全部失效
        "gen": int(old.get("gen", 0)) + 1,
    }
    _store().save(NAME, rec)
    with _lock:
        _cache.update(stamp=None, rec=None)


def disable() -> None:
    _store().delete(NAME)
    with _lock:
        _cache.update(stamp=None, rec=None)


def logout_all() -> None:
    """让所有已登录的设备失效（换签名密钥）。"""
    rec = _record()
    if rec:
        rec = {**rec, "secret": base64.b64encode(secrets.token_bytes(32)).decode(), "gen": int(rec.get("gen", 0)) + 1}
        _store().save(NAME, rec)
        with _lock:
            _cache.update(stamp=None, rec=None)


def check_password(password: str) -> bool:
    rec = _record()
    if not rec:
        return False
    salt = base64.b64decode(rec["salt"])
    return hmac.compare_digest(_hash(password, salt), base64.b64decode(rec["hash"]))


# ---------------------------------------------------------------- 会话 Cookie
def _sign(rec: dict, payload: str) -> str:
    return hmac.new(base64.b64decode(rec["secret"]), payload.encode(), hashlib.sha256).hexdigest()


def make_token(now: float | None = None) -> str:
    rec = _record()
    if not rec:
        raise RuntimeError("未设置访问密码")
    exp = int((now or time.time()) + SESSION_DAYS * 86400)
    payload = f"v1.{exp}.{rec['gen']}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(rec, payload)}"


def verify_token(token: str | None, now: float | None = None) -> bool:
    rec = _record()
    if not rec or not token:
        return False
    try:
        payload, sig = token.rsplit(".", 1)
        ver, exp, gen, _nonce = payload.split(".")
        if ver != "v1" or int(exp) < (now or time.time()) or int(gen) != int(rec["gen"]):
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sig, _sign(rec, payload))


def effective_host(headers) -> str:
    """浏览器看到的 Host。经前端代理时，原始 Host 在 x-alp-host（代理每次都会覆盖它）；直连后端只可能来自本机。"""
    return headers.get("x-alp-host") or headers.get("host", "")


def host_is_loopback(host_header: str) -> bool:
    h = (host_header or "").strip().lower()
    if h.startswith("["):  # [::1]:3400 → [::1]
        h = h[: h.index("]") + 1] if "]" in h else h
    elif h.count(":") == 1:  # localhost:3400 → localhost
        h = h.split(":")[0]
    return h in LOOPBACK_HOSTS


# ---------------------------------------------------------------- 登录限速
class Limiter:
    """按来源限速：连续失败 5 次起，每次失败都锁定（5、10、20、40 分钟…最长 1 小时）；另有全局兜底。"""

    def __init__(self):
        self.fails: dict[str, tuple[int, float]] = {}
        self.global_fails: list[float] = []

    def locked_for(self, key: str, now: float | None = None) -> int:
        now = now or time.time()
        self.global_fails = [t for t in self.global_fails if now - t < 600]
        if len(self.global_fails) >= 30:
            return 60
        n, until = self.fails.get(key, (0, 0.0))
        return int(until - now) + 1 if until > now else 0

    def failed(self, key: str, now: float | None = None) -> None:
        now = now or time.time()
        n, until = self.fails.get(key, (0, 0.0))
        n += 1
        if n >= 5:
            until = now + min(3600, 300 * 2 ** (n - 5))
        self.fails[key] = (n, until)
        self.global_fails.append(now)

    def ok(self, key: str) -> None:
        self.fails.pop(key, None)


LIMITER = Limiter()
