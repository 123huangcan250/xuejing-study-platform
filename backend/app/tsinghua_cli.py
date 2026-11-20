"""清华大模型服务本机设置工具（密码只在本机终端无回显输入，加密保存，不经过网络、命令行参数或日志）。

用法（在 backend 目录）：
  .venv\\Scripts\\python -m app.tsinghua_cli setup        设置 / 更新账号密码（改密码后也用它）
  .venv\\Scripts\\python -m app.tsinghua_cli login        打开可见浏览器窗口登录并取得 token
  .venv\\Scripts\\python -m app.tsinghua_cli status       查看登录状态（不显示 token）
  .venv\\Scripts\\python -m app.tsinghua_cli clear        删除本机保存的账号密码
  .venv\\Scripts\\python -m app.tsinghua_cli logout       删除 token 和浏览器登录状态
  .venv\\Scripts\\python -m app.tsinghua_cli import-dsh   从本机 DSH 清华桥接导入已加密的凭据/浏览器会话
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from .services.tsinghua.auth_worker import PlaywrightAuthWorker
from .services.tsinghua.manager import CREDENTIALS, SESSION, TokenManager
from .services.tsinghua.runtime import SECURE_DIR
from .services.tsinghua.secure_store import SecureStore, SecureStoreError, dpapi_unprotect

BACKEND = os.environ.get("ALP_BACKEND", "http://127.0.0.1:8400")


def _manager() -> TokenManager:
    return TokenManager(SecureStore(SECURE_DIR), PlaywrightAuthWorker())


def _backend(path: str, body: dict | None = None) -> dict | None:
    """通知正在运行的后端（只发送控制指令，从不发送凭据）。后端没运行时返回 None。"""
    try:
        req = urllib.request.Request(BACKEND + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def cmd_setup(_a) -> int:
    print("仅用于清华统一身份认证（https://id.tsinghua.edu.cn）。凭据由当前 Windows 用户 DPAPI 加密保存在本机。")
    username = input("账号（学号/工号）：").strip()
    password = getpass.getpass("密码（输入时不显示）：")
    if not username or not password:
        print("账号或密码为空，未保存。")
        return 2
    _manager().set_credentials(username, password)
    password = ""  # noqa: F841 - 尽快丢弃引用
    print("已加密保存，并已解除“暂停自动提交密码”。")
    if _backend("/api/tsinghua/refresh", {"interactive": False}) is not None:
        print("已通知正在运行的后端立即尝试登录；如遇验证码/二次验证会弹出浏览器窗口。")
    else:
        print("后端未运行；启动项目后会在需要时自动登录。")
    return 0


def cmd_status(_a) -> int:
    st = _backend("/api/tsinghua/status")
    if st is None:
        m = _manager()
        m.restore()
        st = m.public_status()
        st["running"] = False
    fmt = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "-"  # noqa: E731
    print(f"状态：{st['state']}  {st['message']}")
    print(f"token 有效：{st['token_valid']}  到期：{fmt(st.get('expires_at'))}  下次更新：{fmt(st.get('next_run_at'))}")
    print(f"已存账号密码：{st['has_credentials']}  浏览器会话：{st['has_session']}  暂停自动提交密码：{st['password_blocked']}")
    return 0


def cmd_login(_a) -> int:
    st = _backend("/api/tsinghua/refresh", {"interactive": True})
    if st is not None:
        print("后端正在运行：已请求它打开登录窗口，请在弹出的浏览器中完成登录。完成后可运行 status 查看。")
        return 0

    async def run():
        m = _manager()
        m.restore()
        m._auto_interactive_used = False
        m._interactive_pending = True
        ok = await m.refresh_once()
        await asyncio.to_thread(m.worker.close)
        print("已取得新 token。" if ok else f"未取得 token：{m.status.message}")
        return 0 if ok else 1

    return asyncio.run(run())


def cmd_clear(_a) -> int:
    _manager().clear_credentials()
    print("已删除本机保存的账号密码。")
    return 0


def cmd_logout(_a) -> int:
    if _backend("/api/tsinghua/logout", {}) is None:
        _manager().logout()
    print("已删除 token 和浏览器登录状态。")
    return 0


def cmd_import_dsh(_a) -> int:
    """DSH 桥接用当前 Windows 用户 DPAPI（无附加熵）加密；这里解密后用本应用的 DPAPI（带附加熵）重新加密保存。"""
    if sys.platform != "win32":
        print("仅支持 Windows。")
        return 2
    base = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "tsinghua-model-bridge"
    store = SecureStore(SECURE_DIR)
    imported = []
    for src, name in (("credentials.dpapi", CREDENTIALS), ("browser-session.dpapi", SESSION)):
        f = base / src
        if not f.exists():
            continue
        try:
            obj = json.loads(dpapi_unprotect(f.read_bytes(), entropy=None).decode("utf-8"))
        except (SecureStoreError, ValueError):
            print(f"{src} 无法用当前 Windows 用户解密，跳过。")
            continue
        if name == CREDENTIALS and not (isinstance(obj.get("username"), str) and isinstance(obj.get("password"), str)):
            print(f"{src} 格式不符，跳过。")
            continue
        store.save(name, obj)
        imported.append("账号密码" if name == CREDENTIALS else "浏览器登录状态")
        obj = None
    print("已导入：" + ("、".join(imported) if imported else "无"))
    if imported and _backend("/api/tsinghua/refresh", {"interactive": False}) is not None:
        print("已通知正在运行的后端重新认证。")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tsinghua_cli", description="清华大模型服务本机设置")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("setup", cmd_setup), ("status", cmd_status), ("login", cmd_login), ("clear", cmd_clear),
                     ("logout", cmd_logout), ("import-dsh", cmd_import_dsh)):
        sub.add_parser(name).set_defaults(fn=fn)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
