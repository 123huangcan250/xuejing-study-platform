"""文档翻译：哪些块要翻、批量调用模型、术语表保持一致、模型降级、后台任务与进度。

设计要点
- 以「文字块」为单位翻译并按块落库，已译的块不会重复调用模型（换页、刷新、重启都不丢）。
- 一批多块一起翻（省调用次数），用 JSON 对齐编号；对不上的块拆小重试。
- 模型按 translate → fast → strong 的顺序尝试；某个模型刚失败过会被冷却一段时间，先用后面的。
- 用户翻到哪页就把那一页插到队列最前面（优先于整份文档的后台翻译）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from sqlmodel import Session, select

from ..db import engine
from ..models import DocBlock, Document, Translation
from . import llm
from .blockstore import ensure_blocks, get_blocks, src_hash
from .settings_store import resolve_tier

log = logging.getLogger("app.translate")

TARGET = "zh"
CHAIN = ("translate", "fast", "strong")
MAX_CHARS = 1600  # 每批最多多少字符
MAX_ITEMS = 30
WORKERS = 3
IDLE_TIMEOUT = 45.0

_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]+")
_SYM = re.compile(r"[=+\-*/^_<>∑∫√≈≠≤≥→←∂∆Δ]")
_URL = re.compile(r"^(https?://|www\.)\S+$|^\S+@\S+\.\S+$", re.I)
_GLOSS = re.compile(r"([一-鿿][一-鿿A-Za-z0-9·]{1,14})[（(]([A-Za-z][A-Za-z \-/]{2,50})[）)]")


class TranslateError(RuntimeError):
    pass


def needs_translation(text: str) -> bool:
    """这个块是否值得翻译：有足够的英文单词，且不是网址、纯公式、已经是中文。"""
    t = text.strip()
    if len(t) < 2 or _URL.match(t):
        return False
    cjk = len(_CJK.findall(t))
    letters = len(re.findall(r"[A-Za-z]", t))
    if letters < 3 or (cjk and cjk / (cjk + letters) > 0.3):
        return False
    words = _WORD.findall(t)
    if not words:
        return False
    if len(words) <= 3 and _SYM.search(t) and max(len(w) for w in words) <= 4:
        return False  # 形如 “P = UI cosφ” 的公式行
    return True


# ----------------------------------------------------------------- 模型选择与降级
class TierHealth:
    """某个档位刚失败过，就先冷却一阵子，优先用其他档位（避免每一批都白等一个挂掉的模型）。"""

    cooldown = 180.0

    def __init__(self):
        self.until: dict[str, float] = {}

    def ok(self, tier: str):
        self.until.pop(tier, None)

    def fail(self, tier: str):
        self.until[tier] = time.monotonic() + self.cooldown

    def healthy(self, tier: str) -> bool:
        return self.until.get(tier, 0) <= time.monotonic()


HEALTH = TierHealth()


def configured_chain() -> list[str]:
    seen, out = set(), []
    for t in CHAIN:
        c = resolve_tier(t)
        if not c["model"] or (c["provider"] != "tsinghua" and not c["api_key"]):
            continue
        key = (c["provider"], c["model"], c["base_url"])
        if key not in seen:  # fast 和 strong 配成同一个模型时不重复尝试
            seen.add(key)
            out.append(t)
    return out


async def call_llm(messages: list[dict], *, json_mode: bool) -> tuple[str, str]:
    """返回 (模型输出, 实际使用的模型名)。"""
    chain = configured_chain()
    if not chain:
        raise TranslateError("还没有配置可用的模型：请在「设置」里配置「翻译」或「轻量快速」档位。")
    ordered = [t for t in chain if HEALTH.healthy(t)] + [t for t in chain if not HEALTH.healthy(t)]
    last: Exception | None = None
    for tier in ordered:
        try:
            text = await llm.chat(tier, messages, temperature=0.2, json_mode=json_mode, idle_timeout=IDLE_TIMEOUT)
            if not text.strip():
                raise RuntimeError("模型返回了空内容")
            HEALTH.ok(tier)
            return text, resolve_tier(tier)["model"]
        except Exception as e:  # noqa: BLE001 - 任何失败都换下一个模型
            last = e
            HEALTH.fail(tier)
            log.warning("翻译使用「%s」档位失败，换下一个：%s", tier, str(e)[:120])
    raise TranslateError(f"所有已配置的模型都失败了：{str(last)[:200]}")


# ----------------------------------------------------------------- 提示词与解析
SYSTEM = """你是电气/电力/计算机/数学等理工科的资深翻译，要把英文课件、教材翻译成简体中文，读者是英语不太好的学生。
规则：
1. 忠实准确、通顺自然，不增不减，不要解释；
2. 专业术语用国内教材通行译名；每个术语在整份材料里第一次出现时，用括号附上英文原词，如「潮流计算（power flow）」，之后只写中文；常见缩写保持原样（如 HVDC、PQ、AC）；
3. 公式、变量、符号、数字、单位、代码、网址保持原样；人名、机构名、地名可保留英文；
4. 保持每条的格式：项目符号、编号、换行都要保留；不要合并、拆分或遗漏条目；
5. 已经是中文的内容原样返回。
只输出 JSON：{"items":[{"i":编号,"t":"译文"}, ...]}，条目数量和编号必须与输入完全一致。"""

PLAIN_SYSTEM = """你是理工科资深翻译。把用户给的英文翻译成简体中文：专业术语用国内教材通行译名，并在括号里附英文原词；公式、符号、代码保持原样；只输出译文，不要解释。
如果输入只是一个单词或短语，给出最贴切的中文释义（理工科语境优先），可再补一句简短说明。"""


def build_messages(title: str, batch: list[tuple[int, str]], glossary: "OrderedDict[str, str]") -> list[dict]:
    head = [f"材料：《{title}》"] if title else []
    if glossary:
        recent = list(glossary.items())[-40:]
        head.append("已确定的术语译名（请保持一致）：" + "；".join(f"{en} → {zh}" for en, zh in recent))
    payload = json.dumps({"items": [{"i": i, "t": t} for i, t in batch]}, ensure_ascii=False)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(head + [payload])}]


def parse_items(raw: str, expected: list[int]) -> dict[int, str]:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    out: dict[int, str] = {}
    for it in data.get("items", []) if isinstance(data, dict) else []:
        if isinstance(it, dict) and isinstance(it.get("t"), str) and str(it.get("i")).lstrip("-").isdigit():
            i = int(it["i"])
            if i in expected and it["t"].strip():
                out[i] = it["t"].strip()
    return out


def remember_terms(glossary: "OrderedDict[str, str]", translated: str) -> None:
    for zh, en in _GLOSS.findall(translated):
        glossary[en.strip().lower()] = zh
        glossary.move_to_end(en.strip().lower())
    while len(glossary) > 150:
        glossary.popitem(last=False)


async def translate_group(title: str, batch: list[tuple[int, str]], glossary, depth: int = 0) -> tuple[dict[int, str], str]:
    """翻一批；缺了的块拆小重试，最后单块兜底。返回 ({块id: 译文}, 模型名)。"""
    ids = [i for i, _ in batch]
    raw, model = await call_llm(build_messages(title, batch, glossary), json_mode=True)
    got = parse_items(raw, ids)
    if not got and len(batch) == 1:  # 模型没按 JSON 回，但只有一条，直接当作译文
        plain = re.sub(r"^```\w*\n?|```$", "", raw.strip()).strip()
        if plain:
            got = {ids[0]: plain}
    missing = [(i, t) for i, t in batch if i not in got]
    if missing and depth < 3:
        if len(missing) == len(batch) and len(batch) > 1:
            half = len(batch) // 2
            parts = [batch[:half], batch[half:]]
        else:
            parts = [missing]
        for part in parts:
            sub, model = await translate_group(title, part, glossary, depth + 1)
            got.update(sub)
    for v in got.values():
        remember_terms(glossary, v)
    return got, model


# ----------------------------------------------------------------- 即时翻译（划词）
_CACHE: "OrderedDict[str, tuple[str, str]]" = OrderedDict()


async def translate_text(text: str, context: str = "") -> tuple[str, str, bool]:
    """返回 (译文, 模型名, 是否命中缓存)。"""
    key = src_hash(text)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return *_CACHE[key], True  # type: ignore[return-value]
    user = (f"（上下文：{context[:120]}）\n" if context else "") + text[:4000]
    out, model = await call_llm([{"role": "system", "content": PLAIN_SYSTEM}, {"role": "user", "content": user}], json_mode=False)
    out = out.strip()
    _CACHE[key] = (out, model)
    while len(_CACHE) > 500:
        _CACHE.popitem(last=False)
    return out, model, False


# ----------------------------------------------------------------- 后台任务
@dataclass
class Job:
    document_id: int
    title: str
    status: str = "running"  # running / done / error / cancelled
    total: int = 0
    done: int = 0
    error: str = ""
    model: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    pending: deque = field(default_factory=deque)  # (block_id, text)
    queued: set = field(default_factory=set)
    glossary: "OrderedDict[str, str]" = field(default_factory=OrderedDict)
    task: asyncio.Task | None = None


JOBS: dict[int, Job] = {}


def _todo(s: Session, document_id: int, pages: list[int] | None) -> tuple[Document, list[tuple[int, int, str]]]:
    doc = s.get(Document, document_id)
    blocks = ensure_blocks(s, doc)
    s.commit()
    have = {t.block_id: t.src_hash for t in s.exec(select(Translation).where(
        Translation.document_id == document_id, Translation.target == TARGET)).all()}
    todo = [(b.id, b.page, b.text) for b in blocks
            if needs_translation(b.text) and have.get(b.id) != src_hash(b.text) and (pages is None or b.page in pages)]
    return doc, todo


def start_job(document_id: int, pages: list[int] | None = None, priority: bool = False) -> Job | None:
    """开始（或扩充）一个文档的翻译任务。pages=None 表示整份；priority=True 表示插到队列最前面。"""
    with Session(engine) as s:
        doc, todo = _todo(s, document_id, pages)
        title = re.sub(r"\.\w+$", "", doc.filename)
    job = JOBS.get(document_id)
    if job and job.status == "running":
        fresh = [(i, t) for i, _, t in todo if i not in job.queued]
        if priority:  # 把请求的页提到最前（包括已经排在后面的）
            ids = {i for i, _, _ in todo}
            job.pending = deque([x for x in job.pending if x[0] not in ids])
            job.pending.extendleft(reversed([(i, t) for i, _, t in todo]))
            job.queued |= ids
            job.total += len(fresh)
        else:
            job.pending.extend(fresh)
            job.queued.update(i for i, _ in fresh)
            job.total += len(fresh)
        return job
    if not todo:
        return None
    job = Job(document_id=document_id, title=title, total=len(todo))
    job.pending = deque((i, t) for i, _, t in todo)
    job.queued = {i for i, _, _ in todo}
    with Session(engine) as s:  # 用库里已有的译文预热术语表，续译时前后一致
        for t in s.exec(select(Translation.text).where(Translation.document_id == document_id).limit(300)).all():
            remember_terms(job.glossary, t)
    JOBS[document_id] = job
    job.task = asyncio.create_task(_run(job), name=f"translate-{document_id}")
    return job


def _take(job: Job) -> list[tuple[int, str]]:
    batch, chars = [], 0
    while job.pending and len(batch) < MAX_ITEMS:
        i, t = job.pending[0]
        if batch and chars + len(t) > MAX_CHARS:
            break
        job.pending.popleft()
        batch.append((i, t))
        chars += len(t)
    return batch


def _save(document_id: int, texts: dict[int, str], originals: dict[int, str], model: str) -> None:
    with Session(engine) as s:
        for bid, tr in texts.items():
            for old in s.exec(select(Translation).where(Translation.block_id == bid, Translation.target == TARGET)).all():
                s.delete(old)
            s.add(Translation(document_id=document_id, block_id=bid, target=TARGET, src_hash=src_hash(originals[bid]),
                              text=tr, model=model))
        s.commit()


async def _run(job: Job) -> None:
    async def worker():
        while job.status == "running":
            batch = _take(job)
            if not batch:
                return
            originals = dict(batch)
            try:
                got, model = await translate_group(job.title, batch, job.glossary)
            except TranslateError as e:  # 所有模型都失败：停下来，别一直重试
                job.status, job.error = "error", str(e)
                job.pending.extendleft(reversed(batch))
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("翻译批次异常")
                job.status, job.error = "error", f"翻译出错：{type(e).__name__}"
                return
            _save(job.document_id, got, originals, model)
            job.done += len(got)
            job.model = model
            job.queued -= set(got)

    try:
        await asyncio.gather(*(worker() for _ in range(WORKERS)))
        if job.status == "running":
            job.status = "done"
    except asyncio.CancelledError:
        job.status = "cancelled"
    finally:
        job.finished = time.time()


def cancel_job(document_id: int) -> bool:
    job = JOBS.get(document_id)
    if job and job.status == "running" and job.task:
        job.status = "cancelled"
        job.task.cancel()
        return True
    return False


async def shutdown() -> None:
    await cancel_tasks(j.task for j in list(JOBS.values()))


async def cancel_tasks(tasks) -> None:
    """退出时取消并等待后台任务；只处理属于当前事件循环、还没结束的任务。"""
    loop = asyncio.get_running_loop()
    live = [t for t in tasks if t is not None and not t.done() and t.get_loop() is loop]
    for t in live:
        t.cancel()
    if live:
        await asyncio.gather(*live, return_exceptions=True)


def status(s: Session, document_id: int) -> dict:
    doc = s.get(Document, document_id)
    blocks = ensure_blocks(s, doc)
    s.commit()
    ids = {b.id: b.text for b in blocks if needs_translation(b.text)}
    have = {t.block_id: t.src_hash for t in s.exec(select(Translation).where(
        Translation.document_id == document_id, Translation.target == TARGET)).all()}
    translated = sum(1 for i, t in ids.items() if have.get(i) == src_hash(t))
    job = JOBS.get(document_id)
    return {
        "translatable": len(ids), "translated": translated,
        "running": bool(job and job.status == "running"),
        "state": job.status if job else ("done" if ids and translated == len(ids) else "idle"),
        "error": job.error if job and job.status == "error" else "",
        "model": job.model if job else "",
        "chain": [resolve_tier(t)["model"] for t in configured_chain()],
    }
