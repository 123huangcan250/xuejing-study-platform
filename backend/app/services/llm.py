"""OpenAI 兼容网关：三档模型路由（fast/strong/vision）+ 向量模型 + Token 记账。"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from openai import AsyncOpenAI, BadRequestError, OpenAI
from sqlmodel import Session

from ..db import engine
from ..models import TokenUsage
from .settings_store import resolve_tier

log = logging.getLogger(__name__)


class LLMNotConfigured(RuntimeError):
    pass


def _conf(tier: str) -> dict:
    c = resolve_tier(tier)
    if c["provider"] == "tsinghua":
        if not c["model"]:
            raise LLMNotConfigured(f"尚未为「{tier}」档位选择清华模型。")
        return c
    if not c["api_key"] or not c["model"]:
        raise LLMNotConfigured(f"尚未配置「{tier}」档位的 API Key 或模型，请先到设置页填写。")
    return c


def _tsinghua_token() -> str:
    from .tsinghua.manager import TokenUnavailable
    from .tsinghua.runtime import get_manager

    try:
        return get_manager().get_token()
    except TokenUnavailable as e:
        raise LLMNotConfigured(f"{e}。可在设置页查看登录状态或打开登录窗口。") from None


async def _tsinghua_stream(tier: str, c: dict, messages: list[dict], temperature: float,
                           idle_timeout: float | None = None) -> AsyncIterator[str]:
    from .tsinghua import client as th
    from .tsinghua.runtime import get_manager

    token = _tsinghua_token()
    usage: dict = {}
    out: list[str] = []
    try:
        extra = {"idle_timeout": idle_timeout} if idle_timeout else {}
        async for piece in th.stream_chat(token, c["model"], messages, temperature=temperature,
                                          on_usage=lambda u: usage.update(u), **extra):
            out.append(piece)
            yield piece
    except th.UpstreamError as e:
        if e.auth:
            get_manager().report_auth_failure()
        raise RuntimeError(str(e)) from None
    if usage.get("prompt_tokens") is not None:
        _record(tier, c["model"], usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
    else:
        prompt_text = "".join(str(m.get("content", "")) for m in messages)
        _record(tier, c["model"], _estimate_tokens(prompt_text), _estimate_tokens("".join(out)), True)


def _record(tier: str, model: str, prompt: int, completion: int, estimated: bool = False) -> None:
    try:
        with Session(engine) as s:
            s.add(TokenUsage(tier=tier, model=model, prompt_tokens=prompt,
                             completion_tokens=completion, estimated=estimated))
            s.commit()
    except Exception:  # 记账失败不影响主流程
        log.exception("记录 token 用量失败")


def _estimate_tokens(text: str) -> int:
    # 粗略估算：中文约 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk + (len(text) - cjk) // 4 + 1


async def stream_chat(tier: str, messages: list[dict], temperature: float = 0.5) -> AsyncIterator[str]:
    c = _conf(tier)
    if c["provider"] == "tsinghua":
        async for piece in _tsinghua_stream(tier, c, messages, temperature):
            yield piece
        return
    client = AsyncOpenAI(api_key=c["api_key"], base_url=c["base_url"], timeout=120)
    kwargs = dict(model=c["model"], messages=messages, temperature=temperature, stream=True)
    try:
        stream = await client.chat.completions.create(**kwargs, stream_options={"include_usage": True})
    except BadRequestError:
        # 部分兼容服务不支持 stream_options，退回普通流式
        stream = await client.chat.completions.create(**kwargs)
    usage = None
    out: list[str] = []
    async for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if chunk.choices:
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                out.append(piece)
                yield piece
    if usage:
        _record(tier, c["model"], usage.prompt_tokens or 0, usage.completion_tokens or 0)
    else:
        prompt_text = "".join(str(m.get("content", "")) for m in messages)
        _record(tier, c["model"], _estimate_tokens(prompt_text), _estimate_tokens("".join(out)), True)


async def chat(tier: str, messages: list[dict], temperature: float = 0.3, json_mode: bool = False,
               idle_timeout: float | None = None) -> str:
    """idle_timeout：多久没有响应就判定失败（秒）；批量任务传较小的值，好尽快换用其他模型。"""
    c = _conf(tier)
    if c["provider"] == "tsinghua":
        # 清华接口未声明支持 response_format，JSON 由提示词约束，调用方负责解析
        return "".join([p async for p in _tsinghua_stream(tier, c, messages, temperature, idle_timeout)])
    client = AsyncOpenAI(api_key=c["api_key"], base_url=c["base_url"], timeout=idle_timeout or 120)
    kwargs = dict(model=c["model"], messages=messages, temperature=temperature)
    if json_mode:
        try:
            resp = await client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
        except BadRequestError:
            resp = await client.chat.completions.create(**kwargs)
    else:
        resp = await client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content or ""
    if resp.usage:
        _record(tier, c["model"], resp.usage.prompt_tokens or 0, resp.usage.completion_tokens or 0)
    return text


def embedding_enabled() -> bool:
    c = resolve_tier("embedding")
    return bool(c["provider"] == "openai" and c["model"] and c["api_key"])


def embed_sync(texts: list[str]) -> list[list[float]]:
    c = _conf("embedding")
    client = OpenAI(api_key=c["api_key"], base_url=c["base_url"], timeout=120)
    vectors: list[list[float]] = []
    total = 0
    for i in range(0, len(texts), 64):
        batch = texts[i : i + 64]
        resp = client.embeddings.create(model=c["model"], input=batch)
        vectors.extend(d.embedding for d in resp.data)
        total += (resp.usage.prompt_tokens if resp.usage else sum(_estimate_tokens(t) for t in batch))
    _record("embedding", c["model"], total, 0)
    return vectors


async def test_tier(tier: str) -> dict:
    c = _conf(tier)
    if tier == "embedding":
        client = AsyncOpenAI(api_key=c["api_key"], base_url=c["base_url"], timeout=30)
        resp = await client.embeddings.create(model=c["model"], input=["连通性测试"])
        return {"ok": True, "detail": f"向量维度 {len(resp.data[0].embedding)}"}
    text = await chat(tier, [{"role": "user", "content": "只回复两个字：在线"}], temperature=0.6 if c["provider"] == "tsinghua" else 0)
    return {"ok": True, "detail": text.strip()[:60]}
