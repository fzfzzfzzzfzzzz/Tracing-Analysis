from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracegraph.graph import TraceGraph
from tracegraph.legacy import LegacyArtifact, load_artifact, load_legacy_artifact


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_v01_manifest_is_read_only_and_hash_verified() -> None:
    path = ROOT / "outputs" / "compression_audit" / "v0_1_handoff" / "manifest.json"
    before = path.read_bytes()
    artifact = load_legacy_artifact(path)
    assert isinstance(artifact, LegacyArtifact)
    assert artifact.schema_version == "compression_audit_manifest_v1"
    assert artifact.sha256 == (
        "d33c12ce2b2c5dacefa6ce353fb46df80d2a766510aa398ae1a07961d38a249b"
    )
    assert artifact.linked_hashes_verified
    assert path.read_bytes() == before
    with pytest.raises(TypeError):
        artifact.payload["release"] = "changed"


def test_legacy_graph_schema_is_upgraded_only_in_memory(tmp_path: Path) -> None:
    value = {
        "schema_version": "1.0",
        "session_id": "legacy",
        "metadata": {},
        "nodes": [],
        "edges": [],
    }
    path = tmp_path / "旧记录.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8", newline="\n")
    before = path.read_bytes()
    graph = load_artifact(path)
    assert isinstance(graph, TraceGraph)
    assert graph.metadata["loaded_schema_version"] == "1.0"
    assert path.read_bytes() == before


def test_jsonl_loader_handles_utf8_and_crlf_without_rewriting(tmp_path: Path) -> None:
    path = tmp_path / "记录.jsonl"
    path.write_bytes('{"内容":"甲"}\r\n{"内容":"乙"}\r\n'.encode())
    before = path.read_bytes()
    artifact = load_legacy_artifact(path)
    assert isinstance(artifact, LegacyArtifact)
    assert [row["内容"] for row in artifact.payload] == ["甲", "乙"]
    assert path.read_bytes() == before


def test_manifest_path_traversal_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": "legacy_test",
            "artifacts": [{
                "path": "../outside.json",
                "bytes": 2,
                "sha256": "00" * 32,
            }],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="escapes"):
        load_legacy_artifact(manifest)
