"""进程内单例：随 FastAPI 启动/退出。"""
from __future__ import annotations

import logging

from ...db import DATA_DIR
from .auth_worker import PlaywrightAuthWorker
from .manager import TokenManager
from .secure_store import SecureStore

log = logging.getLogger("app.tsinghua")

SECURE_DIR = DATA_DIR / "secure"
_manager: TokenManager | None = None


def get_manager() -> TokenManager:
    global _manager
    if _manager is None:
        _manager = TokenManager(SecureStore(SECURE_DIR), PlaywrightAuthWorker())
    return _manager


def set_manager(m: TokenManager | None) -> None:
    """测试用：注入假的 manager。"""
    global _manager
    _manager = m


async def startup() -> None:
    m = get_manager()
    m.start()  # 只恢复状态并排程；token 仍有效时不会打开浏览器
    log.info("清华模型登录管理已启动：%s", m.status.message)


async def shutdown() -> None:
    if _manager is not None:
        await _manager.stop()
        log.info("清华模型登录管理已停止，浏览器进程已关闭")
