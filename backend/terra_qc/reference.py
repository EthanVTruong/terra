"""Load only the selected, immutable e1 statistical reference."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import qc

REFERENCE_ID = "terra-qc-v4-e1e59ac0ea395d80"
REFERENCE_SHA256 = "809c78b69d80d93d73f5b4b75ca6e74b0759ddcebf1e17fa2450386b44fcd90f"
DEFAULT_REFERENCE = Path(__file__).resolve().parents[2] / "config" / "terra" / "reference.json"


def load_reference(path: str | Path | None = None) -> qc.Reference:
    """Reject changed bytes and identity; never fit or load executable models."""
    raw = Path(path or DEFAULT_REFERENCE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != REFERENCE_SHA256:
        raise ValueError("The selected statistical reference SHA-256 does not match")
    reference = qc.reference_from_payload(json.loads(raw))
    if reference.manifest["model_id"] != REFERENCE_ID:
        raise ValueError("The statistical reference identity does not match")
    return reference
