"""导出译文：DOCX / PPTX 保留原文件格式（替换为中文，或中英对照）；PDF 在原页面上覆盖中文（尽力而为）；任何格式都可导出对照 Markdown。"""
from __future__ import annotations

import copy
import io
import os
from pathlib import Path

import pymupdf

from ..models import DocBlock
from .docblocks import iter_docx, iter_pptx, meta_of

GRAY_BLUE = (0x2F, 0x55, 0x97)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",  # 微软雅黑：字形覆盖最全（含项目符号、希腊字母、数学符号）
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def find_cjk_font() -> str | None:
    env = os.environ.get("ALP_CJK_FONT")
    return next((p for p in ([env] if env else []) + FONT_CANDIDATES if p and Path(p).exists()), None)


# ----------------------------------------------------------------- DOCX
def _clear_runs_docx(p, keep) -> None:
    for child in list(p._p):
        tag = child.tag.rsplit("}", 1)[-1]
        if tag in ("r", "hyperlink", "ins", "del", "proofErr", "smartTag") and child is not keep:
            p._p.remove(child)


def _set_docx(p, text: str) -> None:
    runs = p.runs
    if runs:
        runs[0].text = text
        _clear_runs_docx(p, runs[0]._r)
    else:
        p.add_run(text)


def export_docx(src: Path, tr: dict[int, str], mode: str) -> bytes:
    from docx import Document as Docx
    from docx.shared import RGBColor
    from docx.text.paragraph import Paragraph

    doc = Docx(str(src))
    items = list(iter_docx(doc))  # 先固定下来再改，避免遍历时插入的新段落被当成原文
    for idx, (_kind, p, _meta) in enumerate(items):
        t = tr.get(idx)
        if not t:
            continue
        if mode == "bilingual":
            new_p = copy.deepcopy(p._p)
            p._p.addnext(new_p)
            para = Paragraph(new_p, p._parent)
            _set_docx(para, t)
            for r in para.runs:
                r.italic = True
                r.font.color.rgb = RGBColor(*GRAY_BLUE)
        else:
            _set_docx(p, t)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------- PPTX
def _set_pptx(p, text: str) -> None:
    from pptx.oxml.ns import qn

    runs = p.runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._r.getparent().remove(r._r)
    else:
        p.add_run().text = text
    for br in p._p.findall(qn("a:br")):
        p._p.remove(br)


def export_pptx(src: Path, tr: dict[int, str], mode: str) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.text.text import _Paragraph

    prs = Presentation(str(src))
    items = list(iter_pptx(prs))
    for idx, (_s, _kind, p, _meta) in enumerate(items):
        t = tr.get(idx)
        if not t:
            continue
        if mode == "bilingual":
            new_p = copy.deepcopy(p._p)
            p._p.addnext(new_p)
            para = _Paragraph(new_p, p._parent)
            _set_pptx(para, t)
            for r in para.runs:
                r.font.color.rgb = RGBColor(*GRAY_BLUE)
                if r.font.size:
                    r.font.size = int(r.font.size * 0.85)
        else:
            _set_pptx(p, t)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------- PDF（在原页面上覆盖中文）
_GLYPH_FALLBACK = {"•": "●·-", "▪": "■·-", "◦": "○·-", "●": "•·-", "–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"',
                   "…": "...", " ": " ", "→": ">", "←": "<", "≤": "<=", "≥": ">=", "×": "x"}


def fit_glyphs(text: str, font_obj) -> str:
    """字体里没有的字符（例如黑体没有「•」）会显示成方框，换成字体里有的近似字符；实在没有就去掉。"""
    if font_obj is None:
        return text
    out = []
    for ch in text:
        if ch in "\n " or font_obj.has_glyph(ord(ch)):
            out.append(ch)
            continue
        out.append(next((c for c in _GLYPH_FALLBACK.get(ch, "") if font_obj.has_glyph(ord(c))), ""))
    return "".join(out)


def export_pdf(src: Path, blocks: list[DocBlock], tr: dict[int, str]) -> tuple[bytes, dict]:
    """tr 以块 id 为键。返回 (pdf 字节, 统计信息)。"""
    doc = pymupdf.open(src)
    font = find_cjk_font()
    img_none = getattr(pymupdf, "PDF_REDACT_IMAGE_NONE", 0)
    art_none = getattr(pymupdf, "PDF_REDACT_LINE_ART_NONE", 0)
    by_page: dict[int, list[tuple[DocBlock, str, dict]]] = {}
    for b in blocks:
        if b.id in tr and "bbox" in (m := meta_of(b)):  # 扫描页识别出的块没有位置，没法原位替换
            by_page.setdefault(b.page, []).append((b, tr[b.id], m))
    font_obj = pymupdf.Font(fontfile=font) if font else None
    placed = shrunk = overflow = 0
    for pno, items in by_page.items():
        page = doc[pno - 1]
        rects = []
        for _b, _t, m in items:
            r = pymupdf.Rect(*m["bbox"])
            rects.append(r)
            page.add_redact_annot(r, fill=False, cross_out=False)
        try:  # 只擦掉文字，不动图片和矢量图形
            page.apply_redactions(images=img_none, graphics=art_none)
        except TypeError:
            page.apply_redactions(images=img_none)
        if font:
            page.insert_font(fontname="cjk", fontfile=font)
        fname = "cjk" if font else "china-s"
        for (_b, text, m), r in zip(items, rects):
            text = fit_glyphs(text, font_obj)
            size = max(6.0, float(m.get("size", 12)))
            c = int(m.get("color", 0))
            color = (((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)
            box, fs, ok = r, size, False
            for grow in (1.0, 1.5):  # 放不下时允许向下多占一点位置
                box = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * grow)
                fs = size
                while fs >= 6:
                    if page.insert_textbox(box, text, fontsize=fs, fontname=fname, color=color, align=0) >= 0:
                        ok = True
                        break
                    fs -= 0.5
                if ok:
                    break
            if not ok:  # 实在放不下：用最小字号强行写出（可能溢出方框）
                page.insert_textbox(pymupdf.Rect(r.x0, r.y0, r.x1 + 40, r.y0 + r.height * 2.5), text, fontsize=6,
                                    fontname=fname, color=color)
                overflow += 1
            elif fs < size:
                shrunk += 1
            placed += 1
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001 - 没装 fonttools 时跳过，只是文件大一点
        pass
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data, {"placed": placed, "shrunk": shrunk, "overflow": overflow, "embedded_font": bool(font)}


# ----------------------------------------------------------------- Markdown 对照
def export_markdown(title: str, filetype: str, blocks: list[DocBlock], tr: dict[int, str]) -> str:
    out = [f"# {title}（中英对照）", ""]
    page = None
    for b in blocks:
        if filetype in ("pdf", "pptx") and b.page != page:
            page = b.page
            label = f"第 {page} 页" if filetype == "pdf" else f"第 {page} 张幻灯片"
            out += ["", "---", "", f"### {label}", ""]
        t = tr.get(b.id)
        prefix = {"title": "## ", "h1": "## ", "h2": "### ", "h3": "#### ", "li": "- "}.get(b.kind, "")
        if b.kind == "code":
            out += ["```", b.text, "```", ""]
            continue
        out.append(f"{prefix}{b.text}")
        if t:
            out += ["", f"> {t.replace(chr(10), chr(10) + '> ')}"]
        out.append("")
    return "\n".join(out)
