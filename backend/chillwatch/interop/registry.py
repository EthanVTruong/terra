"""Logger registry: ``config/nodes.yaml`` (copy ``config/nodes.example.yaml``) -> :class:`Node`."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .. import config
from .contract import Node


def load_nodes(path: str | Path | None = None) -> dict[str, Node]:
    p = Path(path) if path else config.config_dir() / "nodes.yaml"
    if not p.exists():
        raise FileNotFoundError(f"no logger registry at {p}; copy config/nodes.example.yaml to config/nodes.yaml")
    data = json.loads(p.read_text()) if p.suffix == ".json" else yaml.safe_load(p.read_text())
    nodes = [Node.from_dict(d) for d in (data.get("nodes", data) if isinstance(data, dict) else data) or []]
    return {n.node_id: n for n in nodes}


def get_node(node_id: str, path: str | Path | None = None) -> Node:
    nodes = load_nodes(path)
    if node_id not in nodes:
        raise KeyError(f"node {node_id!r} not in the registry (have: {', '.join(sorted(nodes)) or 'none'})")
    return nodes[node_id]
