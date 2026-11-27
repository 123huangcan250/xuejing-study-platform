"""资料解析与分块：PDF 按页提取文本（保留页码），Markdown/TXT 按段落切块。"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf
from sqlmodel import Session, select

from ..db import engine
from ..models import Chunk, Document
from . import rag
from .blockstore import ensure_blocks

log = logging.getLogger(__name__)

CHUNK_SIZE = 700
OVERLAP = 120


def _split(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        while len(p) > CHUNK_SIZE:  # 超长段落硬切
            head, p = p[:CHUNK_SIZE], p[CHUNK_SIZE - OVERLAP :]
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(head)
        if len(buf) + len(p) + 2 <= CHUNK_SIZE:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            tail = buf[-OVERLAP:] if buf else ""
            buf = f"{tail}\n{p}" if tail else p
    if buf:
        chunks.append(buf)
    return chunks


def parse_pdf(path: Path) -> tuple[list[tuple[int, str]], int, list[int]]:
    pages: list[tuple[int, str]] = []
    empty: list[int] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            if len(text.strip()) < 20:
                empty.append(i)
            pages.append((i, text))
        return pages, doc.page_count, empty


def ingest_document(document_id: int) -> None:
    """后台任务：解析 → 分块 → 写入 FTS（以及可选的向量库）。"""
    with Session(engine) as s:
        doc = s.get(Document, document_id)
        if not doc:
            return
        try:
            from .cloud import ensure_local  # 云盘资料：缓存里没有就先取回

            path = ensure_local(doc)
            if doc.filetype == "pdf":
                pages, count, empty = parse_pdf(path)
                from .ocr import ocr_texts

                ocr = ocr_texts(s, doc.id)  # 扫描页若已识别过，用识别出的文字代替空文本层
                if ocr:
                    pages = [(n, ocr[n] if n in ocr and len(t.strip()) < 20 else t) for n, t in pages]
                    empty = [n for n in empty if n not in ocr]
                doc.page_count = count
                doc.empty_pages = ",".join(map(str, empty))
            elif doc.filetype in ("docx", "pptx"):
                # 先拆成文字块（翻译、对照阅读也用它），再按页（幻灯片 / 虚拟页）合并成检索用的文本
                rows = ensure_blocks(s, doc, reparse=True)
                by_page: dict[int, list[str]] = {}
                for r in rows:
                    by_page.setdefault(r.page, []).append(r.text)
                pages = [(p, "\n".join(t)) for p, t in sorted(by_page.items())]
                doc.page_count = max(by_page) if by_page else 0
            else:
                raw = path.read_bytes()
                for enc in ("utf-8", "gb18030"):
                    try:
                        content = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    content = raw.decode("utf-8", errors="ignore")
                pages = [(1, content)]
                doc.page_count = 1

            # 重新解析时先清理旧分块
            old = s.exec(select(Chunk).where(Chunk.document_id == doc.id)).all()
            rag.remove_chunks(s, [c.id for c in old])
            for c in old:
                s.delete(c)
            s.flush()

            chunks: list[Chunk] = []
            idx = 0
            for page_no, text in pages:
                for piece in _split(text):
                    chunks.append(Chunk(document_id=doc.id, course_id=doc.course_id, page=page_no, idx=idx, text=piece))
                    idx += 1
            s.add_all(chunks)
            s.flush()
            rag.index_chunks(s, chunks)
            doc.chunk_count = len(chunks)
            doc.status = "ready"
            doc.error = ""
            if doc.filetype == "pdf" and doc.page_count and len(chunks) == 0:
                doc.error = "未提取到文本，可能是扫描版 PDF：在资料库里点「识别文字」，用视觉模型识别"
            s.add(doc)
            items = [(c.id, c.course_id, c.document_id, c.text) for c in chunks]
            s.commit()
            err = rag.embed_chunks(items)  # 网络调用放在事务之外
            if err:
                doc = s.get(Document, document_id)
                if doc and not doc.error:
                    doc.error = err
                    s.add(doc)
                    s.commit()
        except Exception as e:  # noqa: BLE001
            log.exception("解析失败")
            s.rollback()
            doc = s.get(Document, document_id)
            if doc:
                doc.status = "error"
                doc.error = str(e)[:500]
                s.add(doc)
                s.commit()
