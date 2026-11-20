"""本机加密存储：Windows 用当前用户 DPAPI；其他系统用 keyring（系统凭据存储）。

存放清华账号密码、浏览器登录状态（Playwright storageState，含 Cookie）、token 记录。
任何内容都不以明文落盘、不进日志、不进命令行参数。
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

APP_ENTROPY = b"ai-learning-platform/tsinghua-model/v1"  # 附加熵：绑定到本应用
KEYRING_SERVICE = "ai-learning-platform.tsinghua"


class SecureStoreError(RuntimeError):
    pass


# ---------------- Windows DPAPI（ctypes 直调 crypt32，不起子进程） ----------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> tuple[_Blob, Any]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def dpapi_protect(data: bytes, entropy: bytes | None = APP_ENTROPY) -> bytes:
    if sys.platform != "win32":
        raise SecureStoreError("DPAPI 仅在 Windows 可用")
    inp, _keep = _to_blob(data)
    ent, _keep2 = _to_blob(entropy) if entropy else (None, None)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, ctypes.byref(ent) if ent else None,
                                                None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecureStoreError("Windows DPAPI 加密失败")
    return _from_blob(out)


def dpapi_unprotect(data: bytes, entropy: bytes | None = APP_ENTROPY) -> bytes:
    if sys.platform != "win32":
        raise SecureStoreError("DPAPI 仅在 Windows 可用")
    inp, _keep = _to_blob(data)
    ent, _keep2 = _to_blob(entropy) if entropy else (None, None)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, ctypes.byref(ent) if ent else None,
                                                  None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecureStoreError("无法用当前 Windows 用户解密（文件可能来自其他用户或已损坏）")
    return _from_blob(out)


class SecureStore:
    """按名称保存 JSON 对象。Windows：<dir>/<name>.dpapi；其他系统：keyring。"""

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _path(self, name: str) -> Path:
        if not name.replace("_", "").isalnum():
            raise ValueError("非法名称")
        return self.dir / f"{name}.dpapi"

    def save(self, name: str, obj: dict) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        if sys.platform == "win32":
            enc = dpapi_protect(raw)
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self._path(name)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            try:
                with open(tmp, "wb") as f:
                    f.write(enc)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        else:  # pragma: no cover - 非 Windows 平台
            import keyring

            keyring.set_password(KEYRING_SERVICE, name, raw.decode("utf-8"))

    def load(self, name: str) -> dict | None:
        if sys.platform == "win32":
            path = self._path(name)
            if not path.exists():
                return None
            return json.loads(dpapi_unprotect(path.read_bytes()).decode("utf-8"))
        import keyring  # pragma: no cover

        v = keyring.get_password(KEYRING_SERVICE, name)  # pragma: no cover
        return json.loads(v) if v else None  # pragma: no cover

    def delete(self, name: str) -> None:
        if sys.platform == "win32":
            self._path(name).unlink(missing_ok=True)
            return
        import keyring  # pragma: no cover

        try:  # pragma: no cover
            keyring.delete_password(KEYRING_SERVICE, name)
        except keyring.errors.PasswordDeleteError:  # pragma: no cover
            pass

    def exists(self, name: str) -> bool:
        try:
            return self.load(name) is not None
        except (SecureStoreError, ValueError):
            return False
