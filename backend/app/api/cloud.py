"""清华云盘分享链接：添加、浏览、导入、管理。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import CloudSource, Course, Document
from ..services import cloud as cl

router = APIRouter(prefix="/api", tags=["cloud"])

KINDS = ("slides", "exam", "reference")


def _err(e: cl.CloudError) -> HTTPException:
    return HTTPException(401 if isinstance(e, cl.CloudAuthError) else 400, str(e))


def _src(s: Session, sid: int) -> CloudSource:
    src = s.get(CloudSource, sid)
    if not src:
        raise HTTPException(404, "云盘来源不存在")
    return src


def _source_out(s: Session, src: CloudSource) -> dict:
    n = len(s.exec(select(Document.id).where(Document.source_id == src.id)).all())
    return {"id": src.id, "course_id": src.course_id, "url": src.url, "name": src.name, "share_type": src.share_type,
            "default_kind": src.default_kind, "has_password": src.has_password, "documents": n,
            "last_synced": src.last_synced.isoformat() if src.last_synced else None, "import": cl.status(src.id)}


def _entries_out(s: Session, src: CloudSource, entries: list[cl.Entry]) -> list[dict]:
    have = {d.source_path: d for d in s.exec(select(Document).where(Document.source_id == src.id)).all()}
    out = []
    for e in entries:
        d = have.get(e.path)
        state = "new" if not d else ("imported" if (d.source_size == e.size and d.source_mtime == e.mtime) else "updated")
        out.append({"name": e.name, "path": e.path, "size": e.size, "mtime": e.mtime, "state": state,
                    "document_id": d.id if d else None, "kind": d.kind if d else None})
    return out


class SourceIn(BaseModel):
    url: str
    password: str | None = None
    name: str = ""
    default_kind: str = "slides"


@router.post("/courses/{cid}/cloud/sources")
async def add_source(cid: int, body: SourceIn, s: Session = Depends(get_session)):
    if not s.get(Course, cid):
        raise HTTPException(404, "课程不存在")
    if body.default_kind not in KINDS:
        raise HTTPException(400, "资料种类只能是 slides / exam / reference")
    try:
        host, typ, token = cl.parse_share_url(body.url)
        url = f"https://{host}/{typ}/{token}/"
        if s.exec(select(CloudSource).where(CloudSource.course_id == cid, CloudSource.token == token)).first():
            raise HTTPException(400, "这个链接已经添加过了")
        pw = (body.password or "").strip() or None
        async with cl.Share(host, typ, token, pw, cl.TRANSPORT) as share:
            await share.authenticate()  # 先确认链接和提取码有效，再保存
            entries, skipped = await share.walk()
    except cl.CloudError as e:
        raise _err(e) from None
    src = CloudSource(course_id=cid, url=url, host=host, token=token, share_type=typ, name=body.name.strip(),
                      default_kind=body.default_kind, has_password=bool(pw))
    s.add(src)
    s.commit()
    s.refresh(src)
    if pw:
        cl.save_password(src.id, pw)
    return {"source": _source_out(s, src), "entries": _entries_out(s, src, entries), "skipped": skipped}


@router.get("/courses/{cid}/cloud/sources")
def list_sources(cid: int, s: Session = Depends(get_session)):
    return [_source_out(s, x) for x in s.exec(select(CloudSource).where(CloudSource.course_id == cid).order_by(CloudSource.created_at)).all()]


@router.get("/cloud/sources/{sid}/entries")
async def source_entries(sid: int, s: Session = Depends(get_session)):
    src = _src(s, sid)
    try:
        async with cl.open_share(src) as share:
            await share.authenticate()
            entries, skipped = await share.walk()
    except cl.CloudError as e:
        raise _err(e) from None
    return {"source": _source_out(s, src), "entries": _entries_out(s, src, entries), "skipped": skipped}


class ImportIn(BaseModel):
    paths: list[str] | None = None  # None = 所有新文件
    kind: str | None = None
    replace: bool = False  # 云盘上有更新的文件：True 才会替换（会丢掉旧资料上的译文、梳理等）


@router.post("/cloud/sources/{sid}/import")
async def import_source(sid: int, body: ImportIn, s: Session = Depends(get_session)):
    src = _src(s, sid)
    kind = body.kind or src.default_kind
    if kind not in KINDS:
        raise HTTPException(400, "资料种类只能是 slides / exam / reference")
    try:
        async with cl.open_share(src) as share:
            await share.authenticate()
            entries, _ = await share.walk()
    except cl.CloudError as e:
        raise _err(e) from None
    flags = {e["path"]: e["state"] for e in _entries_out(s, src, entries)}
    chosen = [e for e in entries if (body.paths is None and flags[e.path] == "new") or (body.paths is not None and e.path in set(body.paths))]
    if not chosen:
        raise HTTPException(400, "没有需要导入的文件")
    cl.start_import(sid, chosen, kind, body.replace)
    return cl.status(sid)


@router.get("/cloud/sources/{sid}/status")
def import_status(sid: int, s: Session = Depends(get_session)):
    _src(s, sid)
    return cl.status(sid)


@router.post("/cloud/sources/{sid}/cancel")
async def cancel_import(sid: int, s: Session = Depends(get_session)):
    _src(s, sid)
    j = cl.JOBS.get(sid)
    if j and j.state == "running" and j.task:
        j.state = "cancelled"
        j.task.cancel()
    return cl.status(sid)


class PasswordIn(BaseModel):
    password: str


@router.put("/cloud/sources/{sid}/password")
async def set_password(sid: int, body: PasswordIn, s: Session = Depends(get_session)):
    """提取码变了（或之前没填）：验证通过后重新加密保存。"""
    src = _src(s, sid)
    pw = body.password.strip()
    if not pw:
        raise HTTPException(400, "提取码不能为空")
    try:
        async with cl.Share(src.host, src.share_type, src.token, pw, cl.TRANSPORT) as share:
            await share.authenticate()
    except cl.CloudError as e:
        raise _err(e) from None
    cl.save_password(sid, pw)
    src.has_password = True
    s.add(src)
    s.commit()
    return _source_out(s, src)


@router.delete("/cloud/sources/{sid}")
def delete_source(sid: int, s: Session = Depends(get_session)):
    """删除来源，同时删除从它导入的资料（没有原文件就无法阅读；云盘里的文件不受影响）。"""
    src = _src(s, sid)
    j = cl.JOBS.get(sid)
    if j and j.state == "running" and j.task:
        j.task.cancel()
    for d in s.exec(select(Document).where(Document.source_id == sid)).all():
        cl.delete_document_rows(s, d)
    s.delete(src)
    s.commit()
    cl.delete_password(sid)
    cl.JOBS.pop(sid, None)
    return {"ok": True}
