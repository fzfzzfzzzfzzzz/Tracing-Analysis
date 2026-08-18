from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tracegraph.archive import ArchiveStore
from tracegraph.graph import TraceGraph
from tracegraph.phase5_offline import (
    F5_G1_THRESHOLDS,
    adjudicate_f5_g1,
    assert_no_outcome_fields,
    build_development_manifest,
    build_strict_prefix,
    prefix_messages,
    strict_predecision_nodes,
    tool_schema_artifact,
)
from tracegraph.schema import EdgeType, NodeType
from tracegraph.trajectory_artifacts import sha256_json


class _OutcomeGuard(dict[str, Any]):
    def __getitem__(self, key: str) -> Any:
        if key == "outcome":
            raise AssertionError("outcome must not be accessed")
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "outcome":
            raise AssertionError("outcome must not be accessed")
        return super().get(key, default)


def _schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "lookup",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    }


class Phase5OfflineManifestTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
    ) -> tuple[TraceGraph, Path, dict[str, Any], dict[str, Any]]:
        session_root = root / "session"
        session_root.mkdir()
        archive = ArchiveStore(session_root / "archive")
        graph = TraceGraph(session_id="s1")
        graph.create_node(
            NodeType.GOAL,
            "find record",
            0,
            node_id="goal",
            metadata={
                "source": "user_message",
                "source_message_ordinal": 1,
            },
        )
        call_payload = {
            "tool_name": "lookup",
            "arguments": {"id": "7"},
            "call_id": "call-7",
        }
        call = graph.create_node(
            NodeType.TOOL_CALL,
            call_payload,
            1,
            node_id="call",
            raw_ref=archive.put(call_payload),
            metadata={
                "tool_name": "lookup",
                "call_id": "call-7",
                "source_message_ordinal": 2,
            },
        )
        result_payload = {"id": "7", "status": "found"}
        result = graph.create_node(
            NodeType.OBSERVATION,
            result_payload,
            2,
            node_id="result",
            raw_ref=archive.put(result_payload),
            metadata={
                "call_id": "call-7",
                "source_message_ordinal": 3,
            },
        )
        graph.connect(call.node_id, result.node_id, EdgeType.PRODUCES)
        graph.create_node(
            NodeType.DECISION,
            "respond",
            3,
            node_id="decision",
            metadata={"source_message_ordinal": 4},
        )
        source_path = session_root / "trace.json"
        graph.save(source_path)
        loaded = TraceGraph.load(source_path)

        points: list[dict[str, Any]] = []
        for point_id, cutoff, ordinal in (
            ("p2", 2, 3),
            ("p1", 3, 4),
        ):
            prefix_hash = sha256_json(
                [
                    node.to_dict()
                    for node in strict_predecision_nodes(
                        loaded,
                        cutoff_step=cutoff,
                        source_message_ordinal=ordinal,
                    )
                ]
            )
            points.append(
                {
                    "decision_point_id": point_id,
                    "session_id": "s1",
                    "domain": "retail",
                    "task_id": "task-1",
                    "trial": 1,
                    "cutoff_step": cutoff,
                    "source_message_ordinal": ordinal,
                    "prefix_sha256": prefix_hash,
                    "outcome": {
                        "reward": 1,
                        "tool_names": ["lookup"],
                    },
                }
            )
        dataset: dict[str, Any] = {
            "schema_version": "gdsc_decision_points_v1",
            "sources": [
                {
                    "session_id": "s1",
                    "domain": "retail",
                    "task_id": "task-1",
                    "trial": 1,
                    "source_path": source_path.as_posix(),
                    "event_graph_sha256": sha256_json(loaded.to_dict()),
                }
            ],
            "decision_points": points,
            "dataset_sha256": "a" * 64,
        }
        dataset_path = root / "dataset.json"
        dataset_path.write_text(
            json.dumps(dataset, ensure_ascii=False),
            encoding="utf-8",
        )
        schemas = tool_schema_artifact({"retail": (_schema(),)})
        return loaded, dataset_path, dataset, schemas

    def test_manifest_is_deterministic_and_does_not_access_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, dataset_path, dataset, schemas = self._fixture(root)
            guarded = dict(dataset)
            guarded["decision_points"] = [
                _OutcomeGuard(item) for item in dataset["decision_points"]
            ]
            first = build_development_manifest(
                guarded,
                dataset_path=dataset_path,
                schemas_artifact=schemas,
            )
            reversed_dataset = dict(guarded)
            reversed_dataset["decision_points"] = list(
                reversed(guarded["decision_points"])
            )
            second = build_development_manifest(
                reversed_dataset,
                dataset_path=dataset_path,
                schemas_artifact=schemas,
            )
            self.assertEqual(first, second)
            self.assertEqual(
                [item["prefix_id"] for item in first["prefixes"]],
                ["p1", "p2"],
            )
            self.assertEqual(first["f5_g1_thresholds"], F5_G1_THRESHOLDS)
            declared = first["manifest_sha256"]
            without_hash = dict(first)
            without_hash.pop("manifest_sha256")
            self.assertEqual(declared, sha256_json(without_hash))

    def test_prefix_rebuild_uses_only_strictly_prior_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph, _, _, _ = self._fixture(Path(directory))
            prefix = build_strict_prefix(
                graph,
                cutoff_step=3,
                source_message_ordinal=4,
                prefix_id="p1",
            )
            self.assertEqual(set(prefix.nodes), {"goal", "call", "result"})
            self.assertEqual(
                prefix_messages(prefix),
                (
                    {"role": "user", "content": "find record"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-7",
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"id":"7"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-7",
                        "content": '{"id":"7","status":"found"}',
                    },
                ),
            )

    def test_outcome_fields_are_rejected_from_manifest(self) -> None:
        with self.assertRaisesRegex(ValueError, "forbidden outcome field"):
            assert_no_outcome_fields({"selection": {"reward": 1}})
        assert_no_outcome_fields(
            {
                "prefixes": [
                    {
                        "structural_features": {
                            "tool_call_count": 1,
                            "side_effect_node_count": 0,
                        }
                    }
                ]
            }
        )

    def test_gate_adjudication_uses_frozen_exclusive_cost_threshold(self) -> None:
        metrics = {
            "all_frozen_prefixes_included": True,
            "source_load_determinism_rate": 1.0,
            "frozen_prefix_hash_match_rate": 1.0,
            "deterministic_artifact_rate": 1.0,
            "future_suffix_independence_rate": 1.0,
            "protocol_valid_rate": 1.0,
            "root_event_recall": 1.0,
            "critical_event_recall": 1.0,
            "archive_reactivation_rate": 1.0,
            "request_hash_match_rate": 1.0,
            "policy_false_dead": 0,
            "confirmation_false_dead": 0,
            "side_effect_receipt_false_dead": 0,
            "cost_analysis_eligible": 1,
            "reduced_prefix_count": 1,
            "paired_median_serialized_token_delta": -1.0,
            "external_provider_generations": 0,
        }
        self.assertEqual(
            adjudicate_f5_g1(metrics, F5_G1_THRESHOLDS)["decision"],
            "pass",
        )
        metrics["paired_median_serialized_token_delta"] = 0.0
        report = adjudicate_f5_g1(metrics, F5_G1_THRESHOLDS)
        self.assertEqual(report["decision"], "fail")
        failed = [item["metric"] for item in report["criteria"] if not item["passed"]]
        self.assertEqual(
            failed,
            ["paired_median_serialized_token_delta"],
        )


if __name__ == "__main__":
    unittest.main()
