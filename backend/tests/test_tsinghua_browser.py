"""用本机 Chrome/Edge（无头）对本地“假清华站点”做端到端测试。不访问真实清华服务。

覆盖：自动填写并取得 token、会话复用无需密码、密码错误、二次验证、图形验证码、
跳转到非预期站点 / 表单提交目标异常时拒绝填写，以及运行期间所有进程命令行中都没有密码。
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import pytest

from app.services.tsinghua.auth_worker import AuthConfig, PlaywrightAuthWorker, find_browser

pytestmark = pytest.mark.skipif(find_browser() is None, reason="本机没有 Chrome/Edge")

USER, PASSWORD = "2026000001", "E2e-Secret-#4471"


def _jwt(salt: str) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc({'iat': int(time.time()), 'sid': salt})}.sig"


def _cmdlines() -> str:
    if sys.platform != "win32":
        return subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                          "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"],
                         capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
    return out.stdout


class Sites:
    def __init__(self):
        self.mode = "normal"  # normal / two_factor / captcha / evil_redirect / bad_action
        self.copy_mode = "writeText"  # writeText / setData / execCommand：网站可能用的三种复制方式
        self.posts: list[dict] = []
        self.evil_posts = 0
        self.cmdline_leaks: list[str] = []
        self.sso_sessions: set[str] = set()
        self.tickets: set[str] = set()
        self.model_sessions: dict[str, str] = {}
        sites = self

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                cookie = self.headers.get("Cookie", "")
                sid = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("mm=")), "")
                if u.path == "/callback":
                    t = parse_qs(u.query).get("ticket", [""])[0]
                    if t in sites.tickets:
                        sites.tickets.discard(t)
                        sid = uuid.uuid4().hex
                        sites.model_sessions[sid] = _jwt(sid)
                        self.send_response(302)
                        self.send_header("Set-Cookie", f"mm={sid}; Path=/")
                        self.send_header("Location", "/")
                        self.end_headers()
                        return
                if sid in sites.model_sessions:
                    tok = sites.model_sessions[sid]
                    html = f"""<html><body><h3>可以向 DeepSeek 提问</h3>
<div id=dlg style="display:none"><b>API 使用方式</b><button id=copy>复制token</button></div>
<button style="position:fixed;right:10px;bottom:10px" onclick="document.getElementById('dlg').style.display='block'">API使用指南</button>
<script>{sites.copy_js(tok)}</script></body></html>"""
                    self._html(html)
                    return
                target = sites.evil if sites.mode == "evil_redirect" else sites.sso
                svc = quote(f"{sites.model}/callback", safe="")
                self.send_response(302)
                self.send_header("Location", f"{target}/login?service={svc}")
                self.end_headers()

            def _html(self, html):
                b = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b)

        class SSO(BaseHTTPRequestHandler):
            evil = False

            def log_message(self, *a):
                pass

            def _html(self, html, cookie=None):
                b = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(b)

            def _form(self, svc, err=""):
                action = f"{sites.evil}/do_login" if sites.mode == "bad_action" else "/do_login"
                code = '<input id="i_code" name="c">' if sites.mode == "captcha" else ""
                return f"""<html><body><form id="theform" method="post" action="{action}">
<input id="i_user" name="u"><input id="i_pass" type="password" name="p">{code}
<input type="hidden" name="service" value="{svc}"><button type="submit">登录</button></form>
<div>{err}</div></body></html>"""

            def do_GET(self):
                u = urlparse(self.path)
                svc = parse_qs(u.query).get("service", [""])[0]
                cookie = self.headers.get("Cookie", "")
                sso = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("sso=")), "")
                if not self.evil and sso in sites.sso_sessions and svc:
                    t = uuid.uuid4().hex
                    sites.tickets.add(t)
                    self.send_response(302)
                    self.send_header("Location", f"{svc}?ticket={t}")
                    self.end_headers()
                    return
                self._html(self._form(svc))

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
                if self.evil:
                    sites.evil_posts += 1
                    self._html("evil")
                    return
                sites.posts.append({"user": form.get("u")})
                # 浏览器还在运行时，检查所有进程的命令行里有没有密码
                lines = _cmdlines()
                if PASSWORD in lines or PASSWORD.replace("#", "%23") in lines:
                    sites.cmdline_leaks.append("password-in-cmdline")
                svc = form.get("service", "")
                if sites.mode == "two_factor":
                    self._html("<html><body>本次登录需要进行二次验证，请使用企业微信扫码</body></html>")
                    return
                if form.get("u") != USER or form.get("p") != PASSWORD:
                    self._html(self._form(svc, "用户名或密码错误"))
                    return
                sid = uuid.uuid4().hex
                sites.sso_sessions.add(sid)
                t = uuid.uuid4().hex
                sites.tickets.add(t)
                self.send_response(302)
                self.send_header("Set-Cookie", f"sso={sid}; Path=/")
                self.send_header("Location", f"{svc}?ticket={t}")
                self.end_headers()

        class Evil(SSO):
            evil = True

        self.servers = [ThreadingHTTPServer(("127.0.0.1", 0), h) for h in (Model, SSO, Evil)]
        for s in self.servers:
            threading.Thread(target=s.serve_forever, daemon=True).start()
        # Cookie 不区分端口：模型站点与统一认证用不同主机名，才能像真实环境一样分开保存登录状态
        ports = [s.server_address[1] for s in self.servers]
        self.model, self.sso, self.evil = f"http://localhost:{ports[0]}", f"http://127.0.0.1:{ports[1]}", f"http://localhost:{ports[2]}"

    def copy_js(self, tok: str) -> str:
        t = json.dumps(tok)
        if self.copy_mode == "setData":
            return ("document.getElementById('copy').onclick=()=>{const h=e=>{e.clipboardData.setData('text/plain'," + t +
                    ");e.preventDefault();};document.addEventListener('copy',h);document.execCommand('copy');"
                    "document.removeEventListener('copy',h);};")
        if self.copy_mode == "execCommand":
            return ("document.getElementById('copy').onclick=()=>{const a=document.createElement('textarea');a.value=" + t +
                    ";document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();};")
        return "document.getElementById('copy').onclick=()=>navigator.clipboard.writeText(" + t + ");"

    def close(self):
        for s in self.servers:
            s.shutdown()


@pytest.fixture(scope="module")
def sites():
    s = Sites()
    yield s
    s.close()


def _worker(s: Sites) -> PlaywrightAuthWorker:
    return PlaywrightAuthWorker(AuthConfig(model_origin=s.model, sso_origin=s.sso, detect_timeout_s=10,
                                           submit_timeout_s=10, nav_timeout_ms=15000))


def _collect(s, creds=None, saved=None, renew=False, allow_password=True):
    return _worker(s).collect(saved_state=saved, renew=renew, credentials=lambda: creds,
                              allow_password=allow_password, interactive=False)


def test_login_fills_only_sso_and_captures_token(sites):
    sites.mode = "normal"
    r = _collect(sites, {"username": USER, "password": PASSWORD})
    assert r.outcome == "ok", r.message
    assert r.token and r.token.count(".") == 2 and r.password_submitted
    assert sites.posts[-1] == {"user": USER}
    assert sites.cmdline_leaks == []
    # 会话复用：带着保存的浏览器状态重新认证（丢弃模型站点状态），无需密码即拿到不同的新 token
    n = len(sites.posts)
    r2 = _collect(sites, None, saved=r.storage_state, renew=True, allow_password=False)
    assert r2.outcome == "ok" and r2.token != r.token and len(sites.posts) == n and not r2.password_submitted


def test_wrong_password(sites):
    sites.mode = "normal"
    r = _collect(sites, {"username": USER, "password": "wrong-password"})
    assert r.outcome == "bad_credentials" and r.password_submitted


def test_no_credentials_needs_user(sites):
    sites.mode = "normal"
    r = _collect(sites, None)
    assert r.outcome == "needs_user" and not r.password_submitted


def test_two_factor_needs_user(sites):
    sites.mode = "two_factor"
    r = _collect(sites, {"username": USER, "password": PASSWORD})
    assert r.outcome == "needs_user" and "二次验证" in r.message


def test_captcha_not_filled(sites):
    sites.mode = "captcha"
    n = len(sites.posts)
    r = _collect(sites, {"username": USER, "password": PASSWORD})
    assert r.outcome == "needs_user" and "验证码" in r.message and len(sites.posts) == n


def test_redirect_to_unexpected_site_refuses(sites):
    sites.mode = "evil_redirect"
    r = _collect(sites, {"username": USER, "password": PASSWORD})
    assert r.outcome == "flow_changed" and sites.evil_posts == 0 and not r.password_submitted


def test_form_action_to_other_origin_refuses(sites):
    sites.mode = "bad_action"
    n = len(sites.posts)
    r = _collect(sites, {"username": USER, "password": PASSWORD})
    assert r.outcome == "flow_changed" and sites.evil_posts == 0 and len(sites.posts) == n
    sites.mode = "normal"


def test_close_stops_browser(sites):
    """close() 在登录进行中被调用：流程返回 stopped，浏览器进程被关闭。"""
    sites.mode = "two_factor"
    w = PlaywrightAuthWorker(AuthConfig(model_origin=sites.model, sso_origin=sites.sso, detect_timeout_s=10,
                                        submit_timeout_s=30, user_wait_timeout_s=60))
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("r", w.collect(
        saved_state=None, renew=False, credentials=lambda: None, allow_password=False, interactive=False)))
    th.start()
    time.sleep(2.5)
    w.close()
    th.join(timeout=30)
    assert not th.is_alive()
    assert out["r"].outcome in ("stopped", "needs_user")
    assert w._browser is None and w._pw is None
    sites.mode = "normal"


@pytest.mark.parametrize("copy_mode", ["setData", "execCommand"])
def test_other_copy_mechanisms(sites, copy_mode):
    sites.mode, sites.copy_mode = "normal", copy_mode
    try:
        r = _collect(sites, {"username": USER, "password": PASSWORD})
        assert r.outcome == "ok", (r.message, r.detail)
        assert r.token and r.token.count(".") == 2
    finally:
        sites.copy_mode = "writeText"


def test_missing_copy_button_reports_step_and_keeps_session(sites):
    sites.mode, sites.copy_mode = "normal", "writeText"
    orig = sites.copy_js
    sites.copy_js = lambda tok: "document.getElementById('copy').remove();"
    try:
        r = _collect(sites, {"username": USER, "password": PASSWORD})
    finally:
        sites.copy_js = orig
    assert r.outcome == "flow_changed" and r.detail["step"] == "copy_button"
    assert r.storage_state and r.storage_state["cookies"]  # 已登录的会话被保留下来
    assert not any(x.count(".") == 2 and len(x) > 40 for x in r.detail.get("ui", []))
