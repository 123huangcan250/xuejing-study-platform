"""清华云盘（Seafile）分享链接：只读访问、导入为资料、按需取回原文件。

原则
- 只读：只用分享链接本身的权限，不需要、也不保存清华账号。
- 原文件不落地：导入时下载到临时缓存 → 解析出文字块 / 检索索引入库（这是检索、翻译所必需的小文本）；
  原文件只留在缓存里，随时会被清理，应用启动和退出时清空。阅读 / 导出需要原文件时再从云盘取回。
- 安全：只允许访问白名单域名（默认 cloud.tsinghua.edu.cn），重定向的每一跳都检查域名，防止被诱导去访问内网或其他站点；
  提取码只以 Windows DPAPI 密文保存，不进日志、不通过接口返回。
"""
from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from sqlmodel import Session, select

from ..db import DATA_DIR, engine
from ..models import Chunk, CloudSource, Document
from . import rag
from .ingestion import ingest_document
from .tsinghua import redact
from .tsinghua.runtime import SECURE_DIR
from .tsinghua.secure_store import SecureStore, SecureStoreError

log = logging.getLogger("app.cloud")

DEFAULT_HOSTS = {"cloud.tsinghua.edu.cn"}
HOSTS: set[str] = DEFAULT_HOSTS | {h.strip().lower() for h in os.environ.get("ALP_CLOUD_HOSTS", "").split(",") if h.strip()}
SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".md": "md", ".markdown": "md", ".txt": "txt"}
MAX_FILE_BYTES = 300 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_DEPTH = 6
CACHE_DIR = DATA_DIR / "cloud-cache"
CACHE_CAP = 600 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; study-platform/1.0)"

_SHARE_RE = re.compile(r"^https://([A-Za-z0-9.\-]+)(?::\d+)?/(d|f)/([A-Za-z0-9]{8,64})/?$")
_PASSWORD_FORM = re.compile(r"""name=["']password["']""", re.I)
_CSRF_FIELD = re.compile(r"""name=["']csrfmiddlewaretoken["']\s+value=["']([^"']+)""", re.I)


class CloudError(RuntimeError):
    code = "error"


class CloudAuthError(CloudError):
    def __init__(self, msg: str, code: str):
        super().__init__(msg)
        self.code = code  # need_password / wrong_password


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool = False
    size: int = 0
    mtime: str = ""


def parse_share_url(url: str) -> tuple[str, str, str]:
    """返回 (host, 类型 d/f, token)。查询参数和锚点会被忽略；非 https 或不在白名单的域名直接拒绝。"""
    u = url.strip().split("#")[0].split("?")[0]
    m = _SHARE_RE.match(u)
    if not m:
        raise CloudError("这不是有效的云盘分享链接（应形如 https://cloud.tsinghua.edu.cn/d/xxxxxxxx/ 或 /f/xxxxxxxx/）")
    host = m.group(1).lower()
    if host not in HOSTS:
        raise CloudError(f"出于安全考虑，只支持清华云盘（{', '.join(sorted(DEFAULT_HOSTS))}）的分享链接")
    return host, m.group(2), m.group(3)


def _host_ok(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "https" and (p.hostname or "").lower() in HOSTS


# ----------------------------------------------------------------- 提取码存取
def _store() -> SecureStore:
    return SecureStore(SECURE_DIR)


def save_password(source_id: int, password: str) -> None:
    _store().save(f"cloud_{source_id}", {"password": password})
    redact.register(password)


def load_password(source_id: int) -> str | None:
    try:
        v = _store().load(f"cloud_{source_id}")
    except SecureStoreError:
        return None
    pw = (v or {}).get("password")
    if pw:
        redact.register(pw)
    return pw


def delete_password(source_id: int) -> None:
    _store().delete(f"cloud_{source_id}")


# ----------------------------------------------------------------- 客户端
class Share:
    def __init__(self, host: str, share_type: str, token: str, password: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        if host.lower() not in HOSTS:
            raise CloudError("不允许的云盘域名")
        self.host, self.type, self.token, self.password = host.lower(), share_type, token, password
        self.base = f"https://{self.host}"
        self.client = httpx.AsyncClient(base_url=self.base, transport=transport, follow_redirects=False, headers={"User-Agent": UA},
                                        timeout=httpx.Timeout(60, connect=20))
        self._single: Entry | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()

    # -- 访问与提取码 --
    async def _get(self, url: str, **kw) -> httpx.Response:
        try:
            return await self.client.get(url, **kw)
        except httpx.HTTPError as e:
            raise CloudError(f"无法连接云盘（{type(e).__name__}）") from None

    async def _probe(self) -> str:
        """返回 ok / password；链接无效直接抛错。"""
        if self.type == "d":
            r = await self._get(f"/api/v2.1/share-links/{self.token}/dirents/", params={"path": "/"})
            if r.status_code == 200:
                return "ok"
            if r.status_code == 404:
                raise CloudError("分享链接不存在或已失效")
            if r.status_code in (401, 403):
                return "password"
            raise CloudError(f"云盘返回了意外的响应（HTTP {r.status_code}）")
        # 单文件分享：看 ?dl=1 是重定向（可下载）还是网页（需要提取码 / 已失效）
        r = await self._open(f"/f/{self.token}/?dl=1", stream_ok=True)
        try:
            ctype = r.headers.get("content-type", "")
            if r.status_code == 404:
                raise CloudError("分享链接不存在或已失效")
            if "text/html" in ctype:
                body = (await r.aread())[:200_000].decode("utf-8", "ignore")
                if _PASSWORD_FORM.search(body):
                    return "password"
                raise CloudError("分享链接不存在或已失效")
            self._single = Entry(name=_filename(r), path="/", size=int(r.headers.get("content-length") or 0),
                                 mtime=r.headers.get("last-modified", ""))
            return "ok"
        finally:
            await r.aclose()

    async def authenticate(self) -> None:
        if await self._probe() == "ok":
            return
        if not self.password:
            raise CloudAuthError("这个分享链接需要提取码", "need_password")
        await self._submit_password()
        if await self._probe() != "ok":
            raise CloudAuthError("提取码不正确", "wrong_password")

    async def _submit_password(self) -> None:
        page = f"/{self.type}/{self.token}/"
        r = await self._get(page)
        csrf = self.client.cookies.get("sfcsrftoken") or (_CSRF_FIELD.search(r.text) or [None, None])[1]
        data = {"token": self.token, "password": self.password}
        if csrf:
            data["csrfmiddlewaretoken"] = csrf
        try:
            await self.client.post(page, data=data, headers={"Referer": self.base + page})
        except httpx.HTTPError as e:
            raise CloudError(f"无法连接云盘（{type(e).__name__}）") from None

    # -- 列目录 --
    async def _list_dir(self, path: str) -> list[Entry]:
        r = await self._get(f"/api/v2.1/share-links/{self.token}/dirents/", params={"path": path})
        if r.status_code in (401, 403):
            raise CloudAuthError("需要提取码，或提取码已失效", "need_password")
        if r.status_code != 200:
            raise CloudError(f"读取目录失败（HTTP {r.status_code}）")
        data = r.json()
        items = data.get("dirent_list") or data.get("dirents") or data.get("items") or [] if isinstance(data, dict) else data
        out = []
        for it in items:
            name = it.get("obj_name") or it.get("name") or ""
            if not name:
                continue
            out.append(Entry(name=name, path=posixpath.join(path, name), is_dir=bool(it.get("is_dir")) or it.get("type") == "dir",
                             size=int(it.get("size") or 0), mtime=str(it.get("last_modified") or it.get("mtime") or "")))
        return out

    async def walk(self) -> tuple[list[Entry], int]:
        """返回 (可导入的文件, 被跳过的不支持文件数)。文件夹分享会递归，限制深度和总数。"""
        if self.type == "f":
            e = self._single or Entry(name="file", path="/")
            return ([e] if Path(e.name).suffix.lower() in SUPPORTED else []), (0 if Path(e.name).suffix.lower() in SUPPORTED else 1)
        files, skipped, queue = [], 0, [("/", 0)]
        while queue and len(files) < MAX_ENTRIES:
            path, depth = queue.pop(0)
            for e in await self._list_dir(path):
                if e.name.startswith("."):
                    continue
                if e.is_dir:
                    if depth < MAX_DEPTH:
                        queue.append((e.path, depth + 1))
                elif Path(e.name).suffix.lower() in SUPPORTED:
                    files.append(e)
                else:
                    skipped += 1
        return sorted(files, key=lambda x: x.path), skipped

    # -- 下载 --
    async def _open(self, url: str, stream_ok: bool = False) -> httpx.Response:
        """手动跟随重定向，每一跳都检查域名（防 SSRF）。"""
        for _ in range(6):
            req = self.client.build_request("GET", url)
            try:
                r = await self.client.send(req, stream=True)
            except httpx.HTTPError as e:
                raise CloudError(f"无法连接云盘（{type(e).__name__}）") from None
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                nxt = urljoin(str(r.url), r.headers["location"])
                await r.aclose()
                if not _host_ok(nxt):
                    raise CloudError("云盘返回了指向其他站点的重定向，已拒绝")
                url = nxt
                if nxt.startswith(self.base):
                    url = nxt[len(self.base):]
                if stream_ok and "/seafhttp/" in nxt:  # 探测单文件分享：拿到下载地址就够了，不用真的下载
                    r2 = self.client.build_request("HEAD", url)
                    return await self.client.send(r2, stream=True)
                continue
            return r
        raise CloudError("云盘重定向次数过多")

    def _download_url(self, path: str) -> str:
        return f"/f/{self.token}/?dl=1" if self.type == "f" else f"/d/{self.token}/files/?p={quote(path)}&dl=1"

    async def download_to(self, path: str, dest: Path, progress=None) -> int:
        r = await self._open(self._download_url(path))
        try:
            if r.status_code != 200:
                raise CloudError(f"下载失败（HTTP {r.status_code}）")
            if "text/html" in r.headers.get("content-type", ""):
                raise CloudAuthError("下载被拒绝，可能需要提取码或链接已失效", "need_password")
            total = int(r.headers.get("content-length") or 0)
            if total > MAX_FILE_BYTES:
                raise CloudError(f"文件太大（{total // 1048576} MB，上限 {MAX_FILE_BYTES // 1048576} MB）")
            dest.parent.mkdir(parents=True, exist_ok=True)
            n = 0
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes(65536):
                    n += len(chunk)
                    if n > MAX_FILE_BYTES:
                        raise CloudError("文件超过大小上限")
                    f.write(chunk)
                    if progress:
                        progress(n, total)
            return n
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        finally:
            await r.aclose()


def _filename(r: httpx.Response) -> str:
    cd = r.headers.get("content-disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if m:
        return unquote(m.group(1)).strip()
    return unquote(posixpath.basename(urlparse(str(r.url)).path)) or "file"


# ----------------------------------------------------------------- 本地临时缓存
_locks: dict[int, threading.Lock] = {}
TRANSPORT: httpx.AsyncBaseTransport | None = None  # 测试时注入仿真服务器


def cache_path(doc_id: int, filename: str) -> Path:
    return CACHE_DIR / f"doc{doc_id}{Path(filename).suffix.lower()}"


def prune_cache(cap: int = CACHE_CAP) -> None:
    files = sorted((p for p in CACHE_DIR.glob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    while files and total > cap:
        p = files.pop(0)
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def clear_cache() -> None:
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def open_share(src: CloudSource) -> Share:
    return Share(src.host, src.share_type, src.token, load_password(src.id) if src.has_password else None, TRANSPORT)


def run_sync(coro_factory):
    """在同步代码里跑协程。如果当前线程已经有事件循环在跑（例如在 async 接口里间接调用），就换一个新线程去跑。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro_factory())).result()


def ensure_local(doc: Document) -> Path:
    """需要原文件时调用（阅读、导出、重新解析）。本地资料原样返回路径；云盘资料不在缓存里就取回。
    同步函数，在线程池里（FastAPI 的同步接口 / 后台任务）调用；不要在事件循环线程里调用。"""
    p = Path(doc.path)
    if doc.source != "cloud" or p.exists():
        if p.exists():
            os.utime(p)
        return p
    lock = _locks.setdefault(doc.id, threading.Lock())
    with lock:
        if p.exists():
            return p

        async def fetch():
            with Session(engine) as s:
                src = s.get(CloudSource, doc.source_id) if doc.source_id else None
            if not src:
                raise CloudError("这份资料的云盘来源已被删除，无法取回原文件")
            async with open_share(src) as share:
                await share.authenticate()
                await share.download_to(doc.source_path, p)

        run_sync(fetch)
        prune_cache()
    return p


# ----------------------------------------------------------------- 导入任务
@dataclass
class ImportJob:
    source_id: int
    state: str = "running"  # running / done / error / cancelled
    total: int = 0
    done: int = 0
    current: str = ""
    imported: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)
    error: str = ""
    task: asyncio.Task | None = None
    started: float = field(default_factory=time.time)


JOBS: dict[int, ImportJob] = {}


def status(source_id: int) -> dict:
    j = JOBS.get(source_id)
    if not j:
        return {"state": "idle", "total": 0, "done": 0, "current": "", "imported": 0, "skipped": 0, "errors": [], "error": ""}
    return {"state": j.state, "total": j.total, "done": j.done, "current": j.current, "imported": j.imported, "skipped": j.skipped,
            "errors": j.errors, "error": j.error}


def delete_document_rows(s: Session, doc: Document) -> None:
    rag.remove_chunks(s, [c.id for c in s.exec(select(Chunk).where(Chunk.document_id == doc.id)).all()])
    Path(doc.path).unlink(missing_ok=True)
    s.delete(doc)


def start_import(source_id: int, entries: list[Entry], kind: str, replace: bool = False) -> ImportJob:
    old = JOBS.get(source_id)
    if old and old.state == "running":
        return old
    job = ImportJob(source_id=source_id, total=len(entries))
    JOBS[source_id] = job
    job.task = asyncio.create_task(_run(job, entries, kind, replace), name=f"cloud-import-{source_id}")
    return job


async def _run(job: ImportJob, entries: list[Entry], kind: str, replace: bool) -> None:
    try:
        with Session(engine) as s:
            src = s.get(CloudSource, job.source_id)
        async with open_share(src) as share:
            await share.authenticate()
            for e in entries:
                job.current = e.name
                try:
                    r = await _import_one(src, share, e, kind, replace)
                    job.imported += r == "imported"
                    job.skipped += r == "skipped"
                except CloudAuthError as ex:
                    job.state, job.error = "error", str(ex)
                    return
                except CloudError as ex:
                    job.errors.append({"name": e.name, "error": str(ex)})
                except Exception as ex:  # noqa: BLE001
                    log.exception("导入失败")
                    job.errors.append({"name": e.name, "error": f"{type(ex).__name__}"})
                job.done += 1
        with Session(engine) as s:
            src = s.get(CloudSource, job.source_id)
            if src:
                src.last_synced = datetime.now()
                s.add(src)
                s.commit()
        job.state = "done"
    except CloudError as e:
        job.state, job.error = "error", str(e)
    except asyncio.CancelledError:
        job.state = "cancelled"
    except Exception as e:  # noqa: BLE001
        log.exception("云盘导入出错")
        job.state, job.error = "error", f"导入出错：{type(e).__name__}"
    finally:
        job.current = ""
        prune_cache()


async def _import_one(src: CloudSource, share: Share, e: Entry, kind: str, replace: bool) -> str:
    with Session(engine) as s:
        old = s.exec(select(Document).where(Document.source_id == src.id, Document.source_path == e.path)).first()
        if old:
            unchanged = old.source_size == e.size and old.source_mtime == e.mtime
            if unchanged and not replace:
                return "skipped"
            if not replace and not unchanged:
                return "skipped"  # 有更新但用户没要求替换：不动已有资料（以及它上面的译文、梳理等）
            delete_document_rows(s, old)
            s.commit()
    tmp = CACHE_DIR / f"tmp_{uuid.uuid4().hex}{Path(e.name).suffix.lower()}"
    size = await share.download_to(e.path, tmp)
    with Session(engine) as s:
        doc = Document(course_id=src.course_id, filename=e.name, filetype=SUPPORTED[Path(e.name).suffix.lower()], path=str(tmp),
                       kind=kind, source="cloud", source_id=src.id, source_path=e.path, source_size=e.size or size, source_mtime=e.mtime)
        s.add(doc)
        s.commit()
        s.refresh(doc)
        final = cache_path(doc.id, e.name)
        tmp.replace(final)
        doc.path = str(final)
        s.add(doc)
        s.commit()
        did = doc.id
    await asyncio.to_thread(ingest_document, did)  # 解析 + 分块 + 建索引（同步重活，放线程里）
    with Session(engine) as s:
        d = s.get(Document, did)
        if d.status != "ready":
            msg = d.error or "解析失败"
            delete_document_rows(s, d)
            s.commit()
            raise CloudError(f"{e.name}：{msg}")
    return "imported"


async def shutdown() -> None:
    from .translate import cancel_tasks

    await cancel_tasks(j.task for j in list(JOBS.values()))
