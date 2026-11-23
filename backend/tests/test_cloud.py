"""清华云盘（Seafile 分享链接）读取：用仿真服务器测完整流程。"""
from __future__ import annotations

import io
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp(prefix="alp-test-cl-"))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import cloud as cl  # noqa: E402

HOST = "seafile.test"
PASSWORD = "Share-pw-8841"


def docx_bytes(text: str) -> bytes:
    from docx import Document as Docx

    d = Docx()
    d.add_heading("Power Flow Notes", 1)
    d.add_paragraph(text)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


class FakeSeafile:
    """按 Seafile 分享链接的公开行为做的仿真：目录列表接口、网页提取码表单、下载重定向。"""

    def __init__(self):
        self.shares: dict[str, dict] = {}
        self.downloads: list[str] = []
        self.evil = False

    def add(self, token, typ, files: dict[str, bytes], password=None, mtime="2026-11-01T10:00:00"):
        self.shares[token] = {"type": typ, "files": files, "password": password, "mtime": mtime}

    def unlocked(self, req: httpx.Request, sh) -> bool:
        return not sh["password"] or "sessionid=ok" in req.headers.get("cookie", "")

    def dirents(self, sh, path):
        path = path.rstrip("/") or "/"
        names: dict[str, bool] = {}
        for f in sh["files"]:
            rel = f if path == "/" else (f[len(path):] if f.startswith(path + "/") else None)
            if rel is None:
                continue
            first = rel.strip("/").split("/")[0]
            names[first] = "/" in rel.strip("/")
        out = []
        for name, is_dir in sorted(names.items()):
            full = (path.rstrip("/") + "/" + name)
            out.append({("folder_name" if is_dir else "file_name"): name, "is_dir": is_dir, "size": 0 if is_dir else len(sh["files"][full]), "last_modified": sh["mtime"]})
        return {"dirent_list": out}

    def handler(self, req: httpx.Request) -> httpx.Response:
        u = urlparse(str(req.url))
        parts = u.path.strip("/").split("/")
        q = parse_qs(u.query)
        if parts[0] == "api":  # /api/v2.1/share-links/<t>/dirents/
            t = parts[3]
            sh = self.shares.get(t)
            if not sh:
                return httpx.Response(404, json={"error_msg": f"Share link {t} not found."})
            if not self.unlocked(req, sh):
                return httpx.Response(403, json={"error_msg": "Permission denied."})
            return httpx.Response(200, json=self.dirents(sh, q.get("path", ["/"])[0]))
        if parts[0] == "seafhttp":
            self.downloads.append(unquote(u.path))
            name = unquote(parts[-1])
            for sh in self.shares.values():
                for p, data in sh["files"].items():
                    if p.endswith("/" + name) or p == "/" + name:
                        return httpx.Response(200, content=data, headers={
                            "content-type": "application/octet-stream", "content-length": str(len(data)),
                            "content-disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
            return httpx.Response(404)
        typ, t = parts[0], parts[1]
        sh = self.shares.get(t)
        html = lambda body, **h: httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8", **h})  # noqa: E731
        if not sh:
            return html("<html>Share link not found</html>")
        if req.method == "POST":  # 提交提取码
            form = parse_qs(req.content.decode())
            ok = form.get("password", [""])[0] == sh["password"] and form.get("csrfmiddlewaretoken", [""])[0] == "csrf123"
            if ok:
                return httpx.Response(302, headers={"location": u.path, "set-cookie": "sessionid=ok; Path=/"})
            return html('<form><input name="password"> 提取码不正确</form>')
        if not self.unlocked(req, sh):
            return html('<form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="csrf123">'
                        '<input type="password" name="password"></form>', **{"set-cookie": "sfcsrftoken=csrf123; Path=/"})
        if q.get("dl"):
            if self.evil:
                return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
            path = q["p"][0] if "p" in q else "/" + next(iter(sh["files"])).strip("/")
            if typ == "d" and path not in sh["files"]:
                return httpx.Response(404)
            return httpx.Response(302, headers={"location": f"https://{HOST}/seafhttp/files/abc123/{quote(path.rsplit('/', 1)[-1])}"})
        return html("<html>share page</html>")


@pytest.fixture
def seafile(monkeypatch):
    from app.db import init_db

    init_db()
    fs = FakeSeafile()
    monkeypatch.setattr(cl, "HOSTS", cl.HOSTS | {HOST})
    monkeypatch.setattr(cl, "TRANSPORT", httpx.MockTransport(fs.handler))
    cl.clear_cache()
    cl.JOBS.clear()
    return fs


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def course(client, name="云盘"):
    return client.post("/api/courses", json={"name": name}).json()["id"]


def wait_import(client, sid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/cloud/sources/{sid}/status").json()
        if st["state"] != "running":
            return st
        time.sleep(0.2)
    raise AssertionError("导入超时")


# ---------------------------------------------------------------- 链接解析与安全
@pytest.mark.parametrize("url,ok", [
    ("https://cloud.tsinghua.edu.cn/d/0123456789abcdef0123/", True),
    ("https://cloud.tsinghua.edu.cn/d/0123456789abcdef0123", True),
    ("https://cloud.tsinghua.edu.cn/f/AbCdEf1234/?dl=1", True),
    ("  https://cloud.tsinghua.edu.cn/d/0123456789abcdef0123/#x ", True),
    ("http://cloud.tsinghua.edu.cn/d/0123456789abcdef0123/", False),  # 非 https
    ("https://evil.example.com/d/0123456789abcdef0123/", False),  # 非白名单域名
    ("https://cloud.tsinghua.edu.cn.evil.com/d/0123456789abcdef0123/", False),
    ("https://cloud.tsinghua.edu.cn/library/xxx/", False),
    ("https://cloud.tsinghua.edu.cn/d/short/", False),
])
def test_parse_share_url(url, ok):
    if ok:
        host, typ, token = cl.parse_share_url(url)
        assert host == "cloud.tsinghua.edu.cn" and typ in "df" and token
    else:
        with pytest.raises(cl.CloudError):
            cl.parse_share_url(url)


# ---------------------------------------------------------------- 目录分享 + 导入
def test_folder_share_import_cache_and_refetch(client, seafile):
    seafile.add("tok1folder1", "d", {
        "/第一章/lecture1.docx": docx_bytes("The slack bus balances active power in the network."),
        "/第一章/notes.md": b"# Notes\n\nPer-unit system simplifies calculations.",
        "/第二章/deep/lecture2.docx": docx_bytes("Newton Raphson iterations converge quadratically."),
        "/readme.zip": b"PK",
        "/.hidden.docx": docx_bytes("hidden"),
    })
    cid = course(client)
    r = client.post(f"/api/courses/{cid}/cloud/sources", json={"url": f"https://{HOST}/d/tok1folder1/", "default_kind": "slides"})
    assert r.status_code == 200, r.text
    body = r.json()
    sid = body["source"]["id"]
    assert [e["path"] for e in body["entries"]] == ["/第一章/lecture1.docx", "/第一章/notes.md", "/第二章/deep/lecture2.docx"]
    assert body["skipped"] == 1 and all(e["state"] == "new" for e in body["entries"])  # .zip 跳过，隐藏文件忽略

    client.post(f"/api/cloud/sources/{sid}/import", json={"kind": "reference"})
    st = wait_import(client, sid)
    assert st["state"] == "done" and st["imported"] == 3 and not st["errors"], st
    docs = client.get(f"/api/courses/{cid}/documents").json()
    assert len(docs) == 3 and all(d["source"] == "cloud" and d["kind"] == "reference" and d["status"] == "ready" for d in docs)
    # 解析出的文字已入库，可检索
    hits = client.get("/api/search", params={"q": "slack bus active power", "course_id": cid}).json()
    assert hits and hits[0]["filename"] == "lecture1.docx"

    # 原文件不落地：清空缓存后，阅读 / 文字块 / 检索仍然可用，需要原文件时再从云盘取回
    cl.clear_cache()
    d1 = next(d for d in docs if d["filename"] == "lecture1.docx")
    assert client.get(f"/api/documents/{d1['id']}/blocks").status_code == 200  # 只用库里的文字块，不碰云盘
    before = len(seafile.downloads)
    r = client.get(f"/api/documents/{d1['id']}/file")
    assert r.status_code == 200 and r.content[:2] == b"PK" and len(seafile.downloads) == before + 1
    client.get(f"/api/documents/{d1['id']}/file")
    assert len(seafile.downloads) == before + 1  # 第二次命中缓存
    md = next(d for d in docs if d["filename"] == "notes.md")
    cl.clear_cache()
    assert "Per-unit" in client.get(f"/api/documents/{md['id']}/text").json()["text"]

    # 再次导入：已导入的跳过；云盘上有更新的不动，除非明确要求替换
    entries = client.get(f"/api/cloud/sources/{sid}/entries").json()["entries"]
    assert all(e["state"] == "imported" for e in entries)
    seafile.shares["tok1folder1"]["mtime"] = "2026-11-19T09:00:00"
    entries = client.get(f"/api/cloud/sources/{sid}/entries").json()["entries"]
    assert all(e["state"] == "updated" for e in entries)
    assert client.post(f"/api/cloud/sources/{sid}/import", json={}).status_code == 400  # 没有“新”文件
    client.post(f"/api/cloud/sources/{sid}/import", json={"paths": ["/第一章/notes.md"], "replace": True})
    assert wait_import(client, sid)["imported"] == 1
    docs2 = client.get(f"/api/courses/{cid}/documents").json()
    assert len(docs2) == 3 and next(d for d in docs2 if d["filename"] == "notes.md")["id"] != md["id"]

    # 删除来源：导入的资料一并删除
    assert client.delete(f"/api/cloud/sources/{sid}").json()["ok"]
    assert client.get(f"/api/courses/{cid}/documents").json() == []
    assert client.get("/api/search", params={"q": "slack bus", "course_id": cid}).json() == []


# ---------------------------------------------------------------- 提取码
def test_password_flow_and_storage(client, seafile):
    seafile.add("tok2locked22", "d", {"/exam.docx": docx_bytes("Voltage regulation exam question text here.")}, password=PASSWORD)
    cid = course(client, "提取码")
    url = f"https://{HOST}/d/tok2locked22/"
    r = client.post(f"/api/courses/{cid}/cloud/sources", json={"url": url})
    assert r.status_code == 401 and "提取码" in r.json()["detail"]
    r = client.post(f"/api/courses/{cid}/cloud/sources", json={"url": url, "password": "wrong"})
    assert r.status_code == 401 and "不正确" in r.json()["detail"]
    assert client.get(f"/api/courses/{cid}/cloud/sources").json() == []  # 失败不留下半截来源
    r = client.post(f"/api/courses/{cid}/cloud/sources", json={"url": url, "password": PASSWORD, "default_kind": "exam"})
    assert r.status_code == 200, r.text
    sid = r.json()["source"]["id"]
    # 提取码不会通过接口返回，也不以明文落盘
    assert PASSWORD not in json.dumps(r.json()) and PASSWORD not in json.dumps(client.get(f"/api/courses/{cid}/cloud/sources").json())
    assert client.get(f"/api/courses/{cid}/cloud/sources").json()[0]["has_password"] is True
    for f in Path(os.environ["APP_DATA_DIR"]).rglob("*"):
        if f.is_file() and f.suffix != ".dpapi" and f.stat().st_size < 50_000_000:
            assert PASSWORD.encode() not in f.read_bytes(), f
    assert cl.load_password(sid) == PASSWORD
    # 用保存的提取码导入
    client.post(f"/api/cloud/sources/{sid}/import", json={})
    assert wait_import(client, sid)["imported"] == 1
    assert client.get(f"/api/courses/{cid}/documents").json()[0]["kind"] == "exam"
    # 提取码被改：导入报 401，用新提取码更新后恢复
    seafile.shares["tok2locked22"]["password"] = "new-pw-1"
    assert client.get(f"/api/cloud/sources/{sid}/entries").status_code == 401
    assert client.put(f"/api/cloud/sources/{sid}/password", json={"password": "bad"}).status_code == 401
    assert client.put(f"/api/cloud/sources/{sid}/password", json={"password": "new-pw-1"}).status_code == 200
    assert client.get(f"/api/cloud/sources/{sid}/entries").status_code == 200
    client.delete(f"/api/cloud/sources/{sid}")
    assert cl.load_password(sid) is None  # 删除来源会一并删除加密的提取码


# ---------------------------------------------------------------- 单文件分享 + 错误与安全
def test_single_file_share(client, seafile):
    seafile.add("tok3single33", "f", {"/exam2025.docx": docx_bytes("Single file share content about transformers.")})
    cid = course(client, "单文件")
    r = client.post(f"/api/courses/{cid}/cloud/sources", json={"url": f"https://{HOST}/f/tok3single33/"})
    assert r.status_code == 200, r.text
    assert [e["name"] for e in r.json()["entries"]] == ["exam2025.docx"]
    sid = r.json()["source"]["id"]
    client.post(f"/api/cloud/sources/{sid}/import", json={})
    assert wait_import(client, sid)["imported"] == 1


def test_errors_and_security(client, seafile, monkeypatch):
    cid = course(client, "错误")
    for url, msg in [(f"https://{HOST}/d/nosuchtoken01/", "不存在"), (f"https://{HOST}/f/nosuchtoken02/", "不存在"),
                     ("https://evil.example.com/d/0123456789abcdef0123/", "只支持清华云盘"), ("not a url", "有效")]:
        r = client.post(f"/api/courses/{cid}/cloud/sources", json={"url": url})
        assert r.status_code == 400 and msg in r.json()["detail"], (url, r.text)
    assert client.post(f"/api/courses/{cid}/cloud/sources", json={"url": f"https://{HOST}/d/x/", "default_kind": "bogus"}).status_code == 400

    seafile.add("tok4evil444", "d", {"/a.docx": docx_bytes("evil redirect test document text")})
    sid = client.post(f"/api/courses/{cid}/cloud/sources", json={"url": f"https://{HOST}/d/tok4evil444/"}).json()["source"]["id"]
    assert client.post(f"/api/courses/{cid}/cloud/sources", json={"url": f"https://{HOST}/d/tok4evil444/"}).status_code == 400  # 重复添加
    seafile.evil = True  # 下载被重定向到内网地址 169.254.169.254：必须拒绝
    client.post(f"/api/cloud/sources/{sid}/import", json={})
    st = wait_import(client, sid)
    assert st["imported"] == 0 and "其他站点" in st["errors"][0]["error"], st
    assert client.get(f"/api/courses/{cid}/documents").json() == []  # 失败不留下半截资料

    seafile.evil = False
    monkeypatch.setattr(cl, "MAX_FILE_BYTES", 100)  # 超大文件被拒
    client.post(f"/api/cloud/sources/{sid}/import", json={})
    st = wait_import(client, sid)
    assert st["imported"] == 0 and "太大" in st["errors"][0]["error"]
