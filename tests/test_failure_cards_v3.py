from __future__ import annotations

import tempfile
import unittest

from tracegraph import (
    ArchiveStore,
    EdgeType,
    FailureCard,
    FailureClass,
    FailureExpiryTrigger,
    GraphLifecycleManager,
    LifecycleEngine,
    NodeType,
    RawHardFailureRetentionManager,
    ToolStatus,
    TraceGraph,
    build_failure_cards,
)
from tracegraph.capture import ToolExecutor


class FailureCardSchemaTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        card = FailureCard(
            card_id="failure_card_test",
            operation_scope="scope",
            action_family="lookup",
            entity_ids=("A-1",),
            failure_class=FailureClass.ACTIONABLE,
            latest_failure_cause="not found",
            failed_argument_diff={"id": {"before": "A-0", "after": "A-1"}},
            next_admissible_correction="correct the id",
            confidence=0.9,
            created_step=1,
            last_relevant_step=2,
            expiry_trigger=None,
            raw_archive_refs=("sha256:test",),
            source_node_ids=("node_a", "node_b"),
        )

        self.assertEqual(FailureCard.from_dict(card.to_dict()), card)
        self.assertTrue(card.active)


class FailureCardBuilderTests(unittest.TestCase):
    def test_legacy_lifecycle_edge_is_normalized_before_card_selection(self) -> None:
        graph = TraceGraph()
        failure = graph.create_node(
            NodeType.ERROR,
            {"error": "temporary failure"},
            1,
            metadata={"tool_name": "lookup"},
        )
        result = graph.create_node(NodeType.OBSERVATION, {"ok": True}, 2)
        graph.connect(result.node_id, failure.node_id, EdgeType.RESOLVES)

        restored = TraceGraph.from_dict(graph.to_dict())
        LifecycleEngine().apply(restored)
        cards, events = build_failure_cards(restored, ttl_steps=None)

        canonical = restored.outgoing(failure.node_id, EdgeType.RESOLVED_BY)
        self.assertEqual([edge.target for edge in canonical], [result.node_id])
        self.assertEqual(restored.normalize_legacy_lifecycle_edges(), 0)
        self.assertEqual(cards, [])
        self.assertEqual(events[0]["expiry_trigger"], FailureExpiryTrigger.RESOLVED.value)

    def test_failed_retry_keeps_one_card_for_latest_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            _, first = executor.record_result(
                tool_name="book_reservation",
                arguments={"user_id": "U-1", "payment": 100},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "payment total mismatch"},
            )
            _, latest = executor.record_result(
                tool_name="book_reservation",
                arguments={"user_id": "U-1", "payment": 200},
                step_id=2,
                status=ToolStatus.FAILED,
                payload={"error": "payment method unavailable"},
            )
            LifecycleEngine().apply(graph)

            cards, events = build_failure_cards(graph, ttl_steps=None)

            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].source_node_ids, (first.node_id, latest.node_id))
            self.assertIn("payment method unavailable", cards[0].latest_failure_cause)
            self.assertEqual(
                cards[0].failed_argument_diff["payment"],
                {"before": 100, "after": 200},
            )
            self.assertEqual(events[0]["event"], "updated")

    def test_successful_retry_expires_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            executor.record_result(
                tool_name="cancel_order",
                arguments={"order_id": "A-1", "confirmation": False},
                step_id=1,
                status=ToolStatus.SUCCESS,
                payload={"status": "confirmation required"},
            )
            executor.record_result(
                tool_name="cancel_order",
                arguments={"order_id": "A-1", "confirmation": True},
                step_id=2,
                status=ToolStatus.SUCCESS,
                payload={"status": "cancelled"},
            )
            LifecycleEngine().apply(graph)

            cards, events = build_failure_cards(graph, ttl_steps=None)

            self.assertEqual(cards, [])
            self.assertIn(
                FailureExpiryTrigger.RESOLVED.value,
                {event["expiry_trigger"] for event in events},
            )

    def test_explicit_alternative_completion_expires_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            _, failure = executor.record_result(
                tool_name="primary_lookup",
                arguments={"user_id": "U-1"},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "service unavailable"},
            )
            LifecycleEngine().apply(graph)
            scope = failure.lifecycle_profile.scope["operation_key"]
            graph.metadata["completed_operation_scopes"] = [scope]

            cards, events = build_failure_cards(graph, ttl_steps=None)

            self.assertEqual(cards, [])
            self.assertEqual(
                events[0]["expiry_trigger"],
                FailureExpiryTrigger.ALTERNATIVE_COMPLETED.value,
            )

    def test_malformed_failure_expires_after_valid_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            _, failure = executor.record_result(
                tool_name="lookup",
                arguments={"id": None},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "invalid argument"},
            )
            failure.metadata["failure_class"] = FailureClass.MALFORMED.value
            graph.create_node(
                NodeType.TOOL_CALL,
                {"tool_name": "lookup", "arguments": {"id": "A-1"}},
                2,
                metadata={"tool_name": "lookup", "arguments_valid": True},
            )
            LifecycleEngine().apply(graph)

            cards, events = build_failure_cards(graph, ttl_steps=None)

            self.assertEqual(cards, [])
            self.assertEqual(
                events[0]["expiry_trigger"],
                FailureExpiryTrigger.CORRECTED_SYNTAX.value,
            )

    def test_user_abandon_final_accept_and_ttl_are_supported(self) -> None:
        trigger_cases = (
            ("abandoned_operation_scopes", FailureExpiryTrigger.USER_ABANDONED),
            ("accepted_failure_node_ids", FailureExpiryTrigger.FINAL_ACCEPTED),
        )
        for metadata_key, expected in trigger_cases:
            with self.subTest(metadata_key=metadata_key), tempfile.TemporaryDirectory() as directory:
                graph = TraceGraph()
                executor = ToolExecutor(graph, ArchiveStore(directory))
                _, failure = executor.record_result(
                    tool_name="lookup",
                    arguments={"order_id": "A-1"},
                    step_id=1,
                    status=ToolStatus.FAILED,
                    payload={"error": "not found"},
                )
                LifecycleEngine().apply(graph)
                value = (
                    failure.node_id
                    if metadata_key == "accepted_failure_node_ids"
                    else failure.lifecycle_profile.scope["operation_key"]
                )
                graph.metadata[metadata_key] = [value]

                cards, events = build_failure_cards(graph, ttl_steps=None)

                self.assertEqual(cards, [])
                self.assertEqual(events[0]["expiry_trigger"], expected.value)

        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            executor.record_result(
                tool_name="lookup",
                arguments={"order_id": "A-1"},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "not found"},
            )
            graph.create_node(NodeType.DECISION, "unrelated later work", 20)
            LifecycleEngine().apply(graph)

            cards, events = build_failure_cards(graph, ttl_steps=8)

            self.assertEqual(cards, [])
            self.assertEqual(
                events[0]["expiry_trigger"],
                FailureExpiryTrigger.TTL_EXPIRED.value,
            )


class FailureCardContextTests(unittest.TestCase):
    def test_card_is_bounded_and_does_not_select_raw_failure_or_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            call, failure = executor.record_result(
                tool_name="lookup_order",
                arguments={"order_id": "A-1"},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "order not found", "details": "x" * 1000},
            )

            view = GraphLifecycleManager().select(graph, budget=256)

            card = next(item for item in view.items if "failure_card" in item.reason)
            self.assertEqual(card.node_type, NodeType.SUMMARY)
            self.assertIn(failure.node_id, card.source_node_ids)
            self.assertNotEqual(card.content, failure.content)
            self.assertNotIn(call.node_id, {item.node_id for item in view.items})
            self.assertLessEqual(
                view.metadata["failure_card_tokens"],
                view.metadata["failure_card_budget"],
            )
            self.assertLessEqual(view.selected_tokens, 256)
            self.assertEqual(view.metadata["raw_failure_messages_selected"], 0)

    def test_audit_required_call_is_archived_but_not_mandatory_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(directory)
            graph = TraceGraph()
            executor = ToolExecutor(graph, store)
            call, _ = executor.record_result(
                tool_name="write_order",
                arguments={"order_id": "A-1"},
                step_id=1,
                status=ToolStatus.SUCCESS,
                payload={"ok": True},
                side_effect=True,
            )

            card_view = GraphLifecycleManager().select(graph, budget=256)
            raw_view = RawHardFailureRetentionManager().select(graph, budget=0)

            self.assertTrue(store.exists(call.raw_ref or ""))
            self.assertNotIn(call.node_id, {item.node_id for item in card_view.items})
            self.assertIn(call.node_id, {item.node_id for item in raw_view.items})

    def test_failure_card_audit_events_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            executor.record_result(
                tool_name="lookup",
                arguments={"id": "A-1"},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "not found"},
            )
            manager = GraphLifecycleManager()

            manager.select(graph, budget=512)
            manager.select(graph, budget=512)

            created = [
                event
                for event in graph.metadata["failure_card_events"]
                if event["event"] == "created"
            ]
            self.assertEqual(len(created), 1)


if __name__ == "__main__":
    unittest.main()
