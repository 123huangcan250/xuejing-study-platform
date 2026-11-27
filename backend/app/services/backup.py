"""数据库自动备份：用 SQLite 在线备份接口（不用停服务、WAL 里的内容也一并带上）。

- 启动时若最近一份备份超过 20 小时，立刻备份一次；之后每小时检查一次，满 20 小时再备份。
- 只留最近 KEEP 份，旧的自动删除；备份放在 data/backups/，已被 .gitignore 排除。
- 备份的是 app.db（课程/切块/译文/知识图谱/闪卡/学习记录/设置）。上传的原文件在 data/uploads/，
  体积大且本来就是你电脑里的原件，不在自动备份里。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from .. import db

log = logging.getLogger("backup")

KEEP = 10
MIN_INTERVAL_H = 20
_PREFIX = "app-"
_task: asyncio.Task | None = None


def backup_dir() -> Path:
    d = db.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_backups() -> list[dict]:
    out = []
    for p in sorted(backup_dir().glob(f"{_PREFIX}*.db"), reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size,
                    "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    return out


def _prune(keep: int) -> None:
    for p in sorted(backup_dir().glob(f"{_PREFIX}*.db"), reverse=True)[keep:]:
        try:
            p.unlink()
        except OSError as e:  # 被占用就下次再删
            log.warning("旧备份删除失败 %s: %s", p.name, e)


def make_backup(keep: int = KEEP) -> dict:
    """备份一次并清理旧备份。先写 .tmp 再改名，写到一半断电也不会留下半份“备份”。"""
    name = f"{_PREFIX}{datetime.now():%Y%m%d-%H%M%S}.db"
    final = backup_dir() / name
    tmp = final.with_suffix(".tmp")
    src = sqlite3.connect(str(db.DB_PATH), timeout=30)
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    ok = sqlite3.connect(str(tmp))
    try:
        res = ok.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        ok.close()
    if res != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"备份校验失败：{res}")
    tmp.replace(final)
    _prune(keep)
    log.info("数据库已备份：%s（%.1f MB）", name, final.stat().st_size / 1048576)
    return {"name": name, "size": final.stat().st_size}


def due(now: datetime | None = None) -> bool:
    items = list_backups()
    if not items:
        return True
    last = datetime.fromisoformat(items[0]["created_at"])
    return ((now or datetime.now()) - last).total_seconds() >= MIN_INTERVAL_H * 3600


def backup_if_due() -> dict | None:
    return make_backup() if due() else None


async def _loop() -> None:
    while True:
        try:
            await asyncio.to_thread(backup_if_due)
        except Exception as e:  # 备份失败不能拖垮主程序
            log.warning("自动备份失败：%s", e)
        await asyncio.sleep(3600)


def start() -> None:
    global _task
    loop = asyncio.get_running_loop()
    # 旧任务属于已经结束的事件循环（例如测试里先后创建多个 TestClient）时，它永远不会再运行，直接换新的
    if _task is None or _task.done() or _task.get_loop() is not loop:
        _task = loop.create_task(_loop())


async def shutdown() -> None:
    global _task
    t, _task = _task, None
    if t is None or t.done():
        return
    t.cancel()
    if t.get_loop() is asyncio.get_running_loop():  # 别的事件循环里的任务没法在这里等待
        await asyncio.gather(t, return_exceptions=True)
