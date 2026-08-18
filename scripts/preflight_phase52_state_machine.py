#!/usr/bin/env python3
"""Run corpus-wide Phase 5.2 state-machine integrity checks without labels."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tracegraph.archive import ArchiveStore  # noqa: E402
from tracegraph.decision_state import stable_digest  # noqa: E402
from tracegraph.graph import TraceGraph  # noqa: E402
from tracegraph.lifecycle_annotation import file_sha256, load_phase52_config  # noqa: E402
from tracegraph.lifecycle_state_machine import (  # noqa: E402
    build_forbidden_offline_projection,
    load_tool_effect_registry,
    replay_lifecycle_state_machine,
)
from tracegraph.phase5_offline import build_strict_prefix  # noqa: E402
from tracegraph.provider_cost import close_protocol_messages  # noqa: E402
from tracegraph.schema import EdgeType, NodeType  # noqa: E402
from tracegraph.trajectory_artifacts import sha256_json  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _future_copy(prefix: TraceGraph) -> TraceGraph:
    future = TraceGraph.from_dict(prefix.to_dict())
    suffix = stable_digest(prefix.metadata)[:16]
    call = future.create_node(
        NodeType.TOOL_CALL,
        {"tool_name": "future_sentinel", "arguments": {}},
        int(prefix.metadata["cutoff_step"]) + 100,
        node_id=f"future_call_{suffix}",
        metadata={"source_message_ordinal": 100000, "tool_name": "future_sentinel"},
    )
    result = future.create_node(
        NodeType.OBSERVATION,
        {"future": True},
        int(prefix.metadata["cutoff_step"]) + 101,
        node_id=f"future_result_{suffix}",
        metadata={"source_message_ordinal": 100001, "status": "success"},
    )
    future.connect(call.node_id, result.node_id, EdgeType.PRODUCES)
    return future


def _protocol_valid(messages: list[dict[str, Any]]) -> bool:
    closure = close_protocol_messages(messages, set(range(1, len(messages) + 1)))
    return closure.valid and len(closure.ordinals) == len(messages)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs/phase52_lifecycle_modeling.json",
    )
    args = parser.parse_args()
    config = load_phase52_config(args.config)
    output_root = REPO_ROOT / config["output_root"]
    output_path = output_root / "state_machine_preflight.json"
    if output_path.exists():
        raise FileExistsError("state-machine preflight artifact is immutable")
    manifest = _load_json(REPO_ROOT / config["inputs"]["development_manifest"])
    registry = load_tool_effect_registry(config)
    source_rows = {str(item["session_id"]): item for item in manifest["sources"]}
    graphs: dict[str, TraceGraph] = {}
    archive_valid: dict[str, bool] = {}
    for session_id, source in source_rows.items():
        source_path = REPO_ROOT / str(source["source_path"])
        if file_sha256(source_path) != source["source_file_sha256"]:
            raise RuntimeError(f"source graph drift: {source_path}")
        graph = TraceGraph.load(source_path)
        if sha256_json(graph.to_dict()) != source["event_graph_sha256"]:
            raise RuntimeError(f"normalized graph drift: {source_path}")
        graphs[session_id] = graph
        archive_valid[session_id] = not ArchiveStore(source_path.parent / "archive").verify_all()

    records: list[dict[str, Any]] = []
    disposition_counts: Counter[str] = Counter()
    for row in manifest["prefixes"]:
        session_id = str(row["session_id"])
        ordinal = row.get("source_message_ordinal")
        prefix = build_strict_prefix(
            graphs[session_id],
            cutoff_step=int(row["cutoff_step"]),
            source_message_ordinal=int(ordinal) if ordinal is not None else None,
            prefix_id=str(row["prefix_id"]),
        )
        graph_before = stable_digest(prefix.to_dict())
        first = replay_lifecycle_state_machine(prefix, registry=registry)
        second = replay_lifecycle_state_machine(prefix, registry=registry)
        future = replay_lifecycle_state_machine(_future_copy(prefix), registry=registry)
        projection = build_forbidden_offline_projection(prefix, first)
        repeated_projection = build_forbidden_offline_projection(prefix, second)
        disposition_counts.update(item.disposition for item in first)
        records.append(
            {
                "prefix_id": row["prefix_id"],
                "prediction_count": len(first),
                "deterministic": first == second and projection == repeated_projection,
                "future_suffix_independent": first == future,
                "event_graph_unchanged": graph_before == stable_digest(prefix.to_dict()),
                "archive_valid": archive_valid[session_id],
                "protocol_valid": _protocol_valid(projection["messages"]),
                "projection_send_forbidden": projection["never_send_to_provider"],
            }
        )
    expected_predictions = int(config["annotation"]["expected_prefix_span_units"])
    rates = {
        key: statistics.fmean(int(item[key]) for item in records)
        for key in (
            "deterministic",
            "future_suffix_independent",
            "event_graph_unchanged",
            "archive_valid",
            "protocol_valid",
            "projection_send_forbidden",
        )
    }
    report: dict[str, Any] = {
        "schema_version": "phase52_state_machine_preflight_v1",
        "counts": {
            "prefixes": len(records),
            "predictions": sum(int(item["prediction_count"]) for item in records),
            "dispositions": dict(sorted(disposition_counts.items())),
        },
        "rates": rates,
        "checks": {
            "all_261_prefixes": len(records) == 261,
            "all_1092_predictions": (
                sum(int(item["prediction_count"]) for item in records) == expected_predictions
            ),
            "all_integrity_rates_100_percent": all(value == 1.0 for value in rates.values()),
        },
        "uses_machine_labels": False,
        "held_out_claim_authorized": False,
        "provider_requests": 0,
        "rows_sha256": sha256_json(records),
    }
    report["report_sha256"] = sha256_json(report)
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if all(report["checks"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
