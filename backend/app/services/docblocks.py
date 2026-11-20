"""把各种格式的文档拆成「文字块」。

解析（parse_*）和导出译文（见 translate_export.py）共用同一套遍历函数 iter_*，
块的顺序号 idx 才能保证两边严格对应：解析时 idx = 第几个非空段落，导出时按同样顺序回到原位置。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

import pymupdf

BULLET = re.compile(r"^\s*([•·▪■●◦▶►○\-–—*]|\(?\d{1,2}[.)）]|[a-zA-Z][.)])\s+")


def norm(s: str) -> str:
    return re.sub(r"[ \t ]+", " ", s).strip()


def virtual_page(chars_before: int, per_page: int = 2200) -> int:
    """DOCX / 文本没有真实分页，按字数切成“虚拟页”，方便分段翻译和定位。"""
    return 1 + chars_before // per_page


# ---------------------------------------------------------------- PDF
def _join_lines(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln  # 行尾连字符断词
        elif BULLET.match(ln):
            out += "\n" + ln  # 新的项目符号另起一行
        else:
            out += " " + ln
    return out


def pdf_blocks(path: Path) -> list[dict]:
    blocks: list[dict] = []
    with pymupdf.open(path) as doc:
        for pno, page in enumerate(doc, start=1):
            pw, ph = page.rect.width, page.rect.height
            page_blocks = []
            for b in page.get_text("dict", sort=True)["blocks"]:
                if b.get("type") != 0:
                    continue
                lines, sizes, color, bold = [], [], 0, False
                for ln in b["lines"]:
                    txt = "".join(sp["text"] for sp in ln["spans"])
                    if txt.strip():
                        lines.append(txt)
                        for sp in ln["spans"]:
                            if sp["text"].strip():
                                sizes.append(sp["size"])
                                if not sizes[1:]:
                                    color, bold = sp.get("color", 0), bool(sp.get("flags", 0) & 16)
                text = norm(_join_lines(lines))
                if not text:
                    continue
                size = sum(sizes) / len(sizes) if sizes else 11
                page_blocks.append({"text": text, "bbox": [round(v, 1) for v in b["bbox"]], "size": round(size, 1),
                                    "color": color, "bold": bold})
            if page_blocks:
                med = sorted(x["size"] for x in page_blocks)[len(page_blocks) // 2]
            for x in page_blocks:
                kind = "title" if x["size"] >= med * 1.35 and len(x["text"]) < 140 else "p"
                blocks.append({"page": pno, "kind": kind, "text": x["text"],
                               "meta": {"bbox": x["bbox"], "size": x["size"], "color": x["color"], "bold": x["bold"], "pw": pw, "ph": ph}})
    return blocks


# ---------------------------------------------------------------- DOCX
def iter_docx(doc) -> Iterator[tuple[str, object, dict | None]]:
    """按文档顺序产出 (kind, 段落对象, meta)：正文段落 + 表格单元格里的段落。空段落跳过。"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    table_i = -1
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            if p.text.strip():
                style = (p.style.name if p.style is not None else "") or ""
                m = re.match(r"Heading (\d)", style)
                kind = "title" if style == "Title" else f"h{m.group(1)}" if m else "li" if style.startswith("List") else "p"
                yield kind, p, None
        elif tag == "tbl":
            table_i += 1
            table = Table(child, doc)
            for r, row in enumerate(table.rows):
                seen = []
                for c, cell in enumerate(row.cells):
                    if any(cell._tc is s for s in seen):
                        continue  # 合并单元格会重复出现
                    seen.append(cell._tc)
                    for p in cell.paragraphs:
                        if p.text.strip():
                            yield "cell", p, {"table": [table_i, r, c]}


def docx_blocks(path: Path) -> list[dict]:
    from docx import Document as Docx

    doc = Docx(str(path))
    out, chars = [], 0
    for kind, p, meta in iter_docx(doc):
        text = norm(p.text)
        out.append({"page": virtual_page(chars), "kind": kind, "text": text, "meta": meta or {}})
        chars += len(text)
    return out


# ---------------------------------------------------------------- PPTX
def _iter_shapes(shapes):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for sh in shapes:
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(sh.shapes)
        else:
            yield sh


def iter_pptx(prs) -> Iterator[tuple[int, str, object, dict | None]]:
    """按幻灯片顺序产出 (幻灯片序号, kind, 段落对象, meta)；包含表格和演讲者备注。"""
    from pptx.enum.shapes import PP_PLACEHOLDER

    for s, slide in enumerate(prs.slides, start=1):
        for sh in _iter_shapes(slide.shapes):
            if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
                is_title = False
                if sh.is_placeholder:
                    try:
                        is_title = sh.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
                    except Exception:  # noqa: BLE001
                        pass
                for p in sh.text_frame.paragraphs:
                    if p.text.strip():
                        yield s, "title" if is_title else ("li" if p.level > 0 else "p"), p, None
            if getattr(sh, "has_table", False) and sh.has_table:
                for r, row in enumerate(sh.table.rows):
                    for c, cell in enumerate(row.cells):
                        for p in cell.text_frame.paragraphs:
                            if p.text.strip():
                                yield s, "cell", p, {"table": [sh.shape_id, r, c]}
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            for p in slide.notes_slide.notes_text_frame.paragraphs:
                if p.text.strip():
                    yield s, "note", p, None


def pptx_blocks(path: Path) -> list[dict]:
    from pptx import Presentation

    prs = Presentation(str(path))
    return [{"page": s, "kind": kind, "text": norm(p.text.replace("\x0b", " ")), "meta": meta or {}}
            for s, kind, p, meta in iter_pptx(prs)]


# ---------------------------------------------------------------- Markdown / TXT
def text_blocks(path: Path, markdown: bool) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030"):
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        content = raw.decode("utf-8", errors="ignore")
    out, chars, in_code, buf = [], 0, False, []

    def flush(kind="p"):
        nonlocal chars, buf
        text = norm(" ".join(buf)) if kind != "code" else "\n".join(buf).strip()
        buf = []
        if text:
            out.append({"page": virtual_page(chars, 3000), "kind": kind, "text": text, "meta": {}})
            chars += len(text)

    for line in content.splitlines():
        if markdown and line.strip().startswith("```"):
            flush("code" if in_code else "p")
            in_code = not in_code
            continue
        if in_code:
            buf.append(line)
            continue
        h = re.match(r"^(#{1,6})\s+(.*)", line) if markdown else None
        if h:
            flush()
            buf = [h.group(2)]
            flush(f"h{min(len(h.group(1)), 3)}")
        elif not line.strip():
            flush()
        elif markdown and BULLET.match(line):
            flush()
            buf = [line.strip()]
            flush("li")
        else:
            buf.append(line.strip())
    flush("code" if in_code else "p")
    return out


def parse_blocks(path: Path, filetype: str) -> list[dict]:
    if filetype == "pdf":
        return pdf_blocks(path)
    if filetype == "docx":
        return docx_blocks(path)
    if filetype == "pptx":
        return pptx_blocks(path)
    return text_blocks(path, markdown=(filetype == "md"))


def meta_of(block) -> dict:
    try:
        return json.loads(block.meta_json) if block.meta_json else {}
    except ValueError:
        return {}
