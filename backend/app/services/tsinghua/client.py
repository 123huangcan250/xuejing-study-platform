"""清华 OpenAI 兼容接口客户端。

上游有时 HTTP 200 也会返回业务错误（例如 JSON {"message": "模型不存在"}，或把错误当作正文流出来），
所以这里同时检查：HTTP 状态 → Content-Type → 每个 SSE 帧 → 开头一小段正文 → 是否正常结束。
"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator, Callable

import httpx

ENDPOINT = "https://madmodel.cs.tsinghua.edu.cn/v1/chat/completions"
# 2026-11-12 经真实流式调用验证可用（模型供应可能变化，可在设置页重新检测）
MODELS = ["DeepSeek-V4.1-Flash", "DeepSeek-R1-W8A8", "qwen3.8-27b"]
# 旧版 PDF 里出现过，但 2026-11-12 实测返回“模型不存在”，不作为可用模型展示
LEGACY_UNAVAILABLE = ["DeepSeek-R1-671B", "DeepSeek-R1-32B"]

_ERR_PATTERNS = re.compile(
    r"^\s*(模型不存在|模型.{0,10}(不存在|不可用|已下线)|model\s+.{0,40}(not\s+(exist|found)|does\s+not\s+exist)|"
    r"(token|令牌).{0,10}(无效|过期|失效)|invalid\s+token|unauthori[sz]ed|请先登录|未登录|服务器繁忙|rate\s*limit)",
    re.I,
)
_AUTH_PATTERNS = re.compile(r"(token|令牌).{0,10}(无效|过期|失效)|invalid\s+token|unauthori[sz]ed|请先登录|未登录", re.I)
_PEEK_CHARS = 64


class UpstreamError(RuntimeError):
    def __init__(self, message: str, *, auth: bool = False, status: int | None = None):
        super().__init__(message)
        self.auth = auth
        self.status = status


def _error_text(obj) -> str | None:
    """从 JSON 响应 / SSE 帧里识别业务错误。"""
    if not isinstance(obj, dict):
        return None
    err = obj.get("error")
    if err:
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or err)
        return str(err)
    if "choices" not in obj:
        for k in ("message", "msg", "detail", "errmsg"):
            if isinstance(obj.get(k), str) and obj[k].strip():
                return obj[k]
        code = obj.get("code")
        if code not in (None, 0, 200, "0", "200"):
            return f"上游错误码 {code}"
    return None


REASONING_MODEL = re.compile(r"(^|[-_/])R1([-_]|$)|reason", re.I)


class ThinkFilter:
    """去掉推理过程，只保留最终回答。

    两种格式都处理：
    - 显式：<think>推理</think>回答
    - 隐式（2026-11-19 实测 DeepSeek-R1-W8A8）：推理直接出现在 content 里，没有 <think>，只有结尾的 </think>。
      对推理模型先缓冲，见到 </think> 后丢掉之前的内容；若整段都没有 </think>，结束时原样放出。
    """

    def __init__(self, implicit_open: bool = False):
        self.inside = False
        self.pending_implicit = implicit_open
        self.buf = ""

    def feed(self, text: str) -> str:
        self.buf += text
        if self.pending_implicit:
            i = self.buf.find("</think>")
            if i < 0:
                if "<think>" in self.buf:  # 原来是显式格式
                    self.pending_implicit = False
                else:
                    return ""
            else:
                self.buf, self.pending_implicit = self.buf[i + 8 :].lstrip(), False
        out = []
        while self.buf:
            if self.inside:
                i = self.buf.find("</think>")
                if i < 0:
                    self.buf = self.buf[-8:]
                    return "".join(out)
                self.buf, self.inside = self.buf[i + 8 :].lstrip(), False
            else:
                i = self.buf.find("<think>")
                if i < 0:
                    keep = next((k for k in range(min(7, len(self.buf)), 0, -1) if "<think>".startswith(self.buf[-k:])), 0)
                    out.append(self.buf[: len(self.buf) - keep])
                    self.buf = self.buf[len(self.buf) - keep :]
                    return "".join(out)
                out.append(self.buf[:i])
                self.buf, self.inside = self.buf[i + 7 :], True
        return "".join(out)

    def flush(self) -> str:
        rest, self.buf = ("" if self.inside else self.buf), ""
        self.pending_implicit = False
        return rest


def _check_status(resp: httpx.Response, body: bytes | None = None) -> None:
    if resp.status_code == 401:
        raise UpstreamError("清华模型认证失败（HTTP 401），token 无效或已过期", auth=True, status=401)
    if resp.status_code == 403:
        raise UpstreamError("清华模型拒绝访问（HTTP 403），请检查账号权限", status=403)
    if resp.status_code == 404:
        raise UpstreamError("清华模型接口不存在（HTTP 404）", status=404)
    if resp.status_code == 429:
        raise UpstreamError("清华模型服务繁忙或限流（HTTP 429），请稍后再试", status=429)
    if resp.status_code >= 400:
        detail = ""
        if body:
            try:
                detail = _error_text(json.loads(body)) or ""
            except ValueError:
                pass
        raise UpstreamError(f"清华模型请求失败（HTTP {resp.status_code}）{('：' + detail[:120]) if detail else ''}", status=resp.status_code)


def _classify_body(body: bytes, model: str) -> UpstreamError:
    text = body.decode("utf-8", errors="ignore").strip()
    if text.startswith("<"):
        return UpstreamError("清华模型返回了网页而不是接口数据（可能需要重新登录）", auth=True)
    try:
        obj = json.loads(text)
    except ValueError:
        return UpstreamError(f"清华模型返回了无法识别的响应：{text[:80]}")
    msg = _error_text(obj)
    if msg:
        if "不存在" in msg or "not exist" in msg.lower() or "not found" in msg.lower():
            return UpstreamError(f"清华模型 {model} 不存在或已下线（上游：{msg[:80]}）")
        return UpstreamError(f"清华模型返回业务错误：{msg[:160]}", auth=bool(_AUTH_PATTERNS.search(msg)))
    return UpstreamError("清华模型没有返回预期的流式响应")


async def stream_chat(token: str, model: str, messages: list[dict], *, temperature: float = 0.6,
                      repetition_penalty: float | None = 1.2, max_tokens: int | None = None,
                      transport: httpx.AsyncBaseTransport | None = None,
                      on_usage: Callable[[dict], None] | None = None, endpoint: str = ENDPOINT,
                      idle_timeout: float = 90) -> AsyncIterator[str]:
    """idle_timeout：等响应头、或两段数据之间超过这么久没有任何数据就判定失败（不是整次调用的总时长）。"""
    body: dict = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
    if repetition_penalty is not None:
        body["repetition_penalty"] = repetition_penalty
    if max_tokens:
        body["max_tokens"] = max_tokens
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    limits = httpx.Timeout(connect=20, read=idle_timeout, write=30, pool=30)
    async with httpx.AsyncClient(transport=transport, timeout=limits) as client:
        try:
            req = client.build_request("POST", endpoint, headers=headers, json=body)
            resp = await client.send(req, stream=True)
        except httpx.TimeoutException as e:
            if isinstance(e, httpx.ConnectTimeout):
                raise UpstreamError("连接清华模型服务超时（网络不可达？）") from None
            raise UpstreamError(f"清华模型 {model} 在 {int(idle_timeout)} 秒内没有响应（服务可能繁忙或该模型暂不可用），可稍后重试或换用其他模型") from None
        except httpx.HTTPError as e:
            raise UpstreamError(f"无法连接清华模型服务（{type(e).__name__}）") from None
        try:
            if resp.status_code >= 400:
                _check_status(resp, await resp.aread())
            ctype = resp.headers.get("content-type", "")
            if "text/event-stream" not in ctype:
                raise _classify_body(await resp.aread(), model)

            think = ThinkFilter(implicit_open=bool(REASONING_MODEL.search(model)))
            peek: list[str] = []
            peeked = 0
            released = False
            finished = False
            done_marker = False
            usage = None
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                while True:
                    m = re.search(r"\r?\n\r?\n", buf)
                    if not m:
                        break
                    event, buf = buf[: m.start()], buf[m.end() :]
                    data = "\n".join(l[5:].lstrip() for l in event.splitlines() if l.startswith("data:"))
                    if not data:
                        continue
                    if data.strip() == "[DONE]":
                        finished = done_marker = True
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        if _ERR_PATTERNS.search(data):
                            raise UpstreamError(f"清华模型返回业务错误：{data[:120]}", auth=bool(_AUTH_PATTERNS.search(data)))
                        raise UpstreamError("清华模型返回了无法解析的流数据")
                    msg = _error_text(obj)
                    if msg:
                        raise UpstreamError(f"清华模型返回流式错误：{msg[:160]}", auth=bool(_AUTH_PATTERNS.search(msg)))
                    if obj.get("usage"):
                        usage = obj["usage"]
                    for ch in obj.get("choices") or []:
                        delta = ch.get("delta") or ch.get("message") or {}
                        piece = think.feed(delta.get("content") or "")
                        if ch.get("finish_reason"):
                            finished = True
                        if not piece:
                            continue
                        if released:
                            yield piece
                            continue
                        # 先攒开头一小段，确认不是“模型不存在”之类被当成正文的错误
                        peek.append(piece)
                        peeked += len(piece)
                        if peeked >= _PEEK_CHARS:
                            head = "".join(peek)
                            if _ERR_PATTERNS.search(head[:40]) and len(head) < 120:
                                raise UpstreamError(f"清华模型返回业务错误：{head[:80]}", auth=bool(_AUTH_PATTERNS.search(head)))
                            released = True
                            peek.clear()
                            yield head
                if done_marker:
                    break
            tail = think.flush()
            head = "".join(peek) + tail
            if not released and head:
                if _ERR_PATTERNS.search(head) and len(head) < 120:
                    raise UpstreamError(f"清华模型返回业务错误：{head[:80]}", auth=bool(_AUTH_PATTERNS.search(head)))
                yield head
            elif released and tail:
                yield tail
            if not finished:
                raise UpstreamError("清华模型响应意外中断（没有收到结束标记）")
            if not released and not head:
                raise UpstreamError("清华模型返回了空回答")
            if usage and on_usage:
                on_usage(usage)
        except httpx.TimeoutException:
            raise UpstreamError(f"清华模型 {model} 输出中途超过 {int(idle_timeout)} 秒没有新数据，已中止") from None
        except httpx.HTTPError as e:
            raise UpstreamError(f"清华模型响应中断（{type(e).__name__}）") from None
        finally:
            await resp.aclose()


async def chat(token: str, model: str, messages: list[dict], **kw) -> str:
    """非流式场景也走流式接口再拼接，复用同一套错误检测。"""
    return "".join([p async for p in stream_chat(token, model, messages, **kw)])
