from __future__ import annotations

import tempfile
import unittest

from tracegraph.adapters import TauTraceImporter
from tracegraph.archive import ArchiveStore
from tracegraph.post_failure import (
    aggregate_post_failure_events,
    analyze_post_failure_windows,
)
from tracegraph.schema import NodeType


class PostFailureWindowTests(unittest.TestCase):
    def _graph(self, messages):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        importer = TauTraceImporter(ArchiveStore(temporary.name))
        graph = importer.import_simulation(
            {"id": "simulation-1", "task_id": "task-1", "messages": messages}
        )
        return graph

    def test_argument_completion_repair_uses_next_action_and_real_usage(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "lookup",
                        "arguments": {"user_id": "u-1", "record_id": ""},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
            {
                "role": "tool",
                "id": "c1",
                "content": {"error": "missing required parameter: record_id"},
                "error": True,
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c2",
                        "name": "lookup",
                        "arguments": {"user_id": "u-1", "record_id": "r-1"},
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5},
            },
            {
                "role": "tool",
                "id": "c2",
                "content": {"record_id": "r-1", "status": "found"},
            },
        ]
        graph = self._graph(messages)
        failure = graph.find_nodes(node_types={NodeType.ERROR})[0]
        views = [
            {"metadata": {"selected_message_ordinals": []}, "items": []},
            {
                "metadata": {
                    "selected_message_ordinals": [],
                    "failure_card_count": 1,
                    "failure_card_tokens": 20,
                    "raw_failure_messages_selected": 0,
                },
                "items": [
                    {
                        "reason": "failure_card:active",
                        "source_node_ids": [failure.node_id],
                        "token_count": 20,
                    }
                ],
            },
        ]
        report = analyze_post_failure_windows(
            graph, messages=messages, context_views=views, horizon=3
        )
        event = report["events"][0]
        self.assertEqual(event["observed_agent_actions"], 1)
        self.assertTrue(event["admissible_correction"])
        self.assertTrue(event["resolved_within_window"])
        self.assertEqual(event["recovery_action_index"], 1)
        self.assertEqual(event["post_failure_provider_input_tokens"], 50.0)
        self.assertEqual(event["provider_input_tokens_per_action"], 50.0)
        self.assertEqual(event["target_failure_card_visible_actions"], 1)
        self.assertEqual(event["target_failure_card_tokens"], 20)
        self.assertEqual(event["raw_failure_replay_actions"], 0)

    def test_exact_failed_retry_is_repeated_invalid_and_missing_usage_is_not_zero(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "lookup",
                        "arguments": {"record_id": "missing"},
                    }
                ],
            },
            {
                "role": "tool",
                "id": "c1",
                "content": {"error": "not found"},
                "error": True,
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c2",
                        "name": "lookup",
                        "arguments": {"record_id": "missing"},
                    }
                ],
            },
            {
                "role": "tool",
                "id": "c2",
                "content": {"error": "not found"},
                "error": True,
            },
        ]
        graph = self._graph(messages)
        report = analyze_post_failure_windows(graph, messages=messages, horizon=3)
        first = report["events"][0]
        self.assertTrue(first["repeated_same_invalid"])
        self.assertEqual(first["repeated_same_invalid_count"], 1)
        self.assertIsNone(first["post_failure_provider_input_tokens"])
        self.assertIsNone(first["provider_input_tokens_per_action"])
        summary = aggregate_post_failure_events(report["events"])
        self.assertEqual(summary["provider_input_usage_events"], 0)
        self.assertIsNone(summary["mean_post_failure_provider_input_tokens"])

    def test_raw_failure_selection_is_detected_from_prompt_ordinals(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "name": "lookup", "arguments": {}}],
            },
            {
                "role": "tool",
                "id": "c1",
                "content": {"error": "missing parameter"},
                "error": True,
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c2",
                        "name": "lookup",
                        "arguments": {"record_id": "r-1"},
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 1},
            },
            {"role": "tool", "id": "c2", "content": {"ok": True}},
        ]
        graph = self._graph(messages)
        views = [
            {"metadata": {"selected_message_ordinals": []}, "items": []},
            {
                "metadata": {
                    "selected_message_ordinals": [1, 2],
                    "raw_failure_messages_selected": 2,
                },
                "items": [],
            },
        ]
        event = analyze_post_failure_windows(graph, messages=messages, context_views=views)[
            "events"
        ][0]
        self.assertEqual(event["target_failure_ordinal_overlap_actions"], 1)
        self.assertEqual(event["raw_failure_replay_actions"], 1)


if __name__ == "__main__":
    unittest.main()
