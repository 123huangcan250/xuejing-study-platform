"""扫描版 PDF 识别：用「只有图片、没有文字层」的 PDF + 仿真视觉模型走完整流程。"""
from __future__ import annotations

import os
import tempfile
import time

os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="alp-test-ocr-"))

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import llm, ocr, settings_store  # noqa: E402

PAGE_TEXT = {1: "# 潮流计算\n\n节点电压方程 $$Y_{bus}\\dot V = \\dot I$$\n\n- 牛顿拉夫逊法收敛快\n- PQ 分解法省内存",
             2: "```markdown\n短路电流 $I_k = E / X_d$\n```", 3: "EMPTY"}


def scanned_pdf() -> bytes:
    """第 1 页正常文字，第 2、3、4 页是纯图片（无文字层），其中第 3 页空白。"""
    src = pymupdf.open()
    for t in ("Normal text page with plenty of characters in it.", "scan two body", "", "scan four"):
        pg = src.new_page()
        if t:
            pg.insert_text((72, 100), t, fontsize=20)
    out = pymupdf.open()
    for i, pg in enumerate(src):
        pix = pg.get_pixmap(dpi=60)
        if i == 0:
            out.insert_pdf(src, from_page=0, to_page=0)  # 保留文字层
        else:
            np = out.new_page(width=pg.rect.width, height=pg.rect.height)
            np.insert_image(np.rect, pixmap=pix)
    return out.tobytes()


class FakeVision:
    def __init__(self):
        self.calls = 0
        self.fail_pages: set[int] = set()
        self.seen_images = 0

    async def chat(self, tier, messages, **kw):
        assert tier == "vision"
        content = messages[0]["content"]
        assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        self.seen_images += 1
        self.calls += 1
        # 图里没有页码信息，按调用顺序对应：第 2、4 页有字，第 3 页空白——按渲染顺序不可靠，所以用 data 长度之外的方式：
        # 这里用固定的队列
        return self.queue.pop(0)

    queue: list[str] = []


@pytest.fixture()
def client(monkeypatch):
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def configure_vision():
    cfg = settings_store.load_config()
    settings_store.save_config({"tiers": {"vision": {"model": "qwen-vl-test", "base_url": "http://vision.test/v1", "api_key": "sk-test-vision-key"}}})
    return cfg


def upload(client, name="scan.pdf") -> int:
    cid = client.post("/api/courses", json={"name": "OCR 课"}).json()["id"]
    r = client.post(f"/api/courses/{cid}/documents", files={"file": (name, scanned_pdf(), "application/pdf")})
    did = r.json()["document"]["id"]
    for _ in range(100):
        d = client.get(f"/api/documents/{did}").json()
        if d["status"] != "processing":
            break
        time.sleep(0.1)
    return did


def wait_job(client, did):
    for _ in range(200):
        st = client.get(f"/api/documents/{did}/ocr").json()
        if st["job"] and st["job"]["state"] != "running":
            return st
        time.sleep(0.1)
    raise AssertionError("OCR 任务超时")


def test_clean_output_and_split():
    assert ocr.clean_output("```markdown\nhello\n```") == "hello"
    assert ocr.clean_output("EMPTY") == "" and ocr.clean_output(" empty. ") == ""
    parts = ocr.split_blocks("# 标题\n\n正文一\n\n$$\na = b\n\nc = d\n$$\n\n- 列表项")
    assert [k for k, _ in parts] == ["h1", "p", "p", "li"]
    assert "a = b\n\nc = d" in parts[2][1]  # 公式里的空行不拆


def test_scanned_pdf_detected_and_not_configured(client):
    did = upload(client)
    d = client.get(f"/api/documents/{did}").json()
    assert d["page_count"] == 4 and d["empty_pages"] == "2,3,4"
    st = client.get(f"/api/documents/{did}/ocr").json()
    assert st["pending"] == 3 and st["recognized"] == 0 and not st["vision"]["ok"]
    r = client.post(f"/api/documents/{did}/ocr", json={})
    assert r.status_code == 400 and "视觉解析档" in r.json()["detail"]


def test_full_ocr_flow(client, monkeypatch):
    configure_vision()
    f = FakeVision()
    f.queue = [PAGE_TEXT[2], PAGE_TEXT[3], "第四页：同步发电机 $x_d''$ 次暂态电抗"]
    monkeypatch.setattr(llm, "chat", f.chat)
    monkeypatch.setattr(ocr, "CONCURRENCY", 1)  # 顺序执行，队列和页码才对得上
    did = upload(client)
    # 先触发一次文字块解析（模拟用户已经打开过阅读器）
    before = client.get(f"/api/documents/{did}/blocks").json()
    assert {b["page"] for b in before} == {1}
    r = client.post(f"/api/documents/{did}/ocr", json={})
    assert r.status_code == 200, r.text
    st = wait_job(client, did)
    assert st["job"]["state"] == "done" and st["job"]["done"] == 3 and f.calls == 3
    assert st["recognized"] == 3 and st["pending"] == 0
    time.sleep(0.5)  # 并入检索和文字块在线程里

    blocks = client.get(f"/api/documents/{did}/blocks").json()
    assert [b["idx"] for b in blocks] == list(range(len(blocks)))
    assert [b["page"] for b in blocks] == sorted(b["page"] for b in blocks)
    texts_p2 = [b["text"] for b in blocks if b["page"] == 2]
    assert texts_p2 == ["短路电流 $I_k = E / X_d$"]  # 代码块围栏已去掉
    assert not [b for b in blocks if b["page"] == 3]  # 空白页没有块
    assert any("$x_d''$" in b["text"] for b in blocks if b["page"] == 4)
    # 识别出的文字可检索，页码对
    hits = client.get("/api/search", params={"q": "短路电流"}).json()
    assert any(h.get("page") == 2 for h in hits), hits
    d = client.get(f"/api/documents/{did}").json()
    assert d["empty_pages"] == "" and d["status"] == "ready"

    # 重新解析不会再花钱：识别结果在 OcrPage 里
    client.post(f"/api/documents/{did}/reindex")
    time.sleep(0.8)
    assert f.calls == 3
    assert client.get(f"/api/documents/{did}").json()["empty_pages"] == ""
    # 没有 pending 时再点识别是错误提示，不是重复计费
    r = client.post(f"/api/documents/{did}/ocr", json={})
    assert r.status_code == 400 and "没有需要识别" in r.json()["detail"]


def test_ocr_blocks_merge_when_blocks_parsed_later(client, monkeypatch):
    configure_vision()
    f = FakeVision()
    f.queue = ["第二页内容", "第三页内容", "第四页内容"]
    monkeypatch.setattr(llm, "chat", f.chat)
    monkeypatch.setattr(ocr, "CONCURRENCY", 1)
    did = upload(client)
    client.post(f"/api/documents/{did}/ocr", json={})
    wait_job(client, did)
    time.sleep(0.5)
    blocks = client.get(f"/api/documents/{did}/blocks").json()  # 这时才第一次解析
    assert [b["page"] for b in blocks if "页内容" in b["text"]] == [2, 3, 4]
    assert blocks[0]["page"] == 1


def test_partial_failure_can_retry(client, monkeypatch):
    configure_vision()
    f = FakeVision()
    calls = {"n": 0}

    async def flaky(tier, messages, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return "识别文字"

    monkeypatch.setattr(llm, "chat", flaky)
    monkeypatch.setattr(ocr, "CONCURRENCY", 1)
    did = upload(client)
    client.post(f"/api/documents/{did}/ocr", json={})
    st = wait_job(client, did)
    assert st["job"]["state"] == "done" and st["job"]["failed"] == 1 and "重试" in st["job"]["error"]
    assert st["recognized"] == 2 and st["pending"] == 1
    r = client.post(f"/api/documents/{did}/ocr", json={})
    assert r.status_code == 200
    st = wait_job(client, did)
    assert st["recognized"] == 3 and st["pending"] == 0 and not st["job"]["failed"]


def test_export_pdf_skips_blocks_without_bbox():
    from app.services.translate_export import export_pdf
    from app.models import DocBlock
    import json
    import pathlib

    p = pathlib.Path(tempfile.mkdtemp()) / "a.pdf"
    p.write_bytes(scanned_pdf())
    blocks = [DocBlock(id=1, document_id=1, page=1, idx=0, kind="p", text="Normal text",
                       meta_json=json.dumps({"bbox": [72, 80, 400, 110], "size": 12, "color": 0})),
              DocBlock(id=2, document_id=1, page=2, idx=1, kind="p", text="ocr", meta_json=json.dumps({"ocr": True}))]
    data, stats = export_pdf(p, blocks, {1: "普通文字", 2: "识别文字"})
    assert stats["placed"] == 1 and data[:4] == b"%PDF"
