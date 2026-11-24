"""Interoperability layer: the logger record contract, readers, validation and reference fusion.

Data flow (see docs/INTEROP.md)::

    Arduino (wire v1) -> bridge (host UTC stamp, JSONL) -> readers -> rules -> hourly
        -> fusion (physics + ML weighted by uncertainty) -> chill summary / report
"""

from .contract import WIRE_VERSION, DeviceRecord, Issue, Node
from .fusion import estimate_rho, fuse, reference_expectation, score
from .hourly import gaps, samples_to_hourly
from .pipeline import Result, run
from .readers import read_any, read_capture, read_diy
from .registry import get_node, load_nodes
from .rules import validate_samples
from .wire import WireError, decode, encode

__all__ = [
    "WIRE_VERSION", "DeviceRecord", "Issue", "Node", "Result", "WireError",
    "decode", "encode", "estimate_rho", "fuse", "gaps", "get_node", "load_nodes", "read_any",
    "read_capture", "read_diy", "reference_expectation", "run", "samples_to_hourly", "score",
    "validate_samples",
]
