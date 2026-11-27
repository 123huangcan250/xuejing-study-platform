"""数据库自动备份。"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="alp-test-bk-"))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import backup  # noqa: E402


def _clear():
    for p in backup.backup_dir().glob("*"):
        p.unlink()


def test_backup_contains_data_and_wal_content():
    with TestClient(app, base_url="http://localhost") as c:
        _clear()
        r = c.post("/api/courses", json={"name": "备份测试课"})
        assert r.status_code in (200, 201), r.text
        info = c.post("/api/backups").json()
        path = backup.backup_dir() / info["name"]
        con = sqlite3.connect(str(path))
        try:
            names = [row[0] for row in con.execute("SELECT name FROM course")]
            assert "备份测试课" in names
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()
        assert [b["name"] for b in c.get("/api/backups").json()] == [info["name"]]
        assert not list(backup.backup_dir().glob("*.tmp"))


def test_prune_keeps_latest_only():
    _clear()
    d = backup.backup_dir()
    for i in range(5):
        (d / f"app-2020010{i}-000000.db").write_bytes(b"x")
    backup._prune(2)
    assert sorted(p.name for p in d.glob("app-*.db")) == ["app-20200103-000000.db", "app-20200104-000000.db"]


def test_due_logic():
    _clear()
    assert backup.due()  # 没有备份 → 该备份
    backup.make_backup()
    assert not backup.due()
    assert backup.backup_if_due() is None
    assert backup.due(datetime.now() + timedelta(hours=21))
    assert not backup.due(datetime.now() + timedelta(hours=10))


def test_startup_makes_backup_when_due(monkeypatch):
    _clear()
    monkeypatch.delenv("ALP_DISABLE_BACKUP", raising=False)
    with TestClient(app):
        import time

        for _ in range(50):  # 备份在线程里做，稍等
            if backup.list_backups():
                break
            time.sleep(0.1)
        assert len(backup.list_backups()) == 1
    assert db.DB_PATH.exists()
