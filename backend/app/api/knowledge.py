"""知识梳理、思维导图数据、试卷考点。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Course, Document, ExamQuestion, KnowledgeNode
from ..services import exam as exam_svc
from ..services import knowledge as kn

router = APIRouter(prefix="/api", tags=["knowledge"])


def _course(s: Session, cid: int) -> Course:
    c = s.get(Course, cid)
    if not c:
        raise HTTPException(404, "课程不存在")
    return c


def _counts(s: Session, cid: int) -> dict:
    docs = s.exec(select(Document).where(Document.course_id == cid)).all()
    built = {n.document_id for n in s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == cid, KnowledgeNode.level == 1)).all()}
    return {"slides": sum(d.kind == "slides" for d in docs), "built": len(built), "exam": sum(d.kind == "exam" for d in docs),
            "reference": sum(d.kind == "reference" for d in docs)}


@router.get("/courses/{cid}/knowledge")
def get_knowledge(cid: int, s: Session = Depends(get_session)):
    _course(s, cid)
    return {"tree": kn.get_tree(s, cid), "status": kn.status(cid), "docs": _counts(s, cid)}


class BuildIn(BaseModel):
    document_ids: list[int] | None = None
    force: bool = False


@router.post("/courses/{cid}/knowledge/build")
async def build_knowledge(cid: int, body: BuildIn, s: Session = Depends(get_session)):
    _course(s, cid)
    from ..services.translate import configured_chain

    if not configured_chain():
        raise HTTPException(400, "还没有配置可用的模型：请在「设置」里配置「轻量快速」或「翻译」档位。")
    if not _counts(s, cid)["slides"]:
        raise HTTPException(400, "这门课还没有「课件」类资料。上传时选择「课件」，或在资料库里把资料改成「课件」。")
    kn.start_build(cid, body.document_ids, body.force)
    return kn.status(cid)


@router.post("/courses/{cid}/knowledge/cancel")
async def cancel_knowledge(cid: int, s: Session = Depends(get_session)):
    _course(s, cid)
    kn.cancel_build(cid)
    return kn.status(cid)


@router.get("/courses/{cid}/knowledge/export")
def export_knowledge(cid: int, s: Session = Depends(get_session)):
    c = _course(s, cid)
    tree = kn.get_tree(s, cid)
    if not tree:
        raise HTTPException(400, "还没有知识梳理结果")
    name = f"{c.name}-知识梳理.md"
    return Response(content=kn.to_markdown(tree, {}).encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ---------------------------------------------------------------- 试卷
@router.get("/courses/{cid}/exams")
def list_exams(cid: int, s: Session = Depends(get_session)):
    _course(s, cid)
    nodes = {n.id: n for n in s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == cid)).all()}
    out = []
    for d in s.exec(select(Document).where(Document.course_id == cid, Document.kind == "exam").order_by(Document.created_at)).all():
        qs = s.exec(select(ExamQuestion).where(ExamQuestion.document_id == d.id).order_by(ExamQuestion.id)).all()
        out.append({"document_id": d.id, "filename": d.filename, "status": d.status, "analysis": exam_svc.status(d.id),
                    "questions": [exam_svc.question_out(q, nodes) for q in qs]})
    return out


@router.post("/documents/{did}/exam/analyze")
async def analyze_exam(did: int, s: Session = Depends(get_session)):
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    if d.kind != "exam":
        raise HTTPException(400, "只有「试卷」类资料可以分析考点")
    from ..services.translate import configured_chain

    if not configured_chain():
        raise HTTPException(400, "还没有配置可用的模型：请在「设置」里配置「轻量快速」或「翻译」档位。")
    if not s.exec(select(KnowledgeNode).where(KnowledgeNode.course_id == d.course_id, KnowledgeNode.level == 3)).first():
        raise HTTPException(400, "请先对课件做「知识梳理」，才能把考点对应到课件。")
    exam_svc.start_analysis(did)
    return exam_svc.status(did)


@router.get("/documents/{did}/exam-marks")
def exam_marks(did: int, s: Session = Depends(get_session)):
    """课件的每一页上有哪些考点（课件阅读器用来提示「这里是考点」）。"""
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    if d.kind != "slides":
        return {"pages": {}}
    return {"pages": {str(p): v for p, v in exam_svc.marks_for_document(s, did).items()}}
