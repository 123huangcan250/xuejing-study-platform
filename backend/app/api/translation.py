"""文档翻译接口：文字块、译文查询、后台翻译任务、划词即时翻译、导出。"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import DocBlock, Document, Translation
from ..services import translate as tr_svc
from ..services.cloud import CloudError, ensure_local
from ..services import translate_export as exp
from ..services.blockstore import ensure_blocks, get_blocks, src_hash
from ..services.docblocks import meta_of

router = APIRouter(prefix="/api", tags=["translation"])

MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "md": "text/markdown; charset=utf-8",
}


def _doc(s: Session, did: int) -> Document:
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    return d


def _valid_translations(s: Session, did: int, blocks: list[DocBlock]) -> dict[int, str]:
    """块 id → 译文（原文变过的块视为过期，不返回）。"""
    h = {b.id: src_hash(b.text) for b in blocks}
    rows = s.exec(select(Translation).where(Translation.document_id == did, Translation.target == tr_svc.TARGET)).all()
    return {t.block_id: t.text for t in rows if h.get(t.block_id) == t.src_hash}


@router.get("/documents/{did}/blocks")
def list_blocks(did: int, s: Session = Depends(get_session)):
    d = _doc(s, did)
    blocks = ensure_blocks(s, d)
    s.commit()
    out = []
    for b in blocks:
        m = meta_of(b)
        out.append({"id": b.id, "page": b.page, "idx": b.idx, "kind": b.kind, "text": b.text, "table": m.get("table")})
    return out


@router.get("/documents/{did}/translation")
def get_translation(did: int, pages: str = "", s: Session = Depends(get_session)):
    d = _doc(s, did)
    status = tr_svc.status(s, did)
    out: dict = {"status": status, "pages": {}}
    want = {int(x) for x in pages.split(",") if x.strip().isdigit()}
    if want:
        blocks = [b for b in get_blocks(s, did) if b.page in want]
        have = _valid_translations(s, did, blocks)
        for p in sorted(want):
            out["pages"][str(p)] = [
                {"id": b.id, "kind": b.kind, "text": b.text, "translatable": tr_svc.needs_translation(b.text),
                 "translation": have.get(b.id)} for b in blocks if b.page == p]
    _ = d
    return out


class TranslateIn(BaseModel):
    pages: list[int] | None = None  # None = 整份文档
    priority: bool = False  # True = 插到队列最前面（用户正在看的页）


@router.post("/documents/{did}/translate")
async def start_translate(did: int, body: TranslateIn, s: Session = Depends(get_session)):
    _doc(s, did)
    if not tr_svc.configured_chain():
        raise HTTPException(400, "还没有配置可用的模型：请在「设置」里配置「翻译」或「轻量快速」档位。")
    tr_svc.start_job(did, body.pages, body.priority)
    return tr_svc.status(s, did)


@router.post("/documents/{did}/translate/cancel")
async def cancel_translate(did: int, s: Session = Depends(get_session)):
    _doc(s, did)
    tr_svc.cancel_job(did)
    return tr_svc.status(s, did)


@router.delete("/documents/{did}/translation")
def clear_translation(did: int, s: Session = Depends(get_session)):
    _doc(s, did)
    tr_svc.cancel_job(did)
    for t in s.exec(select(Translation).where(Translation.document_id == did)).all():
        s.delete(t)
    s.commit()
    tr_svc.JOBS.pop(did, None)
    return {"ok": True}


class TextIn(BaseModel):
    text: str
    context: str = ""


@router.post("/translate/text")
async def translate_selection(body: TextIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "没有要翻译的内容")
    if len(text) > 4000:
        raise HTTPException(400, "一次最多翻译 4000 字，请选短一点")
    try:
        out, model, cached = await tr_svc.translate_text(text, body.context)
    except tr_svc.TranslateError as e:
        raise HTTPException(502, str(e)) from None
    return {"translation": out, "model": model, "cached": cached}


@router.get("/documents/{did}/translation/export")
def export_translation(did: int, mode: str = Query("zh", pattern="^(zh|bilingual)$"),
                       format: str = Query("native", pattern="^(native|md)$"), s: Session = Depends(get_session)):
    d = _doc(s, did)
    blocks = get_blocks(s, did)
    have = _valid_translations(s, did, blocks)
    if not have:
        raise HTTPException(400, "这份资料还没有译文，请先翻译")
    total = sum(1 for b in blocks if tr_svc.needs_translation(b.text))
    stem = Path(d.filename).stem
    try:
        local = ensure_local(d)  # 云盘资料：需要时从云盘取回原文件
    except CloudError as e:
        raise HTTPException(502, str(e)) from None
    headers = {"X-Translated": f"{len(have)}/{total}"}

    def respond(data: bytes | str, ext: str, media: str, suffix: str):
        name = f"{stem}.{suffix}.{ext}"
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
        return Response(content=data.encode("utf-8") if isinstance(data, str) else data, media_type=media, headers=headers)

    if format == "md" or d.filetype in ("md", "txt"):
        return respond(exp.export_markdown(stem, d.filetype, blocks, have), "md", MEDIA["md"], "bilingual")
    by_idx = {b.idx: have[b.id] for b in blocks if b.id in have}
    if d.filetype == "docx":
        return respond(exp.export_docx(local, by_idx, mode), "docx", MEDIA["docx"], "zh" if mode == "zh" else "bilingual")
    if d.filetype == "pptx":
        return respond(exp.export_pptx(local, by_idx, mode), "pptx", MEDIA["pptx"], "zh" if mode == "zh" else "bilingual")
    data, stats = exp.export_pdf(local, blocks, have)
    headers["X-Export-Stats"] = json.dumps(stats)
    return respond(data, "pdf", MEDIA["pdf"], "zh")
