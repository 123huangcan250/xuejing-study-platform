"""文字块的入库：按需解析（PDF/Markdown/TXT 在第一次翻译时才解析，DOCX/PPTX 上传时就解析）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlmodel import Session, select

from ..models import DocBlock, Document
from .docblocks import parse_blocks


def src_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def get_blocks(s: Session, document_id: int) -> list[DocBlock]:
    return list(s.exec(select(DocBlock).where(DocBlock.document_id == document_id).order_by(DocBlock.idx)).all())


def ensure_blocks(s: Session, doc: Document, reparse: bool = False) -> list[DocBlock]:
    existing = get_blocks(s, doc.id)
    if existing and not reparse:
        return existing
    for b in existing:
        s.delete(b)  # 外键级联会一并删除这些块的译文
    s.flush()
    from .cloud import ensure_local

    parsed = parse_blocks(ensure_local(doc), doc.filetype)
    if doc.filetype == "pdf":  # 扫描页的识别结果并进来（没有 bbox），按页码稳定排序
        from .ocr import ocr_block_dicts

        extra = ocr_block_dicts(s, doc.id)
        if extra:
            parsed = sorted(parsed + extra, key=lambda b: b["page"])
    rows = [DocBlock(document_id=doc.id, page=b["page"], idx=i, kind=b["kind"], text=b["text"],
                     meta_json=json.dumps(b["meta"], ensure_ascii=False) if b["meta"] else "")
            for i, b in enumerate(parsed)]
    s.add_all(rows)
    s.flush()
    return rows
