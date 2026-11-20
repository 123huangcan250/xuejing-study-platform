"""远程访问的访问密码设置（只能在本机命令行操作，密码无回显输入，以 scrypt 哈希保存）。

  .venv\\Scripts\\python -m app.access_cli set         设置 / 修改访问密码（改密码会让所有已登录设备失效）
  .venv\\Scripts\\python -m app.access_cli status      是否已设置
  .venv\\Scripts\\python -m app.access_cli logout-all  让所有已登录的设备失效
  .venv\\Scripts\\python -m app.access_cli disable     取消访问密码（之后拒绝一切远程访问，只剩本机 localhost）
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .services import access


def cmd_set(_a) -> int:
    print(f"设置远程访问密码（至少 {access.MIN_PASSWORD_LEN} 位；建议用随机的长密码，Tailscale 之外它是第二道防线）。")
    p1 = getpass.getpass("新密码（输入时不显示）：")
    if p1 != getpass.getpass("再输一次："):
        print("两次输入不一致，未修改。")
        return 2
    try:
        access.set_password(p1)
    except ValueError as e:
        print(e)
        return 2
    print("已设置。所有已登录的设备需要重新登录。")
    return 0


def cmd_status(_a) -> int:
    ok = access.configured()
    print("访问密码：" + ("已设置（所有访问都需要登录）" if ok else "未设置（只允许本机 localhost 访问，远程被拒绝）"))
    return 0 if ok else 3  # 退出码供脚本判断：0 已设置，3 未设置


def cmd_logout_all(_a) -> int:
    access.logout_all()
    print("已让所有已登录的设备失效。")
    return 0


def cmd_disable(_a) -> int:
    access.disable()
    print("已取消访问密码。现在只有本机 localhost 能访问；请同时停止 Tailscale Serve（remote.ps1 -Stop）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # 非 UTF-8 的控制台（如 cp1252）输出中文会崩溃
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    p = argparse.ArgumentParser(prog="access_cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("set", cmd_set), ("status", cmd_status), ("logout-all", cmd_logout_all), ("disable", cmd_disable)):
        sub.add_parser(name).set_defaults(fn=fn)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
