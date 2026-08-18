import unittest
from types import SimpleNamespace

from tracegraph import NodeType
from tracegraph.context import ContextItem
from tracegraph.message_protocol import (
    close_message_protocol,
    close_message_ordinals,
    project_context_items_to_messages,
)


class MessageProtocolTests(unittest.TestCase):
    def test_typed_closure_records_addition_provenance(self):
        messages = [
            {"role": "user", "content": "read"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "function": {"name": "read"}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ]

        result = close_message_protocol(messages, {3})

        self.assertTrue(result.valid)
        self.assertEqual(result.ordinals, {1, 2, 3})
        self.assertEqual(
            {addition.reason for addition in result.additions},
            {"tool_call_for_selected_result", "leading_user_anchor"},
        )

    def test_typed_closure_marks_missing_tool_result_invalid(self):
        messages = [
            {"role": "user", "content": "write"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "function": {"name": "write"}}],
            },
        ]

        result = close_message_protocol(messages, {2})

        self.assertFalse(result.valid)
        self.assertIn("has no result message", result.errors[0])

    def test_adds_latest_user_anchor_before_compressed_assistant_slice(self):
        messages = [
            {"role": "user", "content": "initial"},
            {"role": "assistant", "content": "old"},
            {"role": "user", "content": "confirm"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "function": {"name": "write"}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ]
        self.assertEqual(close_message_ordinals(messages, {4, 5}), {3, 4, 5})

    def test_closes_every_result_for_multi_tool_assistant_message(self):
        messages = [
            {"role": "user", "content": "look up both"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "call-1", "function": {"name": "first"}},
                    {"id": "call-2", "function": {"name": "second"}},
                ],
            },
            {"role": "tool", "id": "call-1", "content": "one"},
            {"role": "tool", "id": "call-2", "content": "two"},
        ]
        self.assertEqual(close_message_ordinals(messages, {2}), {1, 2, 3, 4})

    def test_user_started_selection_is_unchanged(self):
        messages = [
            {"role": "user", "content": "current"},
            {"role": "assistant", "content": "answer"},
        ]
        self.assertEqual(close_message_ordinals(messages, {1, 2}), {1, 2})

    def test_failure_card_does_not_restore_historical_tool_exchange(self):
        messages = [
            {"role": "user", "content": "try the update"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "function": {"name": "write"}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "failed"},
            {"role": "user", "content": "use another approach"},
        ]
        card = ContextItem(
            node_id="failure-node",
            node_type=NodeType.SUMMARY,
            content={"kind": "failure_card", "latest_failure_cause": "failed"},
            token_count=8,
            reason="failure_card:actionable",
            source_node_ids=("failure-node",),
        )
        nodes = {
            "failure-node": SimpleNamespace(
                metadata={"source_message_ordinal": 3}
            )
        }

        ordinals, fragments = project_context_items_to_messages(
            messages,
            [card],
            nodes,
        )

        self.assertEqual(ordinals, {4})
        self.assertEqual(len(fragments), 1)
        self.assertIn("failure_card", fragments[0])


if __name__ == "__main__":
    unittest.main()
