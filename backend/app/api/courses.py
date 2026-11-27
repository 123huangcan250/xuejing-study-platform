"""课程世界与资料库。"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import UPLOAD_DIR, get_session
from ..models import CanvasNode, Chunk, Course, Document, ExamQuestion, Flashcard, KnowledgeNode
from ..services import gamification, rag
from ..services.cloud import CloudError, ensure_local
from ..services.ingestion import ingest_document

router = APIRouter(prefix="/api", tags=["courses"])

KINDS = ("slides", "exam", "reference")  # 课件 / 试卷 / 参考资料
ALLOWED = {".pdf": "pdf", ".md": "md", ".markdown": "md", ".txt": "txt", ".docx": "docx", ".pptx": "pptx"}


class CourseIn(BaseModel):
    name: str
    description: str = ""
    emoji: str = "📘"
    color: str = "#6366f1"


class CoursePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    emoji: str | None = None
    color: str | None = None
    exam_date: str | None = None  # "" 表示清除
    sprint_minutes: int | None = None


def _course_stats(s: Session, cid: int) -> dict:
    from datetime import datetime

    def cnt(model, *conds):
        return s.exec(select(func.count()).select_from(model).where(*conds)).one()

    return {
        "documents": cnt(Document, Document.course_id == cid),
        "nodes": cnt(CanvasNode, CanvasNode.course_id == cid),
        "flashcards": cnt(Flashcard, Flashcard.course_id == cid),
        "due": cnt(Flashcard, Flashcard.course_id == cid, Flashcard.due <= datetime.now()),
    }


@router.get("/courses")
def list_courses(s: Session = Depends(get_session)):
    courses = s.exec(select(Course).order_by(Course.created_at.desc())).all()
    return [c.model_dump() | {"stats": _course_stats(s, c.id)} for c in courses]


@router.post("/courses")
def create_course(body: CourseIn, s: Session = Depends(get_session)):
    if not body.name.strip():
        raise HTTPException(400, "课程名不能为空")
    c = Course(**body.model_dump())
    s.add(c)
    s.commit()
    s.refresh(c)
    return c.model_dump() | {"stats": _course_stats(s, c.id)}


@router.get("/courses/{cid}")
def get_course(cid: int, s: Session = Depends(get_session)):
    c = s.get(Course, cid)
    if not c:
        raise HTTPException(404, "课程不存在")
    return c.model_dump() | {"stats": _course_stats(s, cid)}


@router.patch("/courses/{cid}")
def patch_course(cid: int, body: CoursePatch, s: Session = Depends(get_session)):
    c = s.get(Course, cid)
    if not c:
        raise HTTPException(404, "课程不存在")
    data = body.model_dump(exclude_none=True)
    if "exam_date" in data and data["exam_date"]:
        from datetime import date

        try:
            date.fromisoformat(data["exam_date"])
        except ValueError:
            raise HTTPException(400, "考试日期格式应为 YYYY-MM-DD") from None
    if "sprint_minutes" in data:
        data["sprint_minutes"] = max(20, min(600, data["sprint_minutes"]))
    for k, v in data.items():
        setattr(c, k, v)
    s.add(c)
    s.commit()
    s.refresh(c)
    return c


@router.delete("/courses/{cid}")
def delete_course(cid: int, s: Session = Depends(get_session)):
    c = s.get(Course, cid)
    if not c:
        raise HTTPException(404, "课程不存在")
    chunk_ids = list(s.exec(select(Chunk.id).where(Chunk.course_id == cid)).all())
    paths = list(s.exec(select(Document.path).where(Document.course_id == cid)).all())
    rag.remove_chunks(s, chunk_ids)
    s.delete(c)  # 外键 ON DELETE CASCADE 级联删除资料、分块、画布、闪卡
    s.commit()
    for p in paths:
        Path(p).unlink(missing_ok=True)
    return {"ok": True}


# ---------------- 资料 ----------------
@router.get("/courses/{cid}/documents")
def list_documents(cid: int, s: Session = Depends(get_session)):
    return s.exec(select(Document).where(Document.course_id == cid).order_by(Document.created_at)).all()


@router.post("/courses/{cid}/documents")
def upload_document(cid: int, file: UploadFile, bg: BackgroundTasks, kind: str = Form("slides"),
                    s: Session = Depends(get_session)):
    if kind not in KINDS:
        raise HTTPException(400, "资料种类只能是 slides / exam / reference")
    if not s.get(Course, cid):
        raise HTTPException(404, "课程不存在")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"暂不支持 {ext or '该'} 格式，目前支持 PDF / Word(.docx) / PPT(.pptx) / Markdown / TXT")
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    doc = Document(course_id=cid, filename=file.filename or dest.name, filetype=ALLOWED[ext], path=str(dest), kind=kind)
    s.add(doc)
    s.commit()
    s.refresh(doc)
    out = doc.model_dump()
    reward = gamification.emit(s, "DOC_UPLOADED", cid, {"document_id": out["id"]})
    bg.add_task(ingest_document, out["id"])
    return {"document": out, "reward": reward}


@router.get("/documents/{did}")
def get_document(did: int, s: Session = Depends(get_session)):
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    return d


class DocPatch(BaseModel):
    kind: str


@router.patch("/documents/{did}")
def patch_document(did: int, body: DocPatch, s: Session = Depends(get_session)):
    """修改资料种类。种类变了，原先按旧种类生成的东西（知识梳理 / 试题）要清掉，否则会残留。"""
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    if body.kind not in KINDS:
        raise HTTPException(400, "资料种类只能是 slides / exam / reference")
    if body.kind != d.kind:
        if d.kind == "slides":
            for n in s.exec(select(KnowledgeNode).where(KnowledgeNode.document_id == did)).all():
                s.delete(n)
        if d.kind == "exam":
            for q in s.exec(select(ExamQuestion).where(ExamQuestion.document_id == did)).all():
                s.delete(q)
        d.kind = body.kind
        s.add(d)
        s.commit()
        s.refresh(d)
    return d


@router.get("/documents/{did}/file")
def document_file(did: int, s: Session = Depends(get_session)):
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "文件不存在")
    try:
        local = ensure_local(d)
    except CloudError as e:
        raise HTTPException(502, str(e)) from None
    if not local.exists():
        raise HTTPException(404, "文件不存在")
    media = {"pdf": "application/pdf", "md": "text/markdown; charset=utf-8", "txt": "text/plain; charset=utf-8",
             "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}[d.filetype]
    return FileResponse(local, media_type=media)


@router.get("/documents/{did}/text")
def document_text(did: int, s: Session = Depends(get_session)):
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    if d.filetype not in ("md", "txt"):
        raise HTTPException(400, "该格式请使用 /file 或 /blocks")
    try:
        raw = ensure_local(d).read_bytes()
    except CloudError as e:
        raise HTTPException(502, str(e)) from None
    for enc in ("utf-8", "gb18030"):
        try:
            return {"text": raw.decode(enc)}
        except UnicodeDecodeError:
            continue
    return {"text": raw.decode("utf-8", errors="ignore")}


@router.post("/documents/{did}/reindex")
def reindex_document(did: int, bg: BackgroundTasks, s: Session = Depends(get_session)):
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    d.status = "processing"
    d.error = ""
    s.add(d)
    s.commit()
    bg.add_task(ingest_document, did)
    return {"ok": True}


@router.delete("/documents/{did}")
def delete_document(did: int, s: Session = Depends(get_session)):
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    rag.remove_chunks(s, list(s.exec(select(Chunk.id).where(Chunk.document_id == did)).all()))
    path = d.path
    s.delete(d)
    s.commit()
    Path(path).unlink(missing_ok=True)
    return {"ok": True}


@router.get("/search")
def search(q: str, course_id: int | None = None, k: int = 8, s: Session = Depends(get_session)):
    return rag.search(s, q, course_id, k=min(max(k, 1), 30))
