"""Literal private-file parsing and shared local runtime paths.

No configuration is read at import. Callers choose their file and allowed keys;
explicit process values win. Journal selection never moves or deletes history.
"""
from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Mapping, MutableMapping
from contextlib import closing
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DIR = PACKAGE_ROOT / ".terra-local" / "runtime"
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def read_environment_file(path: str | Path) -> tuple[Path, dict[str, str]]:
    """Read a bounded literal file atomically; never interpolate or execute it."""
    try:
        resolved = Path(path).expanduser().resolve()
        with resolved.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized")
        values = {}
        for raw_line in raw.decode("utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not _KEY.fullmatch(key) or "\x00" in value:
                raise ValueError("invalid assignment")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError("invalid quote")
                value = value[1:-1]
            if len(key) + len(value) + 1 > 32766:
                raise ValueError("oversized value")
            values[key] = value
        return resolved, values
    except (OSError, TypeError, ValueError, UnicodeError):
        raise ValueError("The private environment file is unavailable or invalid.") from None


def load_environment_file(path: str | Path,
                          environment: MutableMapping[str, str] | None = None) -> Path:
    resolved, values = read_environment_file(path)
    destination = os.environ if environment is None else environment
    for key, value in values.items():
        destination.setdefault(key, value)
    return resolved


def sms_journal_path(values: Mapping[str, str], state_dir: str | Path | None = None) -> Path:
    explicit = values.get("TERRA_SMS_DB_PATH")
    path = (Path(explicit) if explicit else
            Path(state_dir or DEFAULT_STATE_DIR).expanduser().resolve() / "sms.sqlite3")
    if not path.is_absolute():
        path = PACKAGE_ROOT / path
    return path.expanduser().resolve()


class JournalSelectionError(ValueError):
    """An operator choice would silently abandon existing no-resend evidence."""


def check_sms_journal_selection(selected: Path, state_dir: str | Path | None = None) -> None:
    root = Path(state_dir or DEFAULT_STATE_DIR).expanduser().resolve()
    previous_locations = [root / "sms.sqlite3"]
    if root == DEFAULT_STATE_DIR.resolve():
        previous_locations.append(PACKAGE_ROOT / ".terra-local" / "sms.sqlite3")
    for previous in previous_locations:
        previous = previous.resolve()
        if selected == previous or not previous.exists():
            continue
        try:
            with closing(sqlite3.connect(previous.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                has_history = any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                                  for table in ("sms_delivery", "sms_attempt") if table in tables)
                paused = ("sms_settings" in tables and db.execute(
                    "SELECT 1 FROM sms_settings WHERE key='disabled' AND value='true'").fetchone())
        except (OSError, sqlite3.Error):
            raise JournalSelectionError("The previous SMS journal needs review before changing its location.") from None
        if has_history or paused:
            raise JournalSelectionError(
                "SMS journal selection conflicts with existing delivery history or a saved pause. "
                "Use the existing journal or review an explicit migration.")
