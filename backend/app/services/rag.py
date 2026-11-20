"""混合检索：SQLite FTS5（jieba 分词）+ ChromaDB 向量检索（可选），RRF 融合排序。"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

import jieba
from sqlalchemy import text
from sqlmodel import Session, select

from ..db import CHROMA_DIR
from ..models import Chunk, Document
from . import llm
from .settings_store import resolve_tier

log = logging.getLogger(__name__)
jieba.setLogLevel(logging.WARNING)

_STOP = set("的 了 是 在 和 与 及 或 也 就 都 而 及其 一个 这个 那个 什么 怎么 如何 为什么 吗 呢 啊 吧 the a an of to in is are and or for on with what why how".split())


def segment(t: str) -> str:
    return " ".join(w for w in jieba.cut_for_search(t) if w.strip())


def _query_terms(q: str) -> list[str]:
    terms = []
    for w in jieba.cut_for_search(q):
        w = w.strip().lower()
        if not w or w in _STOP or re.fullmatch(r"[\W_]+", w):
            continue
        terms.append(w.replace('"', ""))
    return list(dict.fromkeys(terms))[:24]


# ---------------- 向量库 ----------------
@lru_cache(maxsize=4)
def _collection(model: str):
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(path=str(CHROMA_DIR), settings=Settings(anonymized_telemetry=False))
    import hashlib

    # 按向量模型分集合（不同模型维度不同）；名称须以字母数字结尾
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", model)[:40]
    name = f"chunks_{slug}_{hashlib.md5(model.encode()).hexdigest()[:8]}"
    return client.get_or_create_collection(name=name, embedding_function=None, metadata={"hnsw:space": "cosine"})


def _vector_collection():
    if not llm.embedding_enabled():
        return None
    try:
        return _collection(resolve_tier("embedding")["model"])
    except Exception:
        log.exception("ChromaDB 初始化失败，退回纯关键词检索")
        return None


def index_chunks(s: Session, chunks: list[Chunk]) -> None:
    for c in chunks:
        s.execute(text("INSERT INTO chunk_fts(content, chunk_id, course_id) VALUES (:c, :id, :cid)"),
                  {"c": segment(c.text), "id": c.id, "cid": c.course_id})


def embed_chunks(items: list[tuple[int, int, int, str]]) -> str:
    """items: (chunk_id, course_id, document_id, text)。在数据库事务之外调用，返回错误信息（成功为空）。"""
    col = _vector_collection()
    if col is None or not items:
        return ""
    try:
        vecs = llm.embed_sync([t for *_, t in items])
        col.upsert(ids=[str(i) for i, *_ in items], embeddings=vecs,
                   metadatas=[{"course_id": c, "document_id": d} for _, c, d, _ in items],
                   documents=[t[:200] for *_, t in items])
        return ""
    except Exception as e:  # noqa: BLE001
        log.exception("向量化失败（不影响关键词检索）")
        return f"向量化失败，仅启用关键词检索：{str(e)[:200]}"


def remove_chunks(s: Session, ids: list[int]) -> None:
    """在调用方的事务里删除 FTS 行（避免另开连接与当前写事务抢 SQLite 锁），并删除向量。"""
    if not ids:
        return
    for i in range(0, len(ids), 500):
        part = [int(x) for x in ids[i : i + 500]]
        s.execute(text(f"DELETE FROM chunk_fts WHERE chunk_id IN ({','.join(map(str, part))})"))
    col = _vector_collection()
    if col is not None:
        try:
            col.delete(ids=[str(i) for i in ids])
        except Exception:
            log.exception("删除向量失败")


def _fts(s: Session, q: str, course_id: int | None, k: int) -> list[int]:
    terms = _query_terms(q)
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms)
    sql = "SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH :m"
    params: dict = {"m": match, "k": k}
    if course_id is not None:
        sql += " AND course_id = :cid"
        params["cid"] = course_id
    sql += " ORDER BY bm25(chunk_fts) LIMIT :k"
    try:
        return [int(r[0]) for r in s.execute(text(sql), params).all()]
    except Exception:
        log.exception("FTS 查询失败: %s", match)
        return []


def _vector(q: str, course_id: int | None, k: int) -> list[int]:
    col = _vector_collection()
    if col is None:
        return []
    try:
        if col.count() == 0:
            return []
        vec = llm.embed_sync([q])[0]
        res = col.query(query_embeddings=[vec], n_results=k,
                        where={"course_id": course_id} if course_id is not None else None)
        return [int(i) for i in res["ids"][0]]
    except Exception:
        log.exception("向量检索失败")
        return []


def search(s: Session, q: str, course_id: int | None = None, k: int = 6) -> list[dict]:
    fts_ids = _fts(s, q, course_id, k * 3)
    vec_ids = _vector(q, course_id, k * 3)
    scores: dict[int, float] = {}
    for ranked in (fts_ids, vec_ids):
        for rank, cid in enumerate(ranked):
            scores[cid] = scores.get(cid, 0) + 1.0 / (60 + rank)  # Reciprocal Rank Fusion
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    if not top:
        return []
    rows = s.exec(select(Chunk, Document).join(Document, Document.id == Chunk.document_id)
                  .where(Chunk.id.in_(top))).all()
    by_id = {c.id: (c, d) for c, d in rows}
    out = []
    for cid in top:
        if cid not in by_id:
            continue
        c, d = by_id[cid]
        out.append({
            "chunk_id": c.id, "document_id": d.id, "filename": d.filename, "filetype": d.filetype,
            "course_id": c.course_id, "page": c.page, "text": c.text,
            "score": round(scores[cid], 5), "via": ("fts" if cid in fts_ids else "") + ("+vec" if cid in vec_ids else ""),
        })
    return out
