"""SQLite 引擎、会话与 FTS5 全文索引初始化。"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

DATA_DIR = Path(os.environ.get("APP_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
UPLOAD_DIR = DATA_DIR / "uploads"
CHROMA_DIR = DATA_DIR / "chroma"
for _d in (DATA_DIR, UPLOAD_DIR, CHROMA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "app.db"
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def init_db() -> None:
    from . import models  # noqa: F401  注册所有表

    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        # 中文先用 jieba 分词再写入，所以 unicode61 分词器即可
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5("
                "content, chunk_id UNINDEXED, course_id UNINDEXED, tokenize='unicode61')"
            )
        )
        _ensure_columns(conn)
        _ensure_course_columns(conn)


def _ensure_columns(conn) -> None:
    """给已有数据库补新增的列（create_all 不会修改已存在的表）。老资料默认都算“课件”。"""
    cols = {r[1] for r in conn.execute(text("PRAGMA table_info(document)")).all()}
    for name, ddl in [
        ("kind", "VARCHAR NOT NULL DEFAULT 'slides'"),
        ("source", "VARCHAR NOT NULL DEFAULT 'local'"),
        ("source_id", "INTEGER"),
        ("source_path", "VARCHAR NOT NULL DEFAULT ''"),
        ("source_size", "INTEGER NOT NULL DEFAULT 0"),
        ("source_mtime", "VARCHAR NOT NULL DEFAULT ''"),
    ]:
        if name not in cols:
            conn.execute(text(f"ALTER TABLE document ADD COLUMN {name} {ddl}"))


def _ensure_course_columns(conn) -> None:
    cols = {r[1] for r in conn.execute(text("PRAGMA table_info(course)")).all()}
    for name, ddl in [("exam_date", "VARCHAR NOT NULL DEFAULT ''"), ("sprint_minutes", "INTEGER NOT NULL DEFAULT 90")]:
        if name not in cols:
            conn.execute(text(f"ALTER TABLE course ADD COLUMN {name} {ddl}"))


def get_session():
    with Session(engine) as session:
        yield session
