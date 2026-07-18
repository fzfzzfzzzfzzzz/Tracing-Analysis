import tempfile
import unittest

from tracegraph import (
    ArchiveStore,
    EdgeType,
    GraphLifecycleManager,
    LifecycleEngine,
    LifecycleProfile,
    NodeType,
    RelevanceState,
    RetentionObligation,
    RawHardFailureRetentionManager,
    StorageState,
    ToolStatus,
    TraceGraph,
    ValidityState,
)
from tracegraph.capture import ToolExecutor
from tracegraph.context import NoFailureRetentionManager
from tracegraph.graph import GraphValidationError


class FactorizedLifecycleTests(unittest.TestCase):
    def test_consumed_record_can_remain_critical_evidence(self) -> None:
        graph = TraceGraph()
        evidence = graph.create_node(
            NodeType.OBSERVATION,
            {"verified": True},
            1,
        )
        final = graph.create_node(
            NodeType.DECISION,
            "done",
            2,
            metadata={"final": True},
        )
        graph.connect(evidence.node_id, final.node_id, EdgeType.SUPPORTS)

        LifecycleEngine().apply(graph)

        profile = evidence.lifecycle_profile
        self.assertEqual(profile.relevance, RelevanceState.CONSUMED)
        self.assertIn(RetentionObligation.CRITICAL_EVIDENCE, profile.obligations)

    def test_profile_round_trip_preserves_independent_dimensions(self) -> None:
        graph = TraceGraph()
        node = graph.create_node(
            NodeType.TOOL_CALL,
            {"tool": "write"},
            1,
            raw_ref="sha256:test",
            side_effect=True,
            lifecycle_profile=LifecycleProfile(
                relevance=RelevanceState.CONSUMED,
                validity=ValidityState.VALID,
                storage=StorageState.ARCHIVED,
                obligations=(
                    RetentionObligation.CRITICAL_EVIDENCE,
                    RetentionObligation.AUDIT_REQUIRED,
                ),
                inferred_by="test",
            ),
        )

        loaded = TraceGraph.from_dict(graph.to_dict())
        profile = loaded.nodes[node.node_id].lifecycle_profile
        self.assertEqual(profile.relevance, RelevanceState.CONSUMED)
        self.assertEqual(profile.storage, StorageState.ARCHIVED)
        self.assertEqual(
            set(profile.obligations),
            {
                RetentionObligation.CRITICAL_EVIDENCE,
                RetentionObligation.AUDIT_REQUIRED,
            },
        )

    def test_v1_node_without_profile_is_migrated_on_load(self) -> None:
        payload = {
            "schema_version": "1.0",
            "session_id": "legacy",
            "metadata": {},
            "nodes": [
                {
                    "node_type": "error",
                    "content": {"error": "temporary"},
                    "step_id": 1,
                    "lifecycle": "unresolved_failure",
                    "token_count": 1,
                    "raw_ref": "sha256:test",
                    "side_effect": False,
                    "active": True,
                    "metadata": {},
                    "node_id": "node_legacy",
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "edges": [],
        }

        graph = TraceGraph.from_dict(payload)

        self.assertEqual(graph.metadata["loaded_schema_version"], "1.0")
        self.assertEqual(
            graph.nodes["node_legacy"].lifecycle_profile.validity,
            ValidityState.NEGATIVE_UNRESOLVED,
        )


class SemanticFailureTests(unittest.TestCase):
    def test_successful_rpc_with_negative_business_result_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            _, result = executor.record_result(
                tool_name="lookup_order",
                arguments={"order_id": "A-1"},
                step_id=1,
                status=ToolStatus.SUCCESS,
                payload={"success": False, "message": "order not found"},
            )

            full_view = GraphLifecycleManager().select(graph, budget=512)
            raw_view = RawHardFailureRetentionManager().select(graph, budget=0)
            ablated_view = NoFailureRetentionManager().select(graph, budget=0)

            self.assertEqual(
                result.lifecycle_profile.validity,
                ValidityState.NEGATIVE_UNRESOLVED,
            )
            self.assertIn(
                RetentionObligation.RETAIN_UNTIL_ACTION_COMPLETE,
                result.lifecycle_profile.obligations,
            )
            card = next(item for item in full_view.items if "failure_card" in item.reason)
            self.assertEqual(card.node_type, NodeType.SUMMARY)
            self.assertIn(result.node_id, card.source_node_ids)
            self.assertNotEqual(card.content, result.content)
            self.assertIn(result.node_id, {item.node_id for item in raw_view.items})
            self.assertNotIn(result.node_id, {item.node_id for item in ablated_view.items})

    def test_changed_confirmation_argument_forms_forward_retry_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            first_call, first_result = executor.record_result(
                tool_name="cancel_order",
                arguments={"order_id": "A-1", "confirmation": False},
                step_id=1,
                status=ToolStatus.SUCCESS,
                payload={"status": "confirmation required"},
            )
            second_call, second_result = executor.record_result(
                tool_name="cancel_order",
                arguments={"order_id": "A-1", "confirmation": True},
                step_id=2,
                status=ToolStatus.SUCCESS,
                payload={"status": "cancelled"},
            )
            LifecycleEngine().apply(graph)

            retries = graph.outgoing(first_call.node_id, EdgeType.RETRIED_BY)
            resolutions = graph.outgoing(first_result.node_id, EdgeType.RESOLVED_BY)
            self.assertEqual([edge.target for edge in retries], [second_call.node_id])
            self.assertEqual([edge.target for edge in resolutions], [second_result.node_id])
            self.assertEqual(retries[0].metadata["match_type"], "structural_operation")
            self.assertEqual(
                first_result.lifecycle_profile.validity,
                ValidityState.NEGATIVE_RESOLVED,
            )

    def test_filling_missing_argument_resolves_failure_card_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            first_call, first_result = executor.record_result(
                tool_name="find_user_id_by_name_zip",
                arguments={
                    "first_name": "Isabella",
                    "last_name": "Johansson",
                    "zip": "",
                },
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "User not found"},
            )
            second_call, second_result = executor.record_result(
                tool_name="find_user_id_by_name_zip",
                arguments={
                    "first_name": "Isabella",
                    "last_name": "Johansson",
                    "zip": "32286",
                },
                step_id=2,
                status=ToolStatus.SUCCESS,
                payload={"user_id": "isabella_johansson_2152"},
            )
            LifecycleEngine().apply(graph)

            retry = graph.outgoing(first_call.node_id, EdgeType.RETRIED_BY)
            resolution = graph.outgoing(first_result.node_id, EdgeType.RESOLVED_BY)
            self.assertEqual([edge.target for edge in retry], [second_call.node_id])
            self.assertEqual(retry[0].metadata["match_type"], "argument_completion")
            self.assertEqual([edge.target for edge in resolution], [second_result.node_id])
            self.assertEqual(
                first_result.lifecycle_profile.validity,
                ValidityState.NEGATIVE_RESOLVED,
            )
            view = GraphLifecycleManager().select(graph, budget=512)
            self.assertEqual(view.metadata["failure_card_count"], 0)

    def test_failed_retry_supersedes_old_error_and_retains_only_latest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            graph = TraceGraph()
            executor = ToolExecutor(graph, ArchiveStore(directory))
            _, first_error = executor.record_result(
                tool_name="book_reservation",
                arguments={"user_id": "U-1", "payment": 100},
                step_id=1,
                status=ToolStatus.FAILED,
                payload={"error": "payment total mismatch"},
            )
            _, latest_error = executor.record_result(
                tool_name="book_reservation",
                arguments={"user_id": "U-1", "payment": 200},
                step_id=2,
                status=ToolStatus.FAILED,
                payload={"error": "payment method unavailable"},
            )
            LifecycleEngine().apply(graph)

            self.assertEqual(
                first_error.lifecycle_profile.validity,
                ValidityState.SUPERSEDED,
            )
            self.assertNotIn(
                RetentionObligation.RETAIN_UNTIL_ACTION_COMPLETE,
                first_error.lifecycle_profile.obligations,
            )
            self.assertEqual(
                latest_error.lifecycle_profile.validity,
                ValidityState.NEGATIVE_UNRESOLVED,
            )
            self.assertEqual(
                len(graph.outgoing(first_error.node_id, EdgeType.SUPERSEDED_BY)),
                1,
            )

    def test_canonical_edges_reject_temporal_reversal(self) -> None:
        graph = TraceGraph()
        later = graph.create_node(NodeType.TOOL_CALL, {}, 2)
        earlier = graph.create_node(NodeType.TOOL_CALL, {}, 1)

        with self.assertRaises(GraphValidationError):
            graph.connect(later.node_id, earlier.node_id, EdgeType.RETRIED_BY)


if __name__ == "__main__":
    unittest.main()
