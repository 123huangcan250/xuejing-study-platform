"""资料分类 / 知识梳理 / 试卷考点的离线测试（假模型）。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path

os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="alp-test-kn-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import exam, knowledge, llm, translate  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="alp-kn-files-"))


def make_slides() -> Path:
    from docx import Document as Docx

    d = Docx()
    d.add_heading("Power Flow Lecture", 1)
    for i in range(20):  # 够长，会被切成多个页和窗口，提炼出的知识点超过 6 个才会触发「分小节」
        d.add_paragraph(f"Topic {i}: " + f"concept{i} explained in detail. " * 12)
    p = TMP / "03-lecture.docx"
    d.save(p)
    return p


def make_exam() -> Path:
    from docx import Document as Docx

    d = Docx()
    for i, t in enumerate(["Explain what concept1 means and why it matters in a power system.",
                           "Calculate the value of concept4 given the following network data and assumptions.",
                           "Derive the relation between concept7 and the bus voltage magnitudes step by step."], start=1):
        d.add_paragraph(f"Q{i}. ({5 * i} points) {t}")
    p = TMP / "2025-final-exam.docx"
    d.save(p)
    return p


class FakeLLM:
    def __init__(self):
        self.fail = False
        self.calls: list[str] = []

    async def chat(self, tier, messages, temperature=0.3, json_mode=False, idle_timeout=None):
        sys_, user = messages[0]["content"], messages[-1]["content"]
        if self.fail:
            raise RuntimeError("model down")
        if sys_.startswith("你是理工科课程的助教。下面是一份课件的一部分"):
            self.calls.append("extract")
            pages = [int(p) for p in re.findall(r"\[第(\d+)页\]", user)]
            topics = [{"title": f"知识点P{p}（concept{p}）", "summary": f"第{p}页讲了概念", "points": [f"要点{p}"], "pages": [p], "importance": 3 if p == 1 else 2}
                      for p in pages] + [{"title": f"知识点P{p}-补充", "summary": "补充", "points": [], "pages": [p], "importance": 1} for p in pages]
            if pages:
                topics.append({"title": "目录", "summary": "", "points": [], "pages": [999], "importance": 1})  # 页码不在窗口内：应被修正
            return json.dumps({"topics": topics}, ensure_ascii=False)
        if sys_.startswith("你是理工科课程的助教。下面是一份课件里提炼出的"):
            self.calls.append("merge")
            n = len(re.findall(r"^\d+\. ", user, re.M))
            half = n // 2
            return json.dumps({"title": "潮流计算讲义", "summary": "概括", "sections": [
                {"title": "基础", "summary": "", "topics": [str(i) for i in range(half)] + [9999]},  # 字符串编号也要认；9999 是编造的
                {"title": "进阶", "summary": "", "topics": list(range(half, n - 1))}]})  # 故意漏掉最后一个
        if sys_.startswith("下面是一份考试试卷"):
            self.calls.append("split")
            qs = re.findall(r"(Q\d+)\.\s*\((\d+) points\)\s*(.+)", user)
            return json.dumps({"questions": [{"no": n, "type": "计算" if "Calculate" in t else "简答", "text": t, "points": float(p), "answer": ""}
                                            for n, p, t in qs]}, ensure_ascii=False)
        if sys_.startswith("你是理工科课程的命题分析专家"):
            self.calls.append("analyze")
            cat = [int(x.split("|")[0]) for x in user.split("考试题：")[0].splitlines() if re.match(r"^\d+\|", x)]
            qn = [int(i) for i in re.findall(r"【题 (\d+)】", user)]
            items = []
            for k, i in enumerate(qn):
                items.append({"i": i, "topics": [f"考点{i}"], "node_ids": [str(cat[(i * 3) % len(cat)]), 99999],  # 真实模型常把编号写成字符串；99999 是编造的
                              "ability": ["记忆", "应用", "分析", "乱写"][k % 4], "difficulty": 9 if i == 3 else 2})
            return json.dumps({"items": items})
        raise AssertionError("未知提示词：" + sys_[:30])


@pytest.fixture
def fake(monkeypatch):
    from app.db import init_db

    init_db()
    f = FakeLLM()
    monkeypatch.setattr(llm, "chat", f.chat)
    monkeypatch.setattr(translate, "configured_chain", lambda: ["fast"])
    orig = knowledge.make_windows
    monkeypatch.setattr(knowledge, "make_windows", lambda pages, limit=500: orig(pages, limit))  # 小窗口，测试多窗口合并
    translate.HEALTH.until.clear()
    knowledge.JOBS.clear()
    exam.JOBS.clear()
    return f


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def upload(client, cid, path: Path, kind: str | None = None):
    data = {"kind": kind} if kind else None
    r = client.post(f"/api/courses/{cid}/documents", files={"file": (path.name, path.read_bytes(), "application/octet-stream")}, data=data)
    assert r.status_code == 200, r.text
    did = r.json()["document"]["id"]
    for _ in range(40):
        d = client.get(f"/api/documents/{did}").json()
        if d["status"] != "processing":
            return did
        time.sleep(0.2)
    raise AssertionError("解析超时")


def wait(fn, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        st = fn()
        if st["state"] != "running":
            return st
        time.sleep(0.15)
    raise AssertionError("任务超时")


def test_kind_upload_default_and_patch(client):
    cid = client.post("/api/courses", json={"name": "分类"}).json()["id"]
    a = upload(client, cid, make_slides())
    b = upload(client, cid, make_exam(), "exam")
    assert client.get(f"/api/documents/{a}").json()["kind"] == "slides"
    assert client.get(f"/api/documents/{b}").json()["kind"] == "exam"
    bad = client.post(f"/api/courses/{cid}/documents", files={"file": ("x.md", b"hello world")}, data={"kind": "nonsense"})
    assert bad.status_code == 400
    assert client.patch(f"/api/documents/{a}", json={"kind": "bogus"}).status_code == 400
    assert client.patch(f"/api/documents/{a}", json={"kind": "reference"}).json()["kind"] == "reference"


def test_build_knowledge_only_slides_and_skip_unchanged(client, fake):
    cid = client.post("/api/courses", json={"name": "梳理"}).json()["id"]
    slides, exam_doc, ref = upload(client, cid, make_slides()), upload(client, cid, make_exam(), "exam"), None
    from docx import Document as Docx

    p = TMP / "ref.docx"
    d = Docx()
    d.add_paragraph("Reference textbook paragraph about something entirely different here.")
    d.save(p)
    ref = upload(client, cid, p, "reference")

    st = client.post(f"/api/courses/{cid}/knowledge/build", json={}).json()
    assert st["total"] == 1  # 只有一份课件
    assert wait(lambda: client.get(f"/api/courses/{cid}/knowledge").json()["status"])["state"] == "done"
    k = client.get(f"/api/courses/{cid}/knowledge").json()
    assert k["docs"] == {"slides": 1, "built": 1, "exam": 1, "reference": 1}
    ch = k["tree"][0]
    assert ch["level"] == 1 and ch["title"] == "潮流计算讲义" and ch["document_id"] == slides
    titles = [t["title"] for sec in ch["children"] for t in sec["children"]]
    assert all(t.startswith("知识点P") or t == "目录" for t in titles) and len(titles) >= 4
    assert "补充要点" in [s["title"] for s in ch["children"]]  # 模型漏掉的知识点没有丢
    assert all(r["page"] != 999 for sec in ch["children"] for t in sec["children"] for r in t["refs"])  # 越界页码被修正
    first = next(t for sec in ch["children"] for t in sec["children"] if t["title"].startswith("知识点P1"))
    assert first["importance"] == 3 and first["points"] == ["要点1"] and first["refs"][0]["document_id"] == slides
    assert slides not in (exam_doc, ref)

    # 内容没变：再次梳理会跳过，不调用模型
    n = len(fake.calls)
    st = client.post(f"/api/courses/{cid}/knowledge/build", json={}).json()
    assert st["total"] == 0 and st["skipped"] == 1 and len(fake.calls) == n
    # force：重新梳理，旧结果被整体替换而不是叠加
    client.post(f"/api/courses/{cid}/knowledge/build", json={"force": True})
    wait(lambda: client.get(f"/api/courses/{cid}/knowledge").json()["status"])
    assert len(client.get(f"/api/courses/{cid}/knowledge").json()["tree"]) == 1

    md = client.get(f"/api/courses/{cid}/knowledge/export")
    assert md.status_code == 200 and "# 知识梳理" in md.text and "知识点P1" in md.text and "星" not in md.text


def test_build_errors(client, fake):
    cid = client.post("/api/courses", json={"name": "无课件"}).json()["id"]
    upload(client, cid, make_exam(), "exam")
    r = client.post(f"/api/courses/{cid}/knowledge/build", json={})
    assert r.status_code == 400 and "课件" in r.json()["detail"]
    cid2 = client.post("/api/courses", json={"name": "模型失败"}).json()["id"]
    upload(client, cid2, make_slides())
    fake.fail = True
    client.post(f"/api/courses/{cid2}/knowledge/build", json={})
    st = wait(lambda: client.get(f"/api/courses/{cid2}/knowledge").json()["status"])
    assert st["state"] == "error" and "失败" in st["error"]
    assert client.get(f"/api/courses/{cid2}/knowledge").json()["tree"] == []  # 失败不留半截结果


def test_exam_analysis_maps_to_slides_and_marks(client, fake):
    cid = client.post("/api/courses", json={"name": "试卷"}).json()["id"]
    slides, exam_doc = upload(client, cid, make_slides()), upload(client, cid, make_exam(), "exam")
    r = client.post(f"/api/documents/{exam_doc}/exam/analyze")
    assert r.status_code == 400 and "知识梳理" in r.json()["detail"]  # 没有梳理就不能对应
    assert client.post(f"/api/documents/{slides}/exam/analyze").status_code == 400  # 课件不能当试卷分析

    client.post(f"/api/courses/{cid}/knowledge/build", json={})
    wait(lambda: client.get(f"/api/courses/{cid}/knowledge").json()["status"])
    client.post(f"/api/documents/{exam_doc}/exam/analyze")
    st = wait(lambda: client.get(f"/api/courses/{cid}/exams").json()[0]["analysis"])
    assert st["state"] == "done", st
    ex = client.get(f"/api/courses/{cid}/exams").json()[0]
    qs = ex["questions"]
    assert [q["number"] for q in qs] == ["Q1", "Q2", "Q3"] and qs[1]["qtype"] == "计算" and qs[1]["points"] == 10
    valid = {n["id"] for ch in client.get(f"/api/courses/{cid}/knowledge").json()["tree"] for s in ch["children"] for n in s["children"]}
    for q in qs:
        assert q["nodes"] and all(n["id"] in valid for n in q["nodes"])  # 编造的 99999 被过滤
        assert q["ability"] in ("记忆", "应用", "分析", "") and 1 <= q["difficulty"] <= 5
    assert qs[2]["difficulty"] == 3 and qs[0]["ability"] == "记忆"  # 越界难度回落到 3；非法能力层级被清空

    # 课件页面上的考点提示
    marks = client.get(f"/api/documents/{slides}/exam-marks").json()["pages"]
    assert marks and all(m["exam"] == "2025-final-exam" for ms in marks.values() for m in ms)
    assert {m["number"] for ms in marks.values() for m in ms} <= {"Q1", "Q2", "Q3"}
    assert client.get(f"/api/documents/{exam_doc}/exam-marks").json() == {"pages": {}}
    # 知识点上带有考点计数，导出里也有
    tree = client.get(f"/api/courses/{cid}/knowledge").json()["tree"]
    assert tree[0]["exam_total"] >= 3
    assert "考点 ×" in client.get(f"/api/courses/{cid}/knowledge/export").text

    # 把试卷改成参考资料：题目被清掉；把课件改成参考资料：梳理被清掉
    client.patch(f"/api/documents/{exam_doc}", json={"kind": "reference"})
    assert client.get(f"/api/courses/{cid}/exams").json() == []
    client.patch(f"/api/documents/{slides}", json={"kind": "reference"})
    assert client.get(f"/api/courses/{cid}/knowledge").json()["tree"] == []


def test_natural_key_handles_mixed_filenames():
    """真实课件文件名里既有数字开头的，也有文字开头的（如 “暂态01-EN…”），排序不能崩。"""
    names = ["暂态01-EN电力系统暂态概述.pdf", "10-EN第四章.pdf", "02-EN第一章.pdf", "00-EN第O章.pdf", "12-EN第四章 经济运行.pdf", "附录A.pdf", "03-EN第二章.pdf"]
    got = sorted(names, key=knowledge.natural_key)
    assert got[:5] == ["00-EN第O章.pdf", "02-EN第一章.pdf", "03-EN第二章.pdf", "10-EN第四章.pdf", "12-EN第四章 经济运行.pdf"]  # 数字在前，按数值而不是字典序
    assert set(got[5:]) == {"暂态01-EN电力系统暂态概述.pdf", "附录A.pdf"}
