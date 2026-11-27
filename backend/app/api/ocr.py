"""扫描版 PDF 文字识别接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..models import Document
from ..services import ocr as ocr_svc

router = APIRouter(prefix="/api", tags=["ocr"])


def _doc(s: Session, did: int) -> Document:
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404, "资料不存在")
    return d


@router.get("/documents/{did}/ocr")
def ocr_status(did: int, s: Session = Depends(get_session)):
    _doc(s, did)
    return ocr_svc.status(s, did)


class OcrIn(BaseModel):
    pages: list[int] | None = None  # None = 所有还没识别的页


@router.post("/documents/{did}/ocr")
async def ocr_start(did: int, body: OcrIn, s: Session = Depends(get_session)):
    _doc(s, did)
    try:
        ocr_svc.start_ocr(did, body.pages)
    except ocr_svc.OcrError as e:
        raise HTTPException(400, str(e)) from None
    return ocr_svc.status(s, did)


@router.post("/documents/{did}/ocr/cancel")
async def ocr_cancel(did: int, s: Session = Depends(get_session)):
    _doc(s, did)
    ocr_svc.cancel_ocr(did)
    return ocr_svc.status(s, did)
