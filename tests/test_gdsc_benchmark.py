from __future__ import annotations

import tempfile
import unittest

from tracegraph.decision_point_dataset import (
    GraphRecord,
    build_decision_point_dataset,
    evaluate_benchmark_eligibility,
    stable_task_split,
)
from tracegraph.graph import TraceGraph
from tracegraph.prefix_forks import (
    FrozenPrefix,
    build_fork_plan,
    load_frozen_prefix,
    persist_frozen_prefix,
    score_representation_harm,
    validate_frozen_prefix,
)
from tracegraph.schema import LifecycleProfile, NodeType, ValidityState


def _small_graph(session_id: str = "session-1") -> TraceGraph:
    graph = TraceGraph(session_id=session_id, metadata={"task_success": 1})
    graph.create_node(
        NodeType.GOAL,
        "book a flight",
        0,
        node_id="goal",
        metadata={"source_message_ordinal": 1},
    )
    graph.create_node(
        NodeType.OBSERVATION,
        {"flight": "TG1"},
        1,
        node_id="past",
        token_count=200,
        metadata={"source_message_ordinal": 2},
        lifecycle_profile=LifecycleProfile(
            validity=ValidityState.VALID,
            confidence=1.0,
            inferred_by="test",
        ),
    )
    graph.create_node(
        NodeType.DECISION,
        {"tool_calls": []},
        2,
        node_id="decision",
        metadata={
            "source_message_ordinal": 3,
            "provider_usage": {"prompt_tokens": 1000},
        },
    )
    graph.create_node(
        NodeType.OBSERVATION,
        {"future": True},
        3,
        node_id="future",
        metadata={"source_message_ordinal": 4},
    )
    return graph


def _eligible_graph(session_id: str, success: bool) -> TraceGraph:
    graph = TraceGraph(
        session_id=session_id,
        metadata={
            "task_success": float(success),
            "dynamic_provider_input_ratio": 0.8,
            "provider_token_oracle_headroom": 0.5,
        },
    )
    for index in range(30):
        graph.create_node(
            NodeType.CONSTRAINT,
            f"policy-{index}",
            0,
            node_id=f"constraint-{index}",
            token_count=1,
            metadata={"source": "domain_policy"},
        )
        graph.create_node(
            NodeType.OBSERVATION,
            {"slot": index},
            1,
            node_id=f"slot-{index}",
            token_count=100,
            metadata={"open_slot": True},
        )
        graph.create_node(
            NodeType.TOOL_CALL,
            {"index": index},
            1,
            node_id=f"effect-{index}",
            token_count=1,
            raw_ref=f"sha256:{index:064d}",
            side_effect=True,
        )
    for index in range(10):
        graph.create_node(
            NodeType.DECISION,
            f"decision-{index}",
            index + 2,
            node_id=f"decision-{index}",
            metadata={"provider_usage": {"prompt_tokens": 1000}},
        )
    return graph


class DecisionPointDatasetTests(unittest.TestCase):
    def test_dataset_is_prefix_only_and_stable(self) -> None:
        record = GraphRecord(_small_graph(), "retail", "1")
        first = build_decision_point_dataset([record])
        second = build_decision_point_dataset([record])
        self.assertEqual(first["dataset_sha256"], second["dataset_sha256"])
        source_ids = {
            source_id
            for row in first["candidate_objects"]
            for source_id in row["source_node_ids"]
        }
        self.assertIn("past", source_ids)
        self.assertNotIn("future", source_ids)

    def test_task_split_never_leaks_adjacent_points(self) -> None:
        rows = [
            {"domain": "retail", "task_id": "1", "decision_point_id": f"p-{index}"}
            for index in range(20)
        ]
        split = stable_task_split(rows)
        occupied = [name for name, values in split.items() if values]
        self.assertEqual(len(occupied), 1)
        self.assertEqual(len(split[occupied[0]]), 20)

    def test_eligibility_fails_closed_on_missing_provider_and_snapshot(self) -> None:
        report = evaluate_benchmark_eligibility(
            [GraphRecord(_small_graph(), "retail", "1")]
        )
        self.assertFalse(report["eligible"])
        self.assertEqual(report["decision"], "stop_before_r3")
        self.assertFalse(report["domains"]["retail"]["checks"]["snapshot_replay"])

    def test_eligibility_passes_only_with_all_domain_evidence(self) -> None:
        records = []
        for domain in ("retail", "airline"):
            for index in range(10):
                records.append(
                    GraphRecord(
                        _eligible_graph(f"{domain}-{index}", success=index % 2 == 0),
                        domain,
                        str(index),
                        snapshot_replayable=True,
                        native_evaluator_success=True,
                        native_evaluator_side_effect=True,
                    )
                )
        report = evaluate_benchmark_eligibility(records)
        self.assertTrue(report["eligible"])
        self.assertTrue(all(value["eligible"] for value in report["domains"].values()))


class PrefixForkTests(unittest.TestCase):
    def _prefix(self, prefix_id: str = "prefix-1", object_class: str = "goal_subgoal") -> FrozenPrefix:
        return FrozenPrefix(
            prefix_id=prefix_id,
            task={"task_id": prefix_id},
            domain="retail",
            seed=300,
            conversation=[{"role": "user", "content": "hello"}],
            environment_snapshot={"db": "frozen"},
            event_graph={"nodes": []},
            decision_state={"atoms": []},
            decision_query={"candidate_tools": ["lookup"]},
            tool_schemas=[{"name": "lookup"}],
            representation_payload={"source_ids": ["node-1"]},
            model_config={"model": "zai/glm-4.7-flash", "temperature": 0},
            object_class=object_class,
        )

    def test_frozen_prefix_round_trip_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = persist_frozen_prefix(directory, self._prefix())
            loaded = load_frozen_prefix(path)
            self.assertEqual(loaded["prefix_id"], "prefix-1")
            loaded["conversation"][0]["content"] = "tampered"
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_frozen_prefix(loaded)

    def test_fork_plan_is_deterministic_zero_api_and_capped(self) -> None:
        prefix = self._prefix().to_dict()
        first = build_fork_plan([prefix])
        second = build_fork_plan([prefix])
        self.assertEqual(first["plan_sha256"], second["plan_sha256"])
        self.assertEqual(first["branch_count"], 6)
        self.assertEqual(first["external_api_calls"], 0)
        with self.assertRaisesRegex(ValueError, "session cap"):
            build_fork_plan([prefix], session_cap=5)

    def test_representation_harm_gate(self) -> None:
        rows = []
        for prefix_index in range(30):
            for replicate in (1, 2):
                invariants = {"prefix": prefix_index}
                for treatment, tokens, harm in (
                    ("raw_message", 100, False),
                    ("compiled", 80, False),
                    ("drop", 50, True),
                ):
                    rows.append(
                        {
                            "prefix_id": f"p-{prefix_index}",
                            "replicate": replicate,
                            "treatment": treatment,
                            "treatment_injected": True,
                            "invariants": invariants,
                            "provider_input_tokens": tokens,
                            "invalid_action": harm,
                        }
                    )
        report = score_representation_harm(rows)
        self.assertTrue(report["r3_to_r4_passed"])
        self.assertEqual(report["decision"], "proceed_to_r4")


if __name__ == "__main__":
    unittest.main()
