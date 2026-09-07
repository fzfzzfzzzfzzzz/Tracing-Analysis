from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

import pytest

from tracegraph.graph import TraceGraph
from tracegraph.context_engine import GraphConstrainedPolicy
from tracegraph.legacy import LegacyArtifact, load_artifact, load_legacy_artifact


ROOT = Path(os.environ.get("TRACEGRAPH_FROZEN_ROOT", Path(__file__).resolve().parents[1]))


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


def test_legacy_manifest_rejects_bad_entries_hashes_sizes_and_missing_files(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": [1]}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid entry"):
        load_legacy_artifact(manifest)

    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    cases = [
        ({"path": "missing.json", "sha256": "00" * 32}, FileNotFoundError, "missing"),
        ({"path": "payload.json", "sha256": "00" * 32}, ValueError, "hash mismatch"),
        (
            {"path": "payload.json", "sha256": sha256(payload.read_bytes()).hexdigest(), "bytes": 3},
            ValueError,
            "size mismatch",
        ),
    ]
    for entry, error_type, message in cases:
        manifest.write_text(json.dumps({"artifacts": [entry]}), encoding="utf-8")
        with pytest.raises(error_type, match=message):
            load_legacy_artifact(manifest)


def test_legacy_loader_supports_directory_plain_json_and_typed_v4(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "plain", "value": 1}), encoding="utf-8")
    artifact = load_legacy_artifact(tmp_path, verify_hashes=False)
    assert isinstance(artifact, LegacyArtifact)
    assert artifact.kind == "json"
    assert artifact.linked_hashes_verified is False

    array_path = tmp_path / "array.json"
    array_path.write_text("[1, 2]", encoding="utf-8")
    assert load_legacy_artifact(array_path).payload == (1, 2)

    graph = TraceGraph("typed")
    snapshot = GraphConstrainedPolicy().snapshot(graph, {}, 32)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")
    loaded_snapshot = load_legacy_artifact(snapshot_path)
    assert loaded_snapshot.snapshot_hash == snapshot.snapshot_hash

    plan = GraphConstrainedPolicy().materialize(snapshot, "hello", "local")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    loaded_plan = load_legacy_artifact(plan_path)
    assert loaded_plan.to_dict() == plan.to_dict()

    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_legacy_artifact(tmp_path / "absent.json")
