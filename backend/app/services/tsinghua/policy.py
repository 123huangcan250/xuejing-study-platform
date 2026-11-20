"""token 有效期与调度策略（纯函数，便于用模拟时钟测试）。

文档：每次登录后 token 有效约 5 小时。保守做法：签发后 4.5 小时重新认证取新 token。
"""
from __future__ import annotations

import base64
import json

FIVE_HOURS = 5 * 3600.0
REFRESH_AFTER = 4.5 * 3600.0
REFRESH_MARGIN = FIVE_HOURS - REFRESH_AFTER  # 到期前 30 分钟
# 连续失败退避（秒）：5、10、20、40 分钟，之后每 60 分钟一次
BACKOFF = [300.0, 600.0, 1200.0, 2400.0, 3600.0]


def _jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode()).decode("utf-8"))
    except Exception:  # noqa: BLE001 - 非 JWT 也合法
        return {}


def token_expiry(token: str, obtained_at: float) -> float:
    """取 JWT exp 与「签发时间 + 5 小时」中更早者；不是 JWT 时按获取时间 + 5 小时。"""
    c = _jwt_claims(token)
    issued = c.get("iat") if isinstance(c.get("iat"), (int, float)) else None
    documented = (issued if issued and issued <= obtained_at + 300 else obtained_at) + FIVE_HOURS
    reported = c.get("exp") if isinstance(c.get("exp"), (int, float)) else None
    return min(documented, reported) if reported else documented


def refresh_at(expires_at: float) -> float:
    return expires_at - REFRESH_MARGIN


def backoff_delay(consecutive_failures: int) -> float:
    if consecutive_failures <= 0:
        return 0.0
    return BACKOFF[min(consecutive_failures, len(BACKOFF)) - 1]


def looks_like_token(token: object) -> bool:
    return isinstance(token, str) and 20 <= len(token) <= 8192 and not any(ch.isspace() for ch in token)
