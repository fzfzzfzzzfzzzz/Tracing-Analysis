from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracegraph import ArchiveStore, EdgeType, LifecycleState, NodeType
from tracegraph.adapters import TauTraceImporter


def tau3_fixture() -> dict:
    return {
        "info": {"environment_info": {"domain_name": "retail"}},
        "tasks": [{"id": "task-1", "instruction": "cancel an order after confirmation"}],
        "simulations": [
            {
                "id": "sim-1",
                "task_id": "task-1",
                "trial": 0,
                "termination_reason": "agent_stop",
                "reward_info": {"reward": 1.0},
                "policy": "Obtain confirmation before cancellation.",
                "messages": [
                    {"role": "user", "content": "Please cancel order 7", "turn_idx": 0},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "c1", "name": "cancel_order", "arguments": {"id": 7}}
                        ],
                        "turn_idx": 1,
                    },
                    {
                        "id": "c1",
                        "role": "tool",
                        "content": "confirmation required",
                        "error": True,
                        "turn_idx": 2,
                    },
                    {"role": "assistant", "content": "Please confirm.", "turn_idx": 3},
                    {"role": "user", "content": "Yes", "turn_idx": 4},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "c2", "name": "cancel_order", "arguments": {"id": 7}}
                        ],
                        "turn_idx": 5,
                    },
                    {
                        "id": "c2",
                        "role": "tool",
                        "content": "{\"status\": \"cancelled\"}",
                        "error": False,
                        "turn_idx": 6,
                    },
                    {"role": "assistant", "content": "Order cancelled.", "turn_idx": 7},
                ],
            }
        ],
    }


class TauAdapterTests(unittest.TestCase):
    def test_import_current_monolithic_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "results.json"
            source.write_text(json.dumps(tau3_fixture()), encoding="utf-8")
            store = ArchiveStore(root / "archive")
            graphs = TauTraceImporter(store).import_path(source)
            self.assertEqual(len(graphs), 1)
            graph = graphs[0]
            self.assertEqual(graph.metadata["task_success"], 1.0)
            self.assertEqual(graph.validate(), [])
            self.assertEqual(len(graph.find_nodes(node_types={NodeType.CONSTRAINT})), 1)
            errors = graph.find_nodes(node_types={NodeType.ERROR})
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].lifecycle, LifecycleState.RESOLVED_FAILURE)
            self.assertEqual(
                len([edge for edge in graph.edges.values() if edge.edge_type == EdgeType.RETRIES]),
                1,
            )
            self.assertEqual(
                len([edge for edge in graph.edges.values() if edge.edge_type == EdgeType.RESOLVES]),
                1,
            )
            self.assertTrue(
                all(store.exists(node.raw_ref or "") for node in graph.nodes.values() if node.raw_ref)
            )

    def test_import_current_directory_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = tau3_fixture()
            metadata = dict(fixture)
            simulation = metadata.pop("simulations")[0]
            (root / "simulations").mkdir()
            (root / "results.json").write_text(json.dumps(metadata), encoding="utf-8")
            (root / "simulations" / "sim-1.json").write_text(
                json.dumps(simulation), encoding="utf-8"
            )
            graphs = TauTraceImporter(ArchiveStore(root / "archive")).import_path(root)
            self.assertEqual(len(graphs), 1)

    def test_legacy_trajectory_field_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "task_id": 3,
                "trajectory": [
                    {"role": "assistant", "content": "done"},
                ],
                "reward": 0.5,
            }
            source = Path(directory) / "legacy.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            graph = TauTraceImporter(ArchiveStore(Path(directory) / "archive")).import_path(source)[0]
            self.assertEqual(graph.metadata["task_success"], 0.5)
            decisions = graph.find_nodes(node_types={NodeType.DECISION})
            self.assertTrue(decisions[-1].metadata["final"])

    def test_legacy_error_text_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "task_id": 4,
                "traj": [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "legacy-call",
                                "function": {
                                    "name": "book_reservation",
                                    "arguments": "{\"id\": \"A-1\"}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "legacy-call",
                        "name": "book_reservation",
                        "content": "Error: payment amount does not add up",
                    },
                ],
            }
            source = Path(directory) / "legacy-error.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            graph = TauTraceImporter(ArchiveStore(Path(directory) / "archive")).import_path(source)[0]
            errors = graph.find_nodes(node_types={NodeType.ERROR})
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].lifecycle, LifecycleState.UNRESOLVED_FAILURE)

    def test_legacy_trials_have_distinct_session_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = [
                {"task_id": 7, "trial": 0, "traj": [{"role": "assistant", "content": "a"}]},
                {"task_id": 7, "trial": 1, "traj": [{"role": "assistant", "content": "b"}]},
            ]
            source = Path(directory) / "trials.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            graphs = TauTraceImporter(ArchiveStore(Path(directory) / "archive")).import_path(source)
            self.assertEqual(len({graph.session_id for graph in graphs}), 2)
            self.assertEqual(
                {graph.session_id for graph in graphs},
                {"tau_task_7_trial_0", "tau_task_7_trial_1"},
            )

    def test_prompt_usage_is_not_charged_as_message_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "task_id": 8,
                "messages": [
                    {
                        "role": "user",
                        "content": "hello",
                        "usage": {
                            "prompt_tokens": 400,
                            "completion_tokens": 200,
                        },
                    },
                    {
                        "role": "assistant",
                        "content": "brief answer",
                        "usage": {
                            "prompt_tokens": 5000,
                            "completion_tokens": 300,
                        },
                    },
                ],
            }
            source = Path(directory) / "usage.json"
            source.write_text(json.dumps(payload), encoding="utf-8")

            graph = TauTraceImporter(
                ArchiveStore(Path(directory) / "archive")
            ).import_path(source)[0]

            user_goal = [
                node
                for node in graph.find_nodes(node_types={NodeType.GOAL})
                if node.metadata.get("source") == "user_message"
            ][0]
            decision = graph.find_nodes(node_types={NodeType.DECISION})[0]
            self.assertEqual(user_goal.token_count, 2)
            self.assertEqual(decision.token_count, 3)
            self.assertEqual(
                decision.metadata["provider_usage"]["prompt_tokens"],
                5000,
            )


if __name__ == "__main__":
    unittest.main()
