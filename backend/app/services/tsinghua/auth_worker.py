"""用本机 Chrome/Edge 的独立会话登录清华大模型系统并取得 token。

流程（与官方文档一致）：打开首页 → 未登录则跳到清华统一身份认证 → 登录后点右下角「API使用指南」
→ 在「API 使用方式」里点「复制token」。我们在模型站点页面里拦截剪贴板写入来拿到 token，
不读取系统剪贴板，也不调用任何未公开的刷新接口。

安全约束：
- 只有当页面 origin 严格等于 https://id.tsinghua.edu.cn 且登录表单结构、提交目标都符合预期时才自动填写；
  否则停止自动填写（outcome=flow_changed）。
- 出现图形验证码 / 二次验证（短信、企业微信等）时不尝试绕过，返回 needs_user，由调用方决定是否打开可见窗口交给用户。
- 账号密码只通过 Playwright 管道发送给页面（page.fill），不进入进程参数、日志或异常信息。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

log = logging.getLogger("app.tsinghua.auth")

MODEL_ORIGIN = "https://madmodel.cs.tsinghua.edu.cn"
SSO_ORIGIN = "https://id.tsinghua.edu.cn"
GUIDE_TEXT = "API使用指南"
COPY_TEXT = "复制token"
TWO_FACTOR_HINTS = ("二次验证", "二次认证", "企业微信", "短信验证", "动态口令")
BAD_CRED_HINTS = ("密码错误", "用户名或密码", "账号或密码", "密码不正确", "认证失败", "登录失败", "账号不存在", "已锁定")


@dataclass
class AuthConfig:
    model_origin: str = MODEL_ORIGIN
    sso_origin: str = SSO_ORIGIN
    executable_path: str | None = None
    nav_timeout_ms: int = 45_000
    detect_timeout_s: float = 15.0
    submit_timeout_s: float = 45.0
    user_wait_timeout_s: float = 600.0  # 可见窗口里等待用户完成验证的最长时间


@dataclass
class AuthResult:
    outcome: str  # ok / needs_user / bad_credentials / flow_changed / no_credentials / no_browser / timeout / stopped / error
    message: str
    token: str | None = None
    storage_state: dict | None = None
    password_submitted: bool = False
    detail: dict = field(default_factory=dict)


class _Stopped(Exception):
    pass


def find_browser() -> str | None:
    env = os.environ.get("TSINGHUA_BROWSER_PATH")
    cands = [env] if env else []
    if sys.platform == "win32":
        la, pf, pf86 = os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")
        cands += [
            str(Path(la) / "Google/Chrome/Application/chrome.exe"),
            str(Path(pf) / "Google/Chrome/Application/chrome.exe"),
            str(Path(pf86) / "Microsoft/Edge/Application/msedge.exe"),
            str(Path(pf) / "Microsoft/Edge/Application/msedge.exe"),
        ]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        cands += ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/microsoft-edge"]
    return next((c for c in cands if c and Path(c).exists()), None)


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


def drop_site_state(state: dict | None, origin: str) -> dict | None:
    """重新认证前丢弃模型站点自己的 localStorage 和 Cookie（保留统一认证的会话），迫使站点签发新 token。"""
    if not state:
        return state
    host = urlparse(origin).hostname or ""
    cookies = [c for c in state.get("cookies", []) if (c.get("domain") or "").lstrip(".") != host]
    origins = [o for o in state.get("origins", []) if o.get("origin") != origin]
    return {**state, "cookies": cookies, "origins": origins}


_CAPTURE_SCRIPT = """
(allowed) => {
  if (location.origin !== allowed) return;
  window.__alpCopyHooks = [];
  const capture = (v, via) => {
    if (typeof v === 'string' && v.trim()) { window.__alpCapturedToken = v.trim(); window.__alpCopyHooks.push(via); }
  };
  // 1. navigator.clipboard.writeText / write（拦截在页面内，不碰系统剪贴板）
  try {
    const orig = navigator.clipboard;
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: async (s) => capture(s, 'writeText'),
      write: async (items) => {
        try {
          for (const it of items || []) {
            if (it.types && it.types.includes('text/plain')) capture(await (await it.getType('text/plain')).text(), 'write');
          }
        } catch (e) {}
      },
      readText: orig && orig.readText ? orig.readText.bind(orig) : undefined,
    }});
  } catch (e) {}
  // 2. copy 事件里 e.clipboardData.setData（copy-to-clipboard 等库的做法）
  try {
    const setData = DataTransfer.prototype.setData;
    DataTransfer.prototype.setData = function (type, value) {
      if (String(type).toLowerCase().startsWith('text')) capture(value, 'setData');
      return setData.call(this, type, value);
    };
  } catch (e) {}
  // 3. document.execCommand('copy') + 选区 / 输入框
  const exec = document.execCommand.bind(document);
  document.execCommand = (cmd, ...rest) => {
    if (String(cmd).toLowerCase() === 'copy') {
      exec(cmd, ...rest);  // 先让页面自己的 copy 事件处理器运行（会触发 setData 钩子）
      if (!window.__alpCapturedToken) {
        const el = document.activeElement;
        const sel = (el && el.value) || (window.getSelection() && window.getSelection().toString());
        if (sel) capture(sel, 'execCommand');
      }
      return true;
    }
    return exec(cmd, ...rest);
  };
}
"""


# 无头浏览器的精简启动参数：去掉 GPU 进程、扩展、后台联网和同步（实测 635 → 约 525 MB）
LEAN_ARGS = ["--disable-gpu", "--disable-extensions", "--disable-background-networking", "--disable-component-update",
             "--disable-sync", "--no-first-run", "--renderer-process-limit=1",
             "--disable-features=Translate,OptimizationHints,MediaRouter"]


class PlaywrightAuthWorker:
    """同步接口（在独立线程里跑自己的事件循环），便于从 FastAPI 的事件循环里 to_thread 调用。"""

    def __init__(self, cfg: AuthConfig | None = None):
        self.cfg = cfg or AuthConfig()
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._browser = None
        self._pw = None
        self._busy = threading.Lock()

    # ---------- 生命周期 ----------
    def close(self) -> None:
        """线程安全：通知正在进行的登录停止，并关闭浏览器进程。"""
        self._stop.set()
        loop = self._loop
        if loop and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=15)
            except Exception:  # noqa: BLE001
                log.warning("关闭登录浏览器超时")

    def reset(self) -> None:
        self._stop.clear()

    @property
    def running(self) -> bool:
        return self._busy.locked()

    async def _shutdown(self) -> None:
        b, self._browser = self._browser, None
        if b:
            try:
                await b.close()
            except Exception:  # noqa: BLE001
                pass
        pw, self._pw = self._pw, None
        if pw:
            try:
                await pw.stop()
            except Exception:  # noqa: BLE001
                pass

    def collect(self, *, saved_state: dict | None, renew: bool, credentials: Callable[[], dict | None],
                allow_password: bool, interactive: bool, on_status: Callable[[str, str], None] | None = None) -> AuthResult:
        if not self._busy.acquire(blocking=False):
            return AuthResult("error", "已有登录流程在进行中")
        loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        self._loop = loop
        try:
            return loop.run_until_complete(self._collect(saved_state, renew, credentials, allow_password, interactive,
                                                         on_status or (lambda *_: None)))
        finally:
            try:
                loop.run_until_complete(self._shutdown())
            finally:
                self._loop = None
                loop.close()
                self._busy.release()

    # ---------- 内部 ----------
    def _check_stop(self):
        if self._stop.is_set():
            raise _Stopped()

    async def _sleep(self, s: float):
        end = time.monotonic() + s
        while (left := end - time.monotonic()) > 0:
            self._check_stop()
            await asyncio.sleep(min(0.25, left))

    async def _launch(self, headless: bool, state: dict | None):
        from playwright.async_api import async_playwright

        await self._shutdown()
        self._pw = await async_playwright().start()
        exe = self.cfg.executable_path or find_browser()
        kwargs: dict = {"headless": headless}
        if headless:  # 后台自动刷新用：精简浏览器进程，省约 100 MB（可见窗口保持默认，用户要亲自过验证码）
            kwargs["args"] = LEAN_ARGS
        if exe:
            kwargs["executable_path"] = exe
        else:
            kwargs["channel"] = "chrome"
        try:
            self._browser = await self._pw.chromium.launch(**kwargs)
        except Exception:
            if exe:
                raise
            kwargs["channel"] = "msedge"
            self._browser = await self._pw.chromium.launch(**kwargs)
        ctx = await self._browser.new_context(storage_state=state or None, locale="zh-CN")
        await ctx.add_init_script(script=f"({_CAPTURE_SCRIPT})({json.dumps(self.cfg.model_origin)});")
        if headless:  # 取 token 只需要页面结构，不加载图片 / 媒体 / 字体
            await ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
        return ctx

    async def _visible(self, page, text: str) -> bool:
        try:
            return await page.get_by_text(text, exact=True).first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    async def _page_text(self, page) -> str:
        try:
            return await page.evaluate("() => document.body ? document.body.innerText.slice(0, 4000) : ''")
        except Exception:  # noqa: BLE001
            return ""

    async def _where(self, page) -> str:
        """返回 model_ok / sso / model_loading / other"""
        o = _origin(page.url)
        if o == self.cfg.model_origin:
            return "model_ok" if await self._visible(page, GUIDE_TEXT) else "model_loading"
        if o == self.cfg.sso_origin:
            return "sso"
        return "other"

    async def _goto_model(self, page) -> str:
        try:
            await page.goto(self.cfg.model_origin + "/", wait_until="domcontentloaded", timeout=self.cfg.nav_timeout_ms)
        except Exception:  # noqa: BLE001
            return "network"
        end = time.monotonic() + self.cfg.detect_timeout_s
        where = "other"
        while time.monotonic() < end:
            self._check_stop()
            where = await self._where(page)
            if where in ("model_ok", "sso"):
                if where == "model_ok":
                    await self._sleep(0.8)  # 等前端把登录态写入
                return where
            await self._sleep(0.4)
        return where

    async def _sso_state(self, page) -> str:
        """严格确认是清华统一认证登录页：login_form / captcha / two_factor / unknown"""
        if _origin(page.url) != self.cfg.sso_origin:
            return "unknown"
        text = await self._page_text(page)
        if any(h in text for h in TWO_FACTOR_HINTS):
            return "two_factor"
        form = page.locator("#theform")
        try:
            if not (await form.count() == 1 and await form.locator("#i_user").is_visible()
                    and await form.locator("#i_pass").is_visible()):
                return "unknown"
            action_origin = await form.evaluate("f => new URL(f.getAttribute('action') || location.href, location.href).origin")
            if action_origin != self.cfg.sso_origin:
                return "unknown"
            if await form.locator("#i_code").count() and await form.locator("#i_code").first.is_visible():
                return "captcha"
        except Exception:  # noqa: BLE001
            return "unknown"
        return "login_form"

    async def _submit_password(self, page, creds: dict) -> str:
        """填写并提交。返回 ok / two_factor / captcha / bad_credentials / timeout / unknown"""
        form = page.locator("#theform")
        # 提交前最后再核对一次目标，防止中途被重定向到其他站点
        if await self._sso_state(page) != "login_form":
            return "unknown"
        try:
            await form.locator("#i_user").fill(creds["username"])
            await form.locator("#i_pass").fill(creds["password"])
            btn = form.get_by_text("登录", exact=True)
            if await btn.count():
                await btn.first.click()
            else:
                await form.locator("#i_pass").press("Enter")
        except Exception:  # noqa: BLE001 - 不把 Playwright 的异常文本（可能含输入值）往外传
            return "unknown"
        end = time.monotonic() + self.cfg.submit_timeout_s
        while time.monotonic() < end:
            self._check_stop()
            where = await self._where(page)
            if where == "model_ok":
                await self._sleep(0.8)
                return "ok"
            if where == "sso":
                text = await self._page_text(page)
                if any(h in text for h in TWO_FACTOR_HINTS):
                    return "two_factor"
                st = await self._sso_state(page)
                if st == "captcha":
                    return "captcha"
                if st == "login_form" and any(h in text for h in BAD_CRED_HINTS):
                    return "bad_credentials"
            await self._sleep(0.5)
        # 超时仍停在登录表单，保守地按“凭据可能错误”处理，避免反复提交导致锁号
        return "bad_credentials" if await self._sso_state(page) == "login_form" else "timeout"

    async def _ui_summary(self, page) -> list[str]:
        """只收集与 API/复制/token 相关的短 UI 标签（≤24 字），并过滤任何像 token 的长串，用于诊断页面改版。"""
        try:
            items = await page.evaluate("""() => {
              const out = [];
              for (const el of document.querySelectorAll('button,a,span,div,[role=button],li')) {
                const t = (el.innerText || '').trim();
                if (!t || t.length > 24 || el.children.length > 2) continue;
                if (!/api|复制|token|curl|使用/i.test(t)) continue;
                const r = el.getBoundingClientRect();
                const role = el.getAttribute('role') ? '[' + el.getAttribute('role') + ']' : '';
                out.push(el.tagName.toLowerCase() + role + ':' + t + ':' + (r.width > 0 && r.height > 0 ? 'vis' : 'hid'));
              }
              return Array.from(new Set(out)).slice(0, 30);
            }""")
        except Exception:  # noqa: BLE001
            return []
        return [x for x in items if not re.search(r"[A-Za-z0-9_\-\.]{24,}", x)]

    async def _grab_token(self, page) -> tuple[str | None, dict]:
        diag: dict = {"step": "guide"}
        try:
            await page.get_by_text(GUIDE_TEXT, exact=True).first.click(timeout=15_000)
            diag["step"] = "copy_button"
            await self._sleep(0.8)
            btn = page.get_by_role("button", name=COPY_TEXT, exact=True)
            if not await btn.count():
                btn = page.get_by_text(re.compile(r"^\s*复制\s*token\s*$", re.I))
            if not await btn.count():
                diag["ui"] = await self._ui_summary(page)
                return None, diag
            diag["copy_candidates"] = await btn.count()
            await btn.first.click(timeout=15_000)
            diag["step"] = "capture"
            try:
                await page.wait_for_function(
                    "() => typeof window.__alpCapturedToken === 'string' && window.__alpCapturedToken.length > 0", timeout=10_000)
            except Exception:  # noqa: BLE001
                diag["hooks_installed"] = await page.evaluate("() => Array.isArray(window.__alpCopyHooks)")
                diag["ui"] = await self._ui_summary(page)
                return None, diag
            token = await page.evaluate("() => { const v = window.__alpCapturedToken; delete window.__alpCapturedToken; return v; }")
            diag["via"] = await page.evaluate("() => (window.__alpCopyHooks || []).slice(-1)[0] || ''")
            diag["step"] = "done"
            return token, diag
        except Exception as e:  # noqa: BLE001
            diag["error"] = type(e).__name__
            if diag["step"] != "guide":
                diag["ui"] = await self._ui_summary(page)
            return None, diag

    async def _collect(self, saved_state, renew, credentials, allow_password, interactive, on_status) -> AuthResult:
        try:
            state = drop_site_state(saved_state, self.cfg.model_origin) if renew else saved_state
            submitted = False
            try:
                ctx = await self._launch(True, state)
            except Exception:  # noqa: BLE001
                return AuthResult("no_browser", "未能启动本机 Chrome/Edge，可设置环境变量 TSINGHUA_BROWSER_PATH 指定浏览器")
            page = await ctx.new_page()
            where = await self._goto_model(page)
            if where == "network":
                return AuthResult("timeout", "无法打开清华大模型系统（网络或站点不可用）")
            need_user_reason = ""
            if where != "model_ok":
                if where != "sso":
                    return AuthResult("flow_changed", "登录跳转与预期不符（未到达清华统一身份认证页面），已停止自动填写")
                st = await self._sso_state(page)
                if st == "two_factor":
                    need_user_reason = "清华统一认证要求二次验证"
                elif st == "captcha":
                    need_user_reason = "清华统一认证要求输入图形验证码"
                elif st == "unknown":
                    return AuthResult("flow_changed", "统一身份认证页面结构与预期不符，已停止自动填写；请在设置页打开登录窗口手动登录")
                else:
                    creds = credentials() if allow_password else None
                    if not creds:
                        need_user_reason = "需要登录清华统一身份认证" + ("（已暂停自动填写密码）" if not allow_password else "（未保存账号密码）")
                    else:
                        on_status("authenticating", "正在向清华统一身份认证提交登录")
                        r = await self._submit_password(page, creds)
                        submitted = True
                        creds = None
                        if r == "ok":
                            where = "model_ok"
                        elif r == "bad_credentials":
                            return AuthResult("bad_credentials", "统一身份认证未通过，可能是账号或密码错误；已暂停自动提交密码，请更新本机凭据",
                                              password_submitted=True)
                        elif r == "two_factor":
                            need_user_reason = "清华统一认证要求二次验证（短信/企业微信等）"
                        elif r == "captcha":
                            need_user_reason = "清华统一认证要求输入图形验证码"
                        elif r == "timeout":
                            return AuthResult("timeout", "登录提交后长时间无响应", password_submitted=True)
                        else:
                            return AuthResult("flow_changed", "登录页面结构与预期不符，已停止自动填写", password_submitted=submitted)

            if where != "model_ok":
                if not interactive:
                    return AuthResult("needs_user", need_user_reason + "，请在设置页点击「打开登录窗口」亲自完成", password_submitted=submitted)
                # 打开可见窗口，交给用户本人完成验证码 / 二次验证
                on_status("needs_user", need_user_reason + "，已打开浏览器窗口，请在窗口中完成")
                carry = await ctx.storage_state()
                ctx = await self._launch(False, carry)
                page = await ctx.new_page()
                where = await self._goto_model(page)
                if where == "sso" and allow_password and await self._sso_state(page) == "login_form" and not submitted:
                    creds = credentials()
                    if creds:
                        r = await self._submit_password(page, creds)
                        submitted, creds = True, None
                        if r == "bad_credentials":
                            return AuthResult("bad_credentials", "统一身份认证未通过，可能是账号或密码错误；已暂停自动提交密码",
                                              password_submitted=True)
                end = time.monotonic() + self.cfg.user_wait_timeout_s
                while (where := await self._where(page)) != "model_ok":
                    self._check_stop()
                    if time.monotonic() > end:
                        return AuthResult("needs_user", "等待用户完成登录验证超时", password_submitted=submitted)
                    if page.is_closed():
                        return AuthResult("needs_user", "登录窗口已被关闭，未完成认证", password_submitted=submitted)
                    await self._sleep(1.0)
                await self._sleep(1.5)

            token, diag = await self._grab_token(page)
            if not token:
                log.warning("取 token 失败，诊断：%s", diag)
                # 登录本身成功了：保留浏览器登录状态，下次不必再做二次验证
                return AuthResult("flow_changed", f"已登录，但没能通过「API使用指南 → 复制token」取得 token（卡在 {diag.get('step')} 步骤）",
                                  storage_state=await ctx.storage_state(), password_submitted=submitted, detail=diag)
            return AuthResult("ok", "已取得新 token", token=token, storage_state=await ctx.storage_state(),
                              password_submitted=submitted)
        except _Stopped:
            return AuthResult("stopped", "项目退出，登录流程已停止")
        except Exception as e:  # noqa: BLE001
            log.warning("登录流程异常：%s", type(e).__name__)
            return AuthResult("error", f"登录流程异常（{type(e).__name__}）")
