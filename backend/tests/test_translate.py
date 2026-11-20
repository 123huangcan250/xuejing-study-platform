"""文档翻译的离线测试：真实生成 PDF / DOCX / PPTX，假模型翻译，检查解析、对齐、降级、任务与导出。"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import tempfile
import time
from pathlib import Path

os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="alp-test-tr-"))

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import docblocks, llm, translate  # noqa: E402
from app.services.translate import needs_translation  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="alp-tr-files-"))


# ---------------------------------------------------------------- 测试文件
def make_pdf() -> Path:
    p = TMP / "slides.pdf"
    doc = pymupdf.open()
    a = doc.new_page(width=960, height=540)
    a.insert_text((50, 90), "Thermal Power Plants", fontsize=36)
    a.insert_text((50, 200), "Major part of installed capacity, yielding 60% power supply", fontsize=20)
    a.insert_text((880, 520), "5", fontsize=12)
    b = doc.new_page(width=960, height=540)
    b.insert_text((50, 90), "HVDC", fontsize=36)
    b.insert_text((50, 200), "Transmission lines carry bulk power over long distances", fontsize=20)
    doc.save(p)
    return p


def make_docx() -> Path:
    from docx import Document as Docx

    d = Docx()
    d.add_heading("Power Flow Analysis", 1)
    d.add_paragraph("The power flow problem determines bus voltages.")
    d.add_paragraph("Slack bus has a fixed voltage", style="List Bullet")
    t = d.add_table(rows=2, cols=2)
    for r, row in enumerate([["Bus type", "Known quantities"], ["Generator bus", "Active power and voltage"]]):
        for c, v in enumerate(row):
            t.cell(r, c).text = v
    d.add_paragraph("Iterate until the mismatch is small enough.")
    p = TMP / "notes.docx"
    d.save(p)
    return p


def make_pptx() -> Path:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Course Introduction"
    tf = s.placeholders[1].text_frame
    tf.text = "Understand steady state models"
    sub = tf.add_paragraph()
    sub.text = "Transmission line parameters"
    sub.level = 1
    tbl = s.shapes.add_table(2, 2, Inches(1), Inches(4), Inches(6), Inches(1.5)).table
    tbl.cell(0, 0).text = "Quantity"
    tbl.cell(0, 1).text = "Symbol"
    tbl.cell(1, 0).text = "Voltage magnitude"
    tbl.cell(1, 1).text = "V"
    s.notes_slide.notes_text_frame.text = "Speaker note about system stability"
    p = TMP / "deck.pptx"
    prs.save(p)
    return p


# ---------------------------------------------------------------- 假模型
class FakeLLM:
    """按 JSON 协议返回译文：译文 = 「译」+ 原文里每个单词的长度，保证和原文无关（便于检查原文是否被替换掉）。"""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []
        self.fail_tiers: set[str] = set()
        self.drop_every = 0  # >0：每次丢掉一部分条目，测试补译
        self.glossaries: list[str] = []
        self.delay = 0.0  # 模拟真实模型的延迟

    def zh(self, text: str) -> str:
        return "译文：" + "".join(chr(0x4E00 + (len(w) * 7) % 100) for w in text.split())

    async def chat(self, tier, messages, temperature=0.3, json_mode=False, idle_timeout=None):
        self.calls.append((tier, len(messages[-1]["content"])))
        if self.delay:
            await asyncio.sleep(self.delay)
        if tier in self.fail_tiers:
            raise RuntimeError(f"{tier} down")
        user = messages[-1]["content"]
        m = re.search(r'\{"items".*\}', user, re.S)
        if not m:  # 划词翻译（纯文本）
            return "（译）" + user.strip()[-20:]
        self.glossaries.append(user.split("\n{")[0])
        items = json.loads(m.group(0))["items"]
        if self.drop_every and len(items) > 1:
            items = items[: max(1, len(items) // 2)]
        return json.dumps({"items": [{"i": it["i"], "t": self.zh(it["t"])} for it in items]}, ensure_ascii=False)


@pytest.fixture
def fake(monkeypatch):
    from app.db import init_db

    init_db()  # 纯单元测试不经过应用启动，先把表建好
    f = FakeLLM()
    monkeypatch.setattr(llm, "chat", f.chat)
    monkeypatch.setattr(translate, "configured_chain", lambda: ["translate", "fast", "strong"])
    translate.HEALTH.until.clear()
    translate._CACHE.clear()
    translate.JOBS.clear()
    return f


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------- 判断与解析
@pytest.mark.parametrize("text,expected", [
    ("Major part of installed capacity", True),
    ("HVDC", True),
    ("5", False),
    ("P = UI cosφ", False),
    ("https://www.tsinghua.edu.cn/page", False),
    ("someone@example.edu.cn", False),
    ("第三章 电力系统潮流分析", False),
    ("潮流计算 power flow", False),  # 中文占比高
    ("1.0 pu", False),
])
def test_needs_translation(text, expected):
    assert needs_translation(text) is expected


def test_pdf_blocks():
    blocks = docblocks.pdf_blocks(make_pdf())
    texts = [b["text"] for b in blocks]
    assert any("Thermal Power Plants" in t for t in texts) and any("HVDC" == t for t in texts)
    title = next(b for b in blocks if "Thermal" in b["text"])
    assert title["kind"] == "title" and title["page"] == 1 and len(title["meta"]["bbox"]) == 4
    assert {b["page"] for b in blocks} == {1, 2}


def test_docx_and_pptx_blocks():
    d = docblocks.docx_blocks(make_docx())
    assert [b["kind"] for b in d] == ["h1", "p", "li", "cell", "cell", "cell", "cell", "p"]
    assert d[3]["meta"]["table"] == [0, 0, 0] and d[6]["meta"]["table"] == [0, 1, 1]
    p = docblocks.pptx_blocks(make_pptx())
    kinds = [b["kind"] for b in p]
    assert kinds[0] == "title" and "li" in kinds and kinds.count("cell") == 4 and kinds[-1] == "note"
    assert all(b["page"] == 1 for b in p)


def test_text_blocks(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("# Title\n\nFirst paragraph\nsecond line.\n\n- bullet one\n\n```\ncode here\n```\n", encoding="utf-8")
    b = docblocks.text_blocks(f, markdown=True)
    assert [(x["kind"], x["text"]) for x in b] == [
        ("h1", "Title"), ("p", "First paragraph second line."), ("li", "- bullet one"), ("code", "code here")]


# ---------------------------------------------------------------- 批量翻译与降级
def test_group_alignment_and_missing_items_retry(fake):
    fake.drop_every = 1  # 模型每次只回一半条目
    batch = [(i, f"Sentence number {i} about transformers") for i in range(1, 9)]
    got, model = run(translate.translate_group("Doc", batch, translate.OrderedDict()))
    assert set(got) == set(range(1, 9)) and all(v.startswith("译文：") for v in got.values())
    assert len(fake.calls) > 1  # 缺的条目被拆小重试补齐


def test_glossary_is_remembered_and_sent_back(fake):
    g = translate.OrderedDict()
    translate.remember_terms(g, "潮流计算（power flow）是基础，变压器（transformer）也是")
    assert g == {"power flow": "潮流计算", "transformer": "变压器"}
    run(translate.translate_group("Doc", [(1, "Some text")], g))
    assert "power flow → 潮流计算" in fake.glossaries[0]


def test_tier_fallback_and_cooldown(fake):
    fake.fail_tiers = {"translate"}
    out, model = run(translate.call_llm([{"role": "user", "content": '{"items":[{"i":1,"t":"hello world"}]}'}], json_mode=True))
    assert [c[0] for c in fake.calls] == ["translate", "fast"]
    assert not translate.HEALTH.healthy("translate") and translate.HEALTH.healthy("fast")
    fake.calls.clear()
    run(translate.call_llm([{"role": "user", "content": '{"items":[{"i":1,"t":"again"}]}'}], json_mode=True))
    assert [c[0] for c in fake.calls] == ["fast"]  # 冷却期内先用健康的档位，不再白等挂掉的模型


def test_all_tiers_failing_raises(fake):
    fake.fail_tiers = {"translate", "fast", "strong"}
    with pytest.raises(translate.TranslateError, match="都失败"):
        run(translate.call_llm([{"role": "user", "content": "x"}], json_mode=True))


def test_no_configured_model(monkeypatch):
    monkeypatch.setattr(translate, "configured_chain", lambda: [])
    with pytest.raises(translate.TranslateError, match="还没有配置"):
        run(translate.call_llm([{"role": "user", "content": "x"}], json_mode=True))


# ---------------------------------------------------------------- 接口与导出（真实文件 + 假模型）
@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def _upload(client, cid, path: Path):
    r = client.post(f"/api/courses/{cid}/documents", files={"file": (path.name, path.read_bytes(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    did = r.json()["document"]["id"]
    for _ in range(40):
        d = client.get(f"/api/documents/{did}").json()
        if d["status"] != "processing":
            break
        time.sleep(0.25)
    assert d["status"] == "ready", d
    return did


def _wait_done(client, did, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/documents/{did}/translation").json()["status"]
        if not st["running"]:
            return st
        time.sleep(0.2)
    raise AssertionError("翻译任务超时")


def test_upload_docx_pptx_and_search(client):
    cid = client.post("/api/courses", json={"name": "翻译测试"}).json()["id"]
    docx_id, pptx_id = _upload(client, cid, make_docx()), _upload(client, cid, make_pptx())
    assert client.get(f"/api/documents/{docx_id}").json()["chunk_count"] >= 1
    assert client.get(f"/api/documents/{pptx_id}").json()["page_count"] == 1
    hits = client.get("/api/search", params={"q": "slack bus voltage", "course_id": cid}).json()
    assert hits and hits[0]["filename"] == "notes.docx"
    blocks = client.get(f"/api/documents/{docx_id}/blocks").json()
    assert blocks[0]["kind"] == "h1" and blocks[3]["table"] == [0, 0, 0]
    assert client.post(f"/api/courses/{cid}/documents", files={"file": ("x.doc", b"abc")}).status_code == 400


def test_translate_flow_and_exports(client, fake):
    cid = client.post("/api/courses", json={"name": "翻译导出"}).json()["id"]
    ids = {"docx": _upload(client, cid, make_docx()), "pptx": _upload(client, cid, make_pptx()), "pdf": _upload(client, cid, make_pdf())}

    for kind, did in ids.items():
        st0 = client.get(f"/api/documents/{did}/translation").json()["status"]
        assert st0["translatable"] > 0 and st0["translated"] == 0 and st0["state"] == "idle"
        st = client.post(f"/api/documents/{did}/translate", json={}).json()
        assert st["running"] or st["translated"] > 0
        st = _wait_done(client, did)
        assert st["translated"] == st["translatable"] and st["state"] == "done", (kind, st)
        # 再来一次：已经翻过的不会重复调用模型
        n = len(fake.calls)
        client.post(f"/api/documents/{did}/translate", json={})
        time.sleep(0.5)
        assert len(fake.calls) == n

    # 按页查询译文
    page = client.get(f"/api/documents/{ids['pdf']}/translation", params={"pages": "1,2"}).json()["pages"]
    assert page["1"] and all(b["translation"] for b in page["1"] if b["translatable"])
    assert any(not b["translatable"] for b in page["1"])  # 页码“5”不翻译

    # DOCX：替换 + 对照
    from docx import Document as Docx

    r = client.get(f"/api/documents/{ids['docx']}/translation/export", params={"mode": "zh"})
    assert r.status_code == 200 and r.headers["x-translated"].split("/")[0] == r.headers["x-translated"].split("/")[1]
    texts = [p.text for kind, p, _ in docblocks.iter_docx(Docx(io.BytesIO(r.content)))]
    assert all(t.startswith("译文：") for t in texts) and "Bus type" not in " ".join(texts)
    r = client.get(f"/api/documents/{ids['docx']}/translation/export", params={"mode": "bilingual"})
    d2 = Docx(io.BytesIO(r.content))
    assert "Power Flow Analysis" in " ".join(p.text for p in d2.paragraphs) and sum(p.text.startswith("译文：") for p in d2.paragraphs) >= 3
    assert d2.tables[0].cell(0, 0).text.count("\n") == 1  # 单元格里 原文 + 译文 两段

    # PPTX：替换（含表格、备注）
    from pptx import Presentation

    r = client.get(f"/api/documents/{ids['pptx']}/translation/export", params={"mode": "zh"})
    got = [p.text for _s, _k, p, _m in docblocks.iter_pptx(Presentation(io.BytesIO(r.content)))]
    assert got and all(t.startswith("译文：") or not re.search(r"[A-Za-z]{3}", t) for t in got), got

    # PDF：原页面上覆盖中文，英文原文被擦掉，嵌入中文字体
    r = client.get(f"/api/documents/{ids['pdf']}/translation/export")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    stats = json.loads(r.headers["x-export-stats"])
    assert stats["placed"] >= 4 and stats["overflow"] == 0
    out = pymupdf.open(stream=r.content, filetype="pdf")
    text = " ".join(p.get_text() for p in out)
    assert "译文" in text and "Thermal" not in text and "Transmission" not in text
    assert out[0].rect.width == 960  # 版面尺寸不变

    # Markdown 对照
    r = client.get(f"/api/documents/{ids['pptx']}/translation/export", params={"format": "md"})
    assert "> 译文：" in r.text and "Course Introduction" in r.text and r.headers["content-disposition"].endswith("bilingual.md")

    # 清除译文
    assert client.delete(f"/api/documents/{ids['docx']}/translation").json()["ok"]
    assert client.get(f"/api/documents/{ids['docx']}/translation/export").status_code == 400


def test_job_error_when_all_models_fail_and_recovery(client, fake):
    cid = client.post("/api/courses", json={"name": "失败恢复"}).json()["id"]
    did = _upload(client, cid, make_docx())
    fake.fail_tiers = {"translate", "fast", "strong"}
    client.post(f"/api/documents/{did}/translate", json={})
    st = _wait_done(client, did)
    assert st["state"] == "error" and "都失败" in st["error"] and st["translated"] == 0
    translate.HEALTH.until.clear()
    fake.fail_tiers = set()
    client.post(f"/api/documents/{did}/translate", json={})
    st = _wait_done(client, did)
    assert st["state"] == "done" and st["translated"] == st["translatable"] and not st["error"]


def test_priority_pages_jump_the_queue(client, fake):
    """整份在后台翻译时，用户翻到的页会插到最前面。"""
    cid = client.post("/api/courses", json={"name": "优先级"}).json()["id"]
    from docx import Document as Docx

    d = Docx()
    for i in range(60):  # 够多，会被切成很多批
        d.add_paragraph(f"Paragraph {i} " + "word " * 120)
    path = TMP / "long.docx"
    d.save(path)
    did = _upload(client, cid, path)
    fake.delay = 0.12  # 真实模型不会瞬间返回；否则整份任务在第二个请求到达前就跑完了
    last_page = max(b["page"] for b in client.get(f"/api/documents/{did}/blocks").json())
    assert last_page >= 3
    seen_first = []
    orig = translate._save

    def spy(document_id, texts, originals, model):
        seen_first.append(sorted(texts)[0])
        return orig(document_id, texts, originals, model)

    translate._save = spy
    try:
        client.post(f"/api/documents/{did}/translate", json={})
        client.post(f"/api/documents/{did}/translate", json={"pages": [last_page], "priority": True})
        _wait_done(client, did, 60)
    finally:
        translate._save = orig
    last_ids = {b["id"] for b in client.get(f"/api/documents/{did}/blocks").json() if b["page"] == last_page}
    pos = next(i for i, first in enumerate(seen_first) if first in last_ids)
    assert pos <= len(seen_first) // 2  # 最后一页的译文在前半程就出现了


def test_selection_translate_cache_and_errors(client, fake):
    r = client.post("/api/translate/text", json={"text": "The slack bus balances the system."}).json()
    assert r["translation"].startswith("（译）") and r["cached"] is False
    n = len(fake.calls)
    assert client.post("/api/translate/text", json={"text": "The slack bus balances the system."}).json()["cached"] is True
    assert len(fake.calls) == n
    assert client.post("/api/translate/text", json={"text": "  "}).status_code == 400
    assert client.post("/api/translate/text", json={"text": "x" * 4001}).status_code == 400
    fake.fail_tiers = {"translate", "fast", "strong"}
    r = client.post("/api/translate/text", json={"text": "Brand new sentence here"})
    assert r.status_code == 502 and "失败" in r.json()["detail"]
