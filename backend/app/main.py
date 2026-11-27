"""FastAPI 入口。启动：uvicorn app.main:app --port 8400"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .access_mw import AccessMiddleware
from .api import auth, canvas, cloud, courses, flashcards, knowledge, ocr, questions, sprint, system, translation, tsinghua
from .db import init_db
from .services import backup as backup_svc
from .services import cloud as cloud_svc
from .services import exam as exam_svc
from .services import knowledge as knowledge_svc
from .services import ocr as ocr_svc
from .services import questions as questions_svc
from .services import simulate as simulate_svc
from .services import translate as translate_svc
from .services.tsinghua import redact, runtime as tsinghua_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
redact.install()  # 已知的 token / 密码在任何日志里都会被替换成 ***


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    cloud_svc.clear_cache()  # 云盘资料的原文件只放在临时缓存里，每次启动都清空
    redact.install()  # uvicorn 的 handler 在此时已就绪，再装一次
    if os.environ.get("ALP_DISABLE_BACKUP") != "1":
        backup_svc.start()  # 启动时若超过 20 小时没备份就立刻备份一次，之后每小时检查
    if os.environ.get("ALP_DISABLE_TSINGHUA") != "1":
        await tsinghua_runtime.startup()
    try:
        yield
    finally:
        # 项目退出：停止后台更新任务并关闭登录浏览器，不留常驻进程
        await backup_svc.shutdown()
        await translate_svc.shutdown()  # 先停掉进行中的后台任务
        await knowledge_svc.shutdown()
        await exam_svc.shutdown()
        await ocr_svc.shutdown()
        await questions_svc.shutdown()
        await simulate_svc.shutdown()
        await cloud_svc.shutdown()
        cloud_svc.clear_cache()
        await tsinghua_runtime.shutdown()


app = FastAPI(title="AI 课程学习与游戏化复习平台", version="0.2.0", lifespan=lifespan)

origins = os.environ.get("FRONTEND_ORIGINS", "http://localhost:3400,http://127.0.0.1:3400").split(",")
app.add_middleware(AccessMiddleware)  # 先加的在内层；CORS 在最外层，预检请求不受登录限制
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

for r in (system.router, courses.router, canvas.router, flashcards.router, tsinghua.router, translation.router, knowledge.router, cloud.router, auth.router, ocr.router, questions.router, sprint.router):
    app.include_router(r)
