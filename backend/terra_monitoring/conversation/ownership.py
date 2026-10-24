"""Fail-closed local process identity for explicit abandoned-request recovery."""
from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading

_ACTIVE = set()
_LOCK = threading.Lock()


def active(path, request_id, enabled=None):
    key = (str(Path(path).resolve()), request_id)
    with _LOCK:
        if enabled is True:
            _ACTIVE.add(key)
        elif enabled is False:
            _ACTIVE.discard(key)
        return key in _ACTIVE


def _process(pid):
    """Return active + start identity, exited, or unknown; never signal a PID."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "unknown", None
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            api.OpenProcess.restype = wintypes.HANDLE
            api.CloseHandle.argtypes = (wintypes.HANDLE,)
            api.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            api.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
            handle = api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return ("exited", None) if ctypes.get_last_error() == 87 else ("unknown", None)
            try:
                exit_code = wintypes.DWORD()
                if not api.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return "unknown", None
                if exit_code.value != 259:  # STILL_ACTIVE
                    return "exited", None
                created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
                if not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user)):
                    return "unknown", None
                return "active", str((created.dwHighDateTime << 32) | created.dwLowDateTime)
            finally:
                api.CloseHandle(handle)
        if sys.platform.startswith("linux"):
            if not Path("/proc/self/stat").is_file():
                return "unknown", None
            path = Path("/proc") / str(pid) / "stat"
            try:
                fields = path.read_text().rsplit(")", 1)[1].split()
            except FileNotFoundError:
                return "exited", None
            if fields[0] == "Z":
                return "exited", None
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return "active", boot + ":" + fields[19]
        if sys.platform == "darwin":
            result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=2)
            if result.returncode == 1 and not result.stdout.strip():
                return "exited", None
            if result.returncode == 0 and result.stdout.strip():
                return "active", result.stdout.strip()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return "unknown", None


def current_owner():
    status, started = _process(os.getpid())
    return {"version": 1, "pid": os.getpid(), "started": started, "platform": sys.platform,
            "host": socket.gethostname()} if status == "active" and started else None


def owner_status(owner, path=None, request_id=None):
    if path is not None and request_id is not None and active(path, request_id):
        return "active"
    if (not isinstance(owner, dict) or type(owner.get("version")) is not int or owner["version"] != 1 or not owner.get("started")
            or owner.get("platform") != sys.platform or owner.get("host") != socket.gethostname()):
        return "unknown"
    token = owner["started"]
    if (not isinstance(token, str)
            or sys.platform == "win32" and (not token.isdigit() or not 0 < int(token) < 2**64 or str(int(token)) != token)
            or sys.platform.startswith("linux") and not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:(?:0|[1-9][0-9]*)", token)
            or sys.platform == "darwin" and not re.fullmatch(r"[A-Za-z]{3} [A-Za-z]{3}\s+\d{1,2} \d{2}:\d{2}:\d{2} \d{4}", token)):
        return "unknown"
    status, started = _process(owner.get("pid"))
    if status == "exited":
        return "exited"
    if status == "active" and started:
        same = " ".join(started.split()) == " ".join(token.split()) if sys.platform == "darwin" else started == token
        return "active" if same else "exited"
    return "unknown"
