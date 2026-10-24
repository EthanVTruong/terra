"""Terra-owned credential storage; never reads another application's credentials."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import tempfile
import threading
import time


class VaultError(Exception):
    """Storage or operating-system protection failed (no sensitive details)."""


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_PREFIX = b"TERRA-DPAPI-1\n"


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    """Protect for the current Windows user, without an interactive prompt."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        operation = crypt32.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        operation = crypt32.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), "Terra ChatGPT credentials", None, None, None,
                1, ctypes.byref(target))
    operation.restype = wintypes.BOOL
    if not operation(*args):
        raise VaultError("credential_protection_failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


class Vault:
    """Atomic protected JSON plus a native lock shared by cooperating processes."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).absolute()
        self.path = self.directory / "chatgpt-credentials.vault"
        self.lock_path = self.directory / "chatgpt-credentials.lock"
        try:
            if self.directory.is_symlink():
                raise VaultError("credential_storage_failed")
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._permissions(self.directory, 0o700)
        except (OSError, ValueError) as exc:
            raise VaultError("credential_storage_failed") from exc
        with _LOCKS_GUARD:
            self._thread_lock = _LOCKS.setdefault(str(self.path).casefold(), threading.RLock())

    @staticmethod
    def _permissions(path: Path, mode: int) -> None:
        if path.is_symlink():
            raise VaultError("credential_storage_failed")
        if os.name != "nt":
            if path.stat().st_uid != os.getuid():
                raise VaultError("credential_storage_failed")
            path.chmod(mode)

    def _protect(self, data: bytes) -> bytes:
        return _PREFIX + _dpapi(data) if os.name == "nt" else data

    def _unprotect(self, data: bytes) -> bytes:
        if os.name == "nt":
            if not data.startswith(_PREFIX):
                raise VaultError("credential_protection_failed")
            return _dpapi(data[len(_PREFIX):], decrypt=True)
        return data

    @contextmanager
    def locked(self):
        """Serialize read/refresh/write as one transaction, with a bounded wait."""
        with self._thread_lock:
            handle = None
            acquired = False
            try:
                if self.lock_path.is_symlink():
                    raise VaultError("credential_storage_failed")
                fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                handle = os.fdopen(fd, "r+b", buffering=0)
                self._permissions(self.lock_path, 0o600)
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                deadline = time.monotonic() + 15
                while True:
                    try:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise VaultError("credential_storage_busy") from None
                        time.sleep(0.02)
                yield
            except (OSError, ValueError) as exc:
                raise VaultError("credential_storage_failed") from exc
            finally:
                if handle is not None:
                    if acquired:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    handle.close()

    def load(self) -> dict:
        try:
            if not self.path.exists():
                return {"version": 1, "accounts": {}, "active_account_id": None}
            self._permissions(self.path, 0o600)
            with self.path.open("rb") as handle:
                encoded = handle.read(1_048_577)
            if len(encoded) > 1_048_576:
                raise VaultError("credential_storage_failed")
            data = json.loads(self._unprotect(encoded))
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("accounts"), dict):
                raise VaultError("credential_storage_failed")
            return data
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise VaultError("credential_storage_failed") from exc

    def save(self, data: dict) -> None:
        temporary = None
        try:
            encoded = self._protect(json.dumps(data, allow_nan=False, separators=(",", ":")).encode())
            fd, temporary = tempfile.mkstemp(prefix=".chatgpt-", dir=self.directory)
            with os.fdopen(fd, "wb") as handle:
                self._permissions(Path(temporary), 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.is_symlink():
                raise VaultError("credential_storage_failed")
            os.replace(temporary, self.path)
            temporary = None
            self._permissions(self.path, 0o600)
        except (OSError, ValueError, TypeError) as exc:
            raise VaultError("credential_storage_failed") from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
