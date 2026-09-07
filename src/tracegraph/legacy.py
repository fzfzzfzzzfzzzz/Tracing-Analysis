"""Read-only boundary for historical TraceGraph JSON and JSONL artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .context_engine.lifecycle_models import (
    DecisionLifecycleGraph,
    LiveSubgraph,
    LivenessRoots,
)
from .context_engine.types import ContextPlan, MemorySnapshot
from .graph import TraceGraph


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class LegacyArtifact:
    """Immutable legacy payload when no richer in-memory type applies."""

    kind: str
    schema_version: str
    payload: Any
    source_path: str
    sha256: str
    linked_hashes_verified: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))


def _verify_linked_artifacts(root: Path, value: Mapping[str, Any]) -> bool:
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    for item in artifacts:
        if not isinstance(item, Mapping) or not item.get("path") or not item.get("sha256"):
            raise ValueError("legacy artifact manifest has an invalid entry")
        candidate = (root / str(item["path"])).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("legacy artifact path escapes its manifest directory") from error
        if not candidate.is_file():
            raise FileNotFoundError(f"legacy artifact is missing: {item['path']}")
        actual = sha256(candidate.read_bytes()).hexdigest()
        if actual != str(item["sha256"]):
            raise ValueError(f"legacy artifact hash mismatch: {item['path']}")
        declared_bytes = item.get("bytes")
        if declared_bytes is not None and candidate.stat().st_size != int(declared_bytes):
            raise ValueError(f"legacy artifact size mismatch: {item['path']}")
    return True


def _typed_json(value: Mapping[str, Any]) -> Any:
    schema = str(value.get("schema_version", "unknown"))
    if "nodes" in value and "edges" in value and "session_id" in value:
        return TraceGraph.from_dict(dict(value))
    if schema == "memory_snapshot_v4":
        return MemorySnapshot.from_dict(value)
    if schema == "context_plan_v4":
        return ContextPlan.from_dict(value)
    if schema.startswith("decision_lifecycle_graph"):
        return DecisionLifecycleGraph.from_dict(value)
    if schema.startswith("liveness_roots"):
        return LivenessRoots.from_dict(value)
    if schema.startswith("live_subgraph"):
        return LiveSubgraph.from_dict(value)
    return None


def load_legacy_artifact(path: str | Path, *, verify_hashes: bool = True) -> Any:
    """Load an old artifact without modifying it or writing a migrated copy."""

    source = Path(path)
    if source.is_dir():
        source = source / "manifest.json"
    if not source.is_file():
        raise FileNotFoundError(f"legacy artifact does not exist: {source}")
    raw = source.read_bytes()
    digest = sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    if source.suffix.casefold() == ".jsonl":
        rows = tuple(
            json.loads(line) for line in text.splitlines() if line.strip()
        )
        return LegacyArtifact(
            kind="jsonl",
            schema_version="jsonl",
            payload=rows,
            source_path=str(source.resolve()),
            sha256=digest,
        )
    value = json.loads(text)
    if not isinstance(value, Mapping):
        return LegacyArtifact(
            kind="json",
            schema_version="unknown",
            payload=value,
            source_path=str(source.resolve()),
            sha256=digest,
        )
    typed = _typed_json(value)
    if typed is not None:
        return typed
    verified = _verify_linked_artifacts(source.parent, value) if verify_hashes else False
    schema = str(value.get("schema_version", "unknown"))
    kind = "manifest" if isinstance(value.get("artifacts"), list) else "json"
    return LegacyArtifact(
        kind=kind,
        schema_version=schema,
        payload=value,
        source_path=str(source.resolve()),
        sha256=digest,
        linked_hashes_verified=verified,
    )


load_artifact = load_legacy_artifact

__all__ = ["LegacyArtifact", "load_artifact", "load_legacy_artifact"]
