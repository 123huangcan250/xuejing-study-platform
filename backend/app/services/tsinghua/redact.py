"""日志脱敏：运行中出现过的 token / 密码会被注册，任何日志里出现都替换成 ***。"""
from __future__ import annotations

import logging
import threading

_lock = threading.Lock()
_secrets: set[str] = set()


def register(secret: str | None) -> None:
    if secret and len(secret) >= 4:
        with _lock:
            _secrets.add(secret)


def unregister(secret: str | None) -> None:
    with _lock:
        _secrets.discard(secret or "")


def scrub(text: str) -> str:
    with _lock:
        items = sorted(_secrets, key=len, reverse=True)
    for s in items:
        if s in text:
            text = text.replace(s, "***")
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        clean = scrub(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        if record.exc_info and record.exc_info[1] is not None:
            # 异常文本也可能带出敏感信息：格式化后脱敏
            import traceback

            record.exc_text = scrub("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


_installed = False


def install() -> None:
    """给根 logger 和所有 handler 装上脱敏过滤器。"""
    global _installed
    # 子 logger 的记录冒泡到根 handler 时不会经过根 logger 的 filter，所以要装在 handler 上
    f = RedactFilter()
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "playwright", "app"):
        lg = logging.getLogger(name)
        if not any(isinstance(x, RedactFilter) for x in lg.filters):
            lg.addFilter(f)
        for h in lg.handlers:
            if not any(isinstance(x, RedactFilter) for x in h.filters):
                h.addFilter(f)
    _installed = True
