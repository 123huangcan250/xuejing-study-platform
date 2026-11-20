"""API 配置存取。全部存在 SQLite 的 setting 表里，密钥只在本机。"""
from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from ..db import engine
from ..models import Setting

TIERS = ("fast", "strong", "translate", "vision", "embedding")
PROVIDERS = ("openai", "tsinghua")

DEFAULTS: dict[str, Any] = {
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "tiers": {
        # 每档可单独覆盖 base_url / api_key，留空则使用全局配置
        # provider: "openai"=任意 OpenAI 兼容服务（用下面的 base_url/api_key）；"tsinghua"=清华大模型服务（自动登录取 token）
        "fast": {"provider": "openai", "model": "gpt-4o-mini", "base_url": "", "api_key": "", "price_in": 0.15, "price_out": 0.6},
        "strong": {"provider": "openai", "model": "gpt-4o", "base_url": "", "api_key": "", "price_in": 2.5, "price_out": 10},
        # 翻译档：留空 model = 不单独配置，翻译时依次使用 fast、strong 档
        "translate": {"provider": "openai", "model": "", "base_url": "", "api_key": "", "price_in": 0, "price_out": 0},
        "vision": {"provider": "openai", "model": "gpt-4o-mini", "base_url": "", "api_key": "", "price_in": 0.15, "price_out": 0.6},
        # 留空 model 表示不启用向量检索，只用 FTS5 关键词检索（清华服务不提供向量接口）
        "embedding": {"provider": "openai", "model": "", "base_url": "", "api_key": "", "price_in": 0.02, "price_out": 0},
    },
    "currency": "USD",
}

_KEY = "llm_config"


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    with Session(engine) as s:
        row = s.get(Setting, _KEY)
        stored = json.loads(row.value) if row and row.value else {}
    return _merge(DEFAULTS, stored)


def save_config(new: dict) -> dict:
    cur = load_config()
    # 前端回传的是掩码，空值或掩码表示“保持原密钥”
    def keep_key(old: str, incoming: str | None) -> str:
        if incoming is None or incoming == "" or "•" in incoming:
            return old
        return incoming

    merged = _merge(cur, {k: v for k, v in new.items() if k not in ("api_key", "tiers")})
    merged["api_key"] = keep_key(cur["api_key"], new.get("api_key"))
    if new.get("clear_api_key"):
        merged["api_key"] = ""
    merged.pop("clear_api_key", None)
    for t in TIERS:
        inc = (new.get("tiers") or {}).get(t) or {}
        tier = _merge(cur["tiers"][t], {k: v for k, v in inc.items() if k != "api_key"})
        if tier.get("provider") not in PROVIDERS or (t in ("embedding", "vision") and tier.get("provider") == "tsinghua"):
            tier["provider"] = "openai"  # 清华服务只提供文本对话
        tier["api_key"] = keep_key(cur["tiers"][t].get("api_key", ""), inc.get("api_key"))
        merged["tiers"][t] = tier
    with Session(engine) as s:
        row = s.get(Setting, _KEY) or Setting(key=_KEY)
        row.value = json.dumps(merged, ensure_ascii=False)
        s.add(row)
        s.commit()
    return merged


def mask(key: str) -> str:
    if not key:
        return ""
    return key[:3] + "••••" + key[-4:] if len(key) > 8 else "••••"


def public_config() -> dict:
    cfg = load_config()
    out = json.loads(json.dumps(cfg))
    out["api_key"] = mask(cfg["api_key"])
    out["has_api_key"] = bool(cfg["api_key"])
    for t in TIERS:
        out["tiers"][t]["api_key"] = mask(cfg["tiers"][t].get("api_key", ""))
    return out


def resolve_tier(tier: str) -> dict:
    """返回某档位实际生效的 {model, base_url, api_key}。"""
    cfg = load_config()
    t = cfg["tiers"].get(tier) or {}
    return {
        "provider": t.get("provider", "openai"),
        "model": t.get("model", ""),
        "base_url": (t.get("base_url") or cfg["base_url"]).rstrip("/"),
        "api_key": t.get("api_key") or cfg["api_key"],
        "price_in": float(t.get("price_in") or 0),
        "price_out": float(t.get("price_out") or 0),
    }
