"""扫描版 PDF 的文字识别：把没有文字层的页渲染成图片，交给「视觉解析档」模型转写（公式用 LaTeX）。

结果存进 OcrPage（每页一行，处理完一页就提交一次，中途停止或重启不会白花钱），
再合并成文字块（DocBlock，没有 bbox）和检索分块，之后翻译、知识梳理、问答都和有文字层的页一样用。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from sqlmodel import Session, select

from ..db import engine
from ..models import DocBlock, Document, OcrPage
from . import llm, settings_store

log = logging.getLogger(__name__)

CONCURRENCY = 3
MAX_SIDE = 1800  # 渲染图长边像素：公式下标要看得清，又不至于太贵
PAGE_TIMEOUT = 150

PROMPT = (
    "这是一份课程资料（扫描件）的一页。请把页面内容完整转写成 Markdown：\n"
    "1. 保持阅读顺序；标题用 #，列表用 -，表格用 Markdown 表格。\n"
    "2. 数学公式一律写成 LaTeX：行内公式用 $...$，独立成行的公式用 $$...$$。下标上标、希腊字母、矩阵、求和积分都要写对。\n"
    "3. 图、曲线、电路图等无法转写的图形，用一行「[图：一句话描述图里画了什么]」代替；图里的文字标注要保留。\n"
    "4. 不要翻译，不要总结，不要解释，不要输出页眉页脚和页码。\n"
    "5. 看不清的字用 [?] 标出，不要猜。整页空白就只输出 EMPTY。\n"
    "只输出转写结果本身。"
)


class OcrError(RuntimeError):
    pass


# ----------------------------------------------------------------- 配置
def vision_status() -> dict:
    """视觉档是否可用。设置页默认的 gpt-4o-mini 是占位，没填自己的 Key / 地址就不算配置好
    （否则会拿默认服务的 Key 去调一个它没有的模型）。"""
    cfg = settings_store.load_config()
    t = cfg["tiers"].get("vision") or {}
    if t.get("provider") == "tsinghua":
        return {"ok": False, "reason": "清华模型服务只支持文本对话，不能识别图片。"}
    if not t.get("model"):
        return {"ok": False, "reason": "请先在「设置」里给「视觉解析档」选择一个支持图片输入的模型。"}
    if not (t.get("api_key") and t.get("base_url")):
        return {"ok": False, "reason": "请先在「设置」里为「视觉解析档」单独填写 Base URL 和 API Key（当前文本模型的服务通常不支持图片）。"}
    return {"ok": True, "reason": "", "model": t["model"]}


# ----------------------------------------------------------------- 识别一页
def render_page(path: Path, page_no: int, max_side: int = MAX_SIDE) -> bytes:
    with pymupdf.open(path) as doc:
        page = doc[page_no - 1]
        zoom = max(1.0, min(3.0, max_side / max(page.rect.width, page.rect.height)))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")


def clean_output(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^```(?:markdown|md|latex)?\s*\n(.*?)\n```$", t, re.S)  # 模型常把整页包在代码块里
    if m:
        t = m.group(1).strip()
    if t.upper().strip(".。 ") == "EMPTY":
        return ""
    return t


async def recognize(png: bytes) -> str:
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    msgs = [{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                          {"type": "image_url", "image_url": {"url": url}}]}]
    out = await llm.chat("vision", msgs, temperature=0, idle_timeout=PAGE_TIMEOUT)
    return clean_output(out)


# ----------------------------------------------------------------- 文字块
def split_blocks(text: str) -> list[tuple[str, str]]:
    """识别结果 → [(kind, text)]。空行分段；$$...$$ 公式整体保留不拆；# 开头的当标题，- 开头的当列表。"""
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    in_math = False

    def flush():
        nonlocal buf
        body = "\n".join(buf).strip()
        buf = []
        if not body:
            return
        h = re.match(r"^(#{1,6})\s+(.*)$", body, re.S)
        if h and "\n" not in body:
            out.append((f"h{min(len(h.group(1)), 3)}", h.group(2).strip()))
        elif re.match(r"^\s*([-*•]|\d{1,2}[.)])\s+", body) and "\n" not in body:
            out.append(("li", body))
        else:
            out.append(("p", body))

    for line in text.splitlines():
        if line.count("$$") % 2 == 1:
            in_math = not in_math
        if not line.strip() and not in_math:
            flush()
        else:
            buf.append(line)
    flush()
    return out


def ocr_block_dicts(s: Session, document_id: int) -> list[dict]:
    rows = s.exec(select(OcrPage).where(OcrPage.document_id == document_id).order_by(OcrPage.page)).all()
    out = []
    for r in rows:
        for kind, text in split_blocks(r.text):
            out.append({"page": r.page, "kind": kind, "text": text, "meta": {"ocr": True}})
    return out


def ocr_texts(s: Session, document_id: int) -> dict[int, str]:
    return {r.page: r.text for r in s.exec(select(OcrPage).where(OcrPage.document_id == document_id)).all()}


def sync_blocks(s: Session, doc: Document) -> None:
    """识别结果更新后，把它并进已有的文字块（原有文字页的块和译文都不动），再按页码重排顺序号。"""
    existing = list(s.exec(select(DocBlock).where(DocBlock.document_id == doc.id)).all())
    if not existing:
        return  # 还没解析过，之后 ensure_blocks 会自己并入
    for b in existing:
        if '"ocr"' in (b.meta_json or ""):
            s.delete(b)
    s.flush()
    for d in ocr_block_dicts(s, doc.id):
        s.add(DocBlock(document_id=doc.id, page=d["page"], idx=0, kind=d["kind"], text=d["text"],
                       meta_json=json.dumps(d["meta"])))
    s.flush()
    rows = list(s.exec(select(DocBlock).where(DocBlock.document_id == doc.id)).all())
    rows.sort(key=lambda b: (b.page, 0 if '"ocr"' not in (b.meta_json or "") else 1, b.idx, b.id or 0))
    for i, b in enumerate(rows):
        b.idx = i
        s.add(b)


# ----------------------------------------------------------------- 待识别的页
def pending_pages(s: Session, doc: Document) -> list[int]:
    """没有文字层、也还没识别过的页。"""
    empty = [int(x) for x in (doc.empty_pages or "").split(",") if x.strip().isdigit()]
    done = set(ocr_texts(s, doc.id))
    return [p for p in empty if p not in done]


def text_layer_empty_pages(path: Path) -> list[int]:
    with pymupdf.open(path) as d:
        return [i for i, pg in enumerate(d, start=1) if len((pg.get_text("text") or "").strip()) < 20]


# ----------------------------------------------------------------- 后台任务
@dataclass
class OcrJob:
    document_id: int
    state: str = "running"  # running / done / error / cancelled
    total: int = 0
    done: int = 0
    failed: list[int] = field(default_factory=list)
    error: str = ""
    task: asyncio.Task | None = None


JOBS: dict[int, OcrJob] = {}


def start_ocr(document_id: int, pages: list[int] | None = None) -> OcrJob:
    old = JOBS.get(document_id)
    if old and old.state == "running":
        return old
    st = vision_status()
    if not st["ok"]:
        raise OcrError(st["reason"])
    with Session(engine) as s:
        doc = s.get(Document, document_id)
        if not doc or doc.filetype != "pdf":
            raise OcrError("只有 PDF 需要文字识别")
        todo = pending_pages(s, doc)
    if pages:
        todo = [p for p in todo if p in set(pages)]
    if not todo:
        raise OcrError("没有需要识别的页")
    job = OcrJob(document_id=document_id, total=len(todo))
    JOBS[document_id] = job
    job.task = asyncio.create_task(_run(job, todo), name=f"ocr-{document_id}")
    return job


def cancel_ocr(document_id: int) -> bool:
    j = JOBS.get(document_id)
    if j and j.state == "running" and j.task:
        j.task.cancel()
        return True
    return False


async def _run(job: OcrJob, pages: list[int]) -> None:
    from .cloud import ensure_local

    try:
        with Session(engine) as s:
            doc = s.get(Document, job.document_id)
            path = await asyncio.to_thread(ensure_local, doc)
        model = settings_store.resolve_tier("vision")["model"]
        sem = asyncio.Semaphore(CONCURRENCY)
        fatal: list[str] = []

        async def one(p: int):
            async with sem:
                if fatal:
                    return
                try:
                    png = await asyncio.to_thread(render_page, path, p)
                    text = await recognize(png)
                except llm.LLMNotConfigured as e:
                    fatal.append(str(e))
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001  单页失败不影响其他页，最后汇总
                    log.warning("OCR 第 %d 页失败：%s", p, e)
                    job.failed.append(p)
                    if len(job.failed) >= 3 and job.done == 0:
                        fatal.append(f"连续失败，请检查视觉模型设置：{str(e)[:120]}")
                    return
                with Session(engine) as s:
                    row = s.exec(select(OcrPage).where(OcrPage.document_id == job.document_id, OcrPage.page == p)).first()
                    row = row or OcrPage(document_id=job.document_id, page=p)
                    row.text, row.model = text, model
                    s.add(row)
                    s.commit()
                job.done += 1

        await asyncio.gather(*(one(p) for p in pages))
        if fatal:
            raise OcrError(fatal[0])
        job.state = "done"
        if job.failed:
            job.error = f"{len(job.failed)} 页识别失败（第 {', '.join(map(str, sorted(job.failed)))} 页），可再点一次重试"
    except asyncio.CancelledError:
        job.state = "cancelled"
        raise
    except Exception as e:  # noqa: BLE001
        job.state, job.error = "error", str(e)[:300]
    finally:
        if job.done:  # 已识别的页照常并入检索和文字块
            await asyncio.to_thread(_apply, job.document_id)


def _apply(document_id: int) -> None:
    from .ingestion import ingest_document

    try:
        ingest_document(document_id)  # 重新分块、建索引（用上识别出的文字）
        with Session(engine) as s:
            doc = s.get(Document, document_id)
            if doc:
                sync_blocks(s, doc)
                s.commit()
    except Exception:  # noqa: BLE001
        log.exception("OCR 结果并入失败")


def status(s: Session, document_id: int) -> dict:
    doc = s.get(Document, document_id)
    done = len(ocr_texts(s, document_id)) if doc else 0
    j = JOBS.get(document_id)
    pend = pending_pages(s, doc) if doc else []
    return {"pending": len(pend), "recognized": done, "vision": vision_status(),
            "job": {"state": j.state, "total": j.total, "done": j.done, "failed": len(j.failed), "error": j.error} if j else None}


async def shutdown() -> None:
    from .translate import cancel_tasks

    await cancel_tasks(j.task for j in list(JOBS.values()))
