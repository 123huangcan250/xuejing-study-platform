"""端到端冒烟测试：课程 → 上传 PDF → 检索 → 画布问答（模拟 LLM）→ 闪卡复习 → 游戏化结算。

运行：.venv\\Scripts\\python -m pytest -q
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="alp-test-")

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import llm  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def _pdf_bytes() -> bytes:
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((50, 80), "第一章 进程与线程", fontname="china-s", fontsize=16)
    p1.insert_text((50, 120), "进程是资源分配的基本单位，线程是处理器调度的基本单位。", fontname="china-s", fontsize=11)
    p2 = doc.new_page()
    p2.insert_text((50, 80), "第二章 虚拟内存", fontname="china-s", fontsize=16)
    p2.insert_text((50, 120), "页表记录虚拟页号到物理页框号的映射，缺页中断由操作系统处理。", fontname="china-s", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_full_flow(client, monkeypatch):
    r = client.post("/api/courses", json={"name": "操作系统", "emoji": "🖥️"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]

    r = client.post(f"/api/courses/{cid}/documents", files={"file": ("os.pdf", _pdf_bytes(), "application/pdf")})
    assert r.status_code == 200, r.text
    did = r.json()["document"]["id"]
    assert 0 <= r.json()["reward"]["xp"] <= 10  # 每日上限与其他测试共用同一个库，单独跑时恰好是 10
    doc = client.get(f"/api/documents/{did}").json()
    assert doc["status"] == "ready", doc
    assert doc["page_count"] == 2 and doc["chunk_count"] >= 2

    hits = client.get("/api/search", params={"q": "页表 映射", "course_id": cid}).json()
    assert hits and hits[0]["page"] == 2, hits

    # 画布：原文摘录 → 问答节点
    ex = client.post(f"/api/courses/{cid}/nodes", json={
        "type": "excerpt", "content": "页表记录虚拟页号到物理页框号的映射",
        "anchor": {"document_id": did, "page": 2, "quote": "页表记录虚拟页号到物理页框号的映射", "rects": []}}).json()["node"]
    qa = client.post(f"/api/courses/{cid}/nodes", json={
        "type": "qa", "kind": "highlight", "question": "为什么需要多级页表？", "parent_ids": [ex["id"]]}).json()
    assert qa["node"]["depth"] == 1 and len(qa["edges"]) == 1

    captured = {}

    async def fake_stream(tier, messages, temperature=0.5):
        captured["messages"] = messages
        for piece in ["多级页表可以节省", "内存 [[页表]] [1]"]:
            yield piece

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    with client.stream("POST", f"/api/nodes/{qa['node']['id']}/answer") as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "sources" and types[-1] == "done", types
    assert events[-1]["reward"]["event"] == "HIGHLIGHT_ASKED"
    ctx = captured["messages"][1]["content"]
    assert "原文摘录" in ctx and "第 2 页" in ctx

    canvas = client.get(f"/api/courses/{cid}/canvas").json()
    answered = next(n for n in canvas["nodes"] if n["id"] == qa["node"]["id"])
    assert "多级页表" in answered["content"]

    # 环检测
    r = client.post(f"/api/courses/{cid}/edges", json={"source": qa["node"]["id"], "target": ex["id"]})
    assert r.status_code == 400

    # 闪卡
    r = client.post(f"/api/courses/{cid}/flashcards", json=[{"front": "进程和线程的区别？", "back": "进程分配资源，线程被调度"}])
    card = r.json()["cards"][0]
    due = client.get("/api/flashcards/due", params={"course_id": cid}).json()
    assert len(due) == 1 and set(due[0]["preview"].keys()) == {"1", "2", "3", "4"}
    r = client.post(f"/api/flashcards/{card['id']}/review", json={"rating": 3}).json()
    assert r["reward"]["xp"] == 3

    prof = client.get("/api/profile").json()
    assert prof["total_xp"] >= 10 + 6 + 2 + 3
    unlocked = {a["key"] for a in prof["achievements"] if a["unlocked"]}
    assert {"first_upload", "first_fog"} <= unlocked
    assert prof["today"]["xp"] >= 20 and prof["streak"]["current"] == 1
    assert client.get("/api/heatmap").json()[0]["xp"] == prof["today"]["xp"]

    # 设置掩码
    client.put("/api/settings", json={"api_key": "sk-test-1234567890"})
    s = client.get("/api/settings").json()
    assert s["has_api_key"] and "1234567890" not in s["api_key"]
    client.put("/api/settings", json={"api_key": s["api_key"]})  # 回传掩码不应覆盖
    from app.services.settings_store import load_config
    assert load_config()["api_key"] == "sk-test-1234567890"

    assert client.delete(f"/api/courses/{cid}").json()["ok"]
    assert client.get("/api/search", params={"q": "页表"}).json() == []


def test_vector_path(client, monkeypatch):
    """模拟向量模型，验证 ChromaDB 写入 + 混合检索 RRF。"""
    import hashlib

    def fake_embed(texts):
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in t:
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return out

    monkeypatch.setattr(llm, "embedding_enabled", lambda: True)
    monkeypatch.setattr(llm, "embed_sync", fake_embed)
    cid = client.post("/api/courses", json={"name": "向量测试"}).json()["id"]
    did = client.post(f"/api/courses/{cid}/documents",
                      files={"file": ("n.md", "# 梯度下降\n\n梯度下降沿负梯度方向更新参数。".encode(), "text/markdown")}).json()["document"]["id"]
    assert client.get(f"/api/documents/{did}").json()["error"] == ""
    hits = client.get("/api/search", params={"q": "负梯度方向", "course_id": cid}).json()
    assert hits and "vec" in hits[0]["via"], hits
