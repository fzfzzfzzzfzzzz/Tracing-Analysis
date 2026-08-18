from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tracegraph.archive import ArchiveStore
from tracegraph.decision_query import build_decision_query
from tracegraph.graph import TraceGraph
from tracegraph.integrations.lifecycle_graph_context import (
    LifecycleGraphContextManager,
)
from tracegraph.lifecycle_context import (
    ContextView,
    ProjectionStrategy,
    project_context,
)
from tracegraph.liveness import (
    DecisionLifecycleGraph,
    LivenessRoots,
    LiveSubgraph,
    analyze_liveness,
    build_state,
    derive_roots,
)
from tracegraph.provider_cost import (
    ProviderProtocol,
    request_sha256,
    serialized_request_cost,
)
from tracegraph.schema import EdgeType, LifecycleState, NodeType, SemanticOutcome


def tool_schema(name: str, *required: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "properties": {item: {"type": "string"} for item in required},
                "required": list(required),
            },
        },
    }


def _call_message(call_id: str, name: str, arguments: str = "{}") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def superseded_fixture(
    archive: ArchiveStore,
) -> tuple[TraceGraph, tuple[dict, ...], tuple[dict, ...]]:
    messages = (
        {"role": "user", "content": "Find order 7"},
        _call_message("c1", "lookup_order", '{"order_id":"7"}'),
        {"role": "tool", "tool_call_id": "c1", "content": '{"status":"pending"}'},
        {"role": "user", "content": "Use order 8 instead"},
        _call_message("c2", "lookup_order", '{"order_id":"8"}'),
        {"role": "tool", "tool_call_id": "c2", "content": '{"status":"shipped"}'},
    )
    schemas = (tool_schema("lookup_order", "order_id"),)
    graph = TraceGraph(session_id="phase5-superseded")
    graph.create_node(
        NodeType.GOAL,
        "Find the current order",
        0,
        node_id="goal",
        metadata={"source_message_ordinal": 1},
    )
    call1_payload = {
        "tool_name": "lookup_order",
        "arguments": {"order_id": "7"},
        "call_id": "c1",
    }
    call1 = graph.create_node(
        NodeType.TOOL_CALL,
        call1_payload,
        1,
        node_id="call-1",
        raw_ref=archive.put(call1_payload),
        metadata={
            "tool_name": "lookup_order",
            "operation_key": "lookup_order:7",
            "call_id": "c1",
            "source_message_ordinal": 2,
        },
    )
    result1_payload = {"status": "pending"}
    result1 = graph.create_node(
        NodeType.OBSERVATION,
        result1_payload,
        2,
        node_id="result-1",
        raw_ref=archive.put(result1_payload),
        metadata={
            "semantic_outcome": SemanticOutcome.POSITIVE.value,
            "source_message_ordinal": 3,
        },
    )
    graph.connect(call1.node_id, result1.node_id, EdgeType.PRODUCES)

    call2_payload = {
        "tool_name": "lookup_order",
        "arguments": {"order_id": "8"},
        "call_id": "c2",
    }
    call2 = graph.create_node(
        NodeType.TOOL_CALL,
        call2_payload,
        3,
        node_id="call-2",
        raw_ref=archive.put(call2_payload),
        metadata={
            "tool_name": "lookup_order",
            "operation_key": "lookup_order:8",
            "call_id": "c2",
            "source_message_ordinal": 5,
        },
    )
    result2_payload = {"status": "shipped"}
    result2 = graph.create_node(
        NodeType.OBSERVATION,
        result2_payload,
        4,
        node_id="result-2",
        raw_ref=archive.put(result2_payload),
        metadata={
            "semantic_outcome": SemanticOutcome.POSITIVE.value,
            "source_message_ordinal": 6,
        },
    )
    graph.connect(call2.node_id, result2.node_id, EdgeType.PRODUCES)
    graph.connect(result1.node_id, result2.node_id, EdgeType.SUPERSEDED_BY)
    return graph, messages, schemas


class LivenessAnalysisTests(unittest.TestCase):
    def test_same_prefix_is_stable_and_future_suffix_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph, _, schemas = superseded_fixture(archive)
            first = build_state(graph, 4, tool_schemas=schemas)
            self.assertEqual(first.lifecycle_hash, build_state(graph, 4, tool_schemas=schemas).lifecycle_hash)

            future_call = graph.create_node(
                NodeType.TOOL_CALL,
                {"tool_name": "lookup_order", "arguments": {"order_id": "9"}},
                9,
                node_id="future-call",
                metadata={"tool_name": "lookup_order", "operation_key": "lookup_order:9"},
            )
            future_result = graph.create_node(
                NodeType.OBSERVATION,
                {"status": "future"},
                10,
                node_id="future-result",
            )
            graph.connect(future_call.node_id, future_result.node_id, EdgeType.PRODUCES)
            graph.connect("result-2", future_result.node_id, EdgeType.SUPERSEDED_BY)
            graph.set_lifecycle(
                "result-2",
                LifecycleState.SUPERSEDED,
                active=False,
            )
            self.assertEqual(
                first.lifecycle_hash,
                build_state(graph, 4, tool_schemas=schemas).lifecycle_hash,
            )

    def test_superseded_span_is_evicted_and_live_raw_messages_are_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph, messages, schemas = superseded_fixture(archive)
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            roots = derive_roots(state, query, schemas)
            live = analyze_liveness(
                graph,
                state,
                roots,
                archive_reader=archive.get,
            )
            self.assertEqual(set(live.evicted_node_ids), {"call-1", "result-1"})

            protocol = ProviderProtocol(
                model="test-model",
                system_rules=("fixed policy",),
                base_messages=messages,
                tools=schemas,
                hard_context_limit=100_000,
            )
            view = project_context(graph, live, "gdsc_prune_v1", protocol)
            self.assertTrue(view.protocol_valid)
            self.assertTrue(view.send_eligible)
            self.assertEqual(view.raw_message_ordinals, (1, 4, 5, 6))
            self.assertEqual(
                list(view.messages[1:]),
                [messages[index - 1] for index in view.raw_message_ordinals],
            )
            self.assertEqual(view.tools, schemas)
            raw_messages = (
                {"role": "system", "content": "fixed policy"},
                *messages,
            )
            self.assertLess(
                view.costs.serialized_request,
                serialized_request_cost(protocol, raw_messages),
            )
            self.assertEqual(view.request_hash, request_sha256(view.request))
            self.assertEqual(
                DecisionLifecycleGraph.from_dict(
                    state.to_dict()
                ).lifecycle_hash,
                state.lifecycle_hash,
            )
            self.assertEqual(
                LivenessRoots.from_dict(roots.to_dict()).roots_hash,
                roots.roots_hash,
            )
            self.assertEqual(
                LiveSubgraph.from_dict(
                    live.to_dict()
                ).live_subgraph_hash,
                live.live_subgraph_hash,
            )
            self.assertEqual(
                ContextView.from_dict(view.to_dict()).context_view_hash,
                view.context_view_hash,
            )
            view.assert_sent_request(view.request)
            with_usage = view.with_provider_actual(
                request_hash=view.request_hash,
                input_tokens=321,
            )
            self.assertEqual(with_usage.costs.provider_actual, 321)
            with self.assertRaises(ValueError):
                view.with_provider_actual(request_hash="wrong", input_tokens=1)

    def test_query_change_reactivates_archived_superseded_span(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph, _, schemas = superseded_fixture(archive)
            state = build_state(graph, tool_schemas=schemas)
            base_query = build_decision_query(
                state.decision_state,
                tool_schemas=schemas,
            )
            self.assertNotIn("referenced_atom_ids", base_query.to_dict())
            self.assertNotIn("referenced_event_ids", base_query.to_dict())
            referenced_query = replace(
                base_query,
                referenced_event_ids=("result-1",),
            )
            roots = derive_roots(state, referenced_query, schemas)
            live = analyze_liveness(
                graph,
                state,
                roots,
                archive_reader=archive.get,
            )
            self.assertIn("result-1", live.live_node_ids)
            self.assertIn("call-1", live.live_node_ids)
            self.assertFalse(live.evicted_span_ids)
            self.assertNotEqual(base_query.query_hash, referenced_query.query_hash)

    def test_unverified_archive_defaults_to_live(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph, _, schemas = superseded_fixture(archive)
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            roots = derive_roots(state, query, schemas)
            no_reader = analyze_liveness(graph, state, roots)
            self.assertFalse(no_reader.evicted_span_ids)
            self.assertTrue(
                any(
                    item["reason"] == "archive_verifier_unavailable"
                    for item in no_reader.uncertainty_records
                )
            )

            def tampered_reader(_: str) -> object:
                raise RuntimeError("tampered")

            tampered = analyze_liveness(
                graph,
                state,
                roots,
                archive_reader=tampered_reader,
            )
            self.assertFalse(tampered.evicted_span_ids)
            self.assertTrue(
                any(
                    str(item["reason"]).startswith("archive_round_trip_failed")
                    for item in tampered.uncertainty_records
                )
            )

    def test_low_confidence_terminal_edge_cannot_make_a_span_dead(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph, _, schemas = superseded_fixture(archive)
            supersession = next(
                edge
                for edge in graph.edges.values()
                if edge.edge_type == EdgeType.SUPERSEDED_BY
            )
            supersession.confidence = 0.5
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            live = analyze_liveness(
                graph,
                state,
                derive_roots(state, query, schemas),
                archive_reader=archive.get,
            )
            self.assertFalse(live.evicted_span_ids)
            self.assertIn("result-1", live.live_node_ids)

    def test_explicit_resolution_releases_the_failed_tool_span(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph = TraceGraph(session_id="resolved-failure")
            graph.create_node(
                NodeType.GOAL,
                "recover lookup",
                0,
                node_id="goal",
                metadata={"source_message_ordinal": 1},
            )
            call_payload = {
                "tool_name": "lookup",
                "arguments": {"id": "bad"},
                "call_id": "failed",
            }
            call = graph.create_node(
                NodeType.TOOL_CALL,
                call_payload,
                1,
                node_id="failed-call",
                raw_ref=archive.put(call_payload),
                metadata={
                    "tool_name": "lookup",
                    "operation_key": "lookup:bad",
                    "call_id": "failed",
                    "source_message_ordinal": 2,
                },
            )
            error_payload = {"error": "not found"}
            error = graph.create_node(
                NodeType.ERROR,
                error_payload,
                2,
                node_id="failed-result",
                raw_ref=archive.put(error_payload),
                metadata={
                    "semantic_outcome": SemanticOutcome.NEGATIVE.value,
                    "source_message_ordinal": 3,
                },
            )
            resolver = graph.create_node(
                NodeType.OBSERVATION,
                {"status": "recovered"},
                3,
                node_id="resolver",
                raw_ref=archive.put({"status": "recovered"}),
                metadata={"source_message_ordinal": 4},
            )
            graph.connect(call.node_id, error.node_id, EdgeType.FAILED_WITH)
            graph.connect(error.node_id, resolver.node_id, EdgeType.RESOLVED_BY)
            schemas = (tool_schema("lookup", "id"),)
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            live = analyze_liveness(
                graph,
                state,
                derive_roots(state, query, schemas),
                archive_reader=archive.get,
            )
            self.assertEqual(
                set(live.evicted_node_ids),
                {"failed-call", "failed-result"},
            )

    def test_side_effect_receipt_never_becomes_false_dead(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph = TraceGraph(session_id="side-effect")
            graph.create_node(
                NodeType.GOAL,
                "cancel",
                0,
                node_id="goal",
                metadata={"source_message_ordinal": 1},
            )
            call_payload = {
                "tool_name": "cancel",
                "arguments": {"id": "7"},
                "call_id": "write",
            }
            call = graph.create_node(
                NodeType.TOOL_CALL,
                call_payload,
                1,
                node_id="write-call",
                side_effect=True,
                raw_ref=archive.put(call_payload),
                metadata={
                    "tool_name": "cancel",
                    "operation_key": "cancel:7",
                    "call_id": "write",
                    "source_message_ordinal": 2,
                },
            )
            result_payload = {"status": "cancelled", "receipt": "r7"}
            result = graph.create_node(
                NodeType.OBSERVATION,
                result_payload,
                2,
                node_id="write-result",
                raw_ref=archive.put(result_payload),
                metadata={
                    "semantic_outcome": SemanticOutcome.POSITIVE.value,
                    "source_message_ordinal": 3,
                },
            )
            graph.connect(call.node_id, result.node_id, EdgeType.PRODUCES)
            schemas = (tool_schema("cancel", "id"),)
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            live = analyze_liveness(
                graph,
                state,
                derive_roots(state, query, schemas),
                archive_reader=archive.get,
            )
            self.assertIn("write-call", live.live_node_ids)
            self.assertIn("write-result", live.live_node_ids)
            self.assertFalse(live.evicted_node_ids)

    def test_explicit_consumed_and_invalidated_relations_are_terminal(self) -> None:
        for relation in ("consumed", "invalidated"):
            with self.subTest(relation=relation), tempfile.TemporaryDirectory() as directory:
                archive = ArchiveStore(Path(directory))
                graph = TraceGraph(session_id=f"terminal-{relation}")
                graph.create_node(
                    NodeType.GOAL,
                    "lookup",
                    0,
                    node_id="goal",
                    metadata={"source_message_ordinal": 1},
                )
                call_payload = {
                    "tool_name": "lookup",
                    "arguments": {"id": "7"},
                    "call_id": "c1",
                }
                call = graph.create_node(
                    NodeType.TOOL_CALL,
                    call_payload,
                    1,
                    node_id="call",
                    raw_ref=archive.put(call_payload),
                    metadata={
                        "tool_name": "lookup",
                        "operation_key": "lookup:7",
                        "call_id": "c1",
                        "source_message_ordinal": 2,
                    },
                )
                result_payload = {"value": "old"}
                result = graph.create_node(
                    NodeType.OBSERVATION,
                    result_payload,
                    2,
                    node_id="result",
                    raw_ref=archive.put(result_payload),
                    metadata={
                        "semantic_outcome": SemanticOutcome.POSITIVE.value,
                        "source_message_ordinal": 3,
                    },
                )
                graph.connect(call.node_id, result.node_id, EdgeType.PRODUCES)
                if relation == "consumed":
                    decision = graph.create_node(
                        NodeType.DECISION,
                        "used result",
                        3,
                        node_id="consumer",
                        metadata={"source_message_ordinal": 4},
                    )
                    graph.connect(
                        result.node_id,
                        decision.node_id,
                        EdgeType.PROVIDES_INPUT,
                    )
                else:
                    graph.create_node(
                        NodeType.OBSERVATION,
                        {"value": "invalidator"},
                        3,
                        node_id="invalidator",
                        metadata={
                            "source_message_ordinal": 4,
                            "invalidates_event_ids": ["result"],
                            "invalidation_verifier": "deterministic_fixture_v1",
                            "invalidation_confidence": 1.0,
                        },
                    )
                schemas = (tool_schema("lookup", "id"),)
                state = build_state(graph, tool_schemas=schemas)
                query = build_decision_query(
                    state.decision_state,
                    tool_schemas=schemas,
                )
                live = analyze_liveness(
                    graph,
                    state,
                    derive_roots(state, query, schemas),
                    archive_reader=archive.get,
                )
                self.assertEqual(set(live.evicted_node_ids), {"call", "result"})


class PruneProtocolTests(unittest.TestCase):
    def test_missing_tool_result_is_retained_but_send_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph = TraceGraph(session_id="missing-result")
            graph.create_node(
                NodeType.GOAL,
                "lookup",
                0,
                node_id="goal",
                metadata={"source_message_ordinal": 1},
            )
            payload = {
                "tool_name": "lookup",
                "arguments": {"id": "1"},
                "call_id": "missing",
            }
            graph.create_node(
                NodeType.TOOL_CALL,
                payload,
                1,
                node_id="missing-call",
                raw_ref=archive.put(payload),
                metadata={
                    "tool_name": "lookup",
                    "operation_key": "lookup:1",
                    "call_id": "missing",
                    "source_message_ordinal": 2,
                },
            )
            messages = (
                {"role": "user", "content": "lookup"},
                _call_message("missing", "lookup", '{"id":"1"}'),
            )
            schemas = (tool_schema("lookup", "id"),)
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            live = analyze_liveness(
                graph,
                state,
                derive_roots(state, query, schemas),
                archive_reader=archive.get,
            )
            self.assertFalse(live.evicted_span_ids)
            view = project_context(
                graph,
                live,
                "gdsc_prune_v1",
                ProviderProtocol(
                    model="test",
                    base_messages=messages,
                    tools=schemas,
                ),
            )
            self.assertFalse(view.protocol_valid)
            self.assertFalse(view.send_eligible)
            self.assertTrue(
                any("requires exactly one result" in item for item in view.protocol_errors)
            )

    def test_parallel_tool_span_is_atomic_when_one_result_remains_live(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph = TraceGraph(session_id="parallel")
            graph.create_node(
                NodeType.GOAL,
                "lookup two",
                0,
                node_id="goal",
                metadata={"source_message_ordinal": 1},
            )
            calls = []
            for index in (1, 2):
                payload = {
                    "tool_name": "lookup",
                    "arguments": {"id": str(index)},
                    "call_id": f"c{index}",
                }
                calls.append(
                    graph.create_node(
                        NodeType.TOOL_CALL,
                        payload,
                        1,
                        node_id=f"call-{index}",
                        raw_ref=archive.put(payload),
                        metadata={
                            "tool_name": "lookup",
                            "operation_key": f"lookup:{index}",
                            "call_id": f"c{index}",
                            "source_message_ordinal": 2,
                        },
                    )
                )
                result_payload = {"id": index}
                result = graph.create_node(
                    NodeType.OBSERVATION,
                    result_payload,
                    index + 1,
                    node_id=f"result-{index}",
                    raw_ref=archive.put(result_payload),
                    metadata={
                        "semantic_outcome": SemanticOutcome.POSITIVE.value,
                        "source_message_ordinal": index + 2,
                    },
                )
                graph.connect(calls[-1].node_id, result.node_id, EdgeType.PRODUCES)
            superseder_payload = {"id": 3}
            superseder = graph.create_node(
                NodeType.OBSERVATION,
                superseder_payload,
                5,
                node_id="result-3",
                raw_ref=archive.put(superseder_payload),
            )
            graph.connect("result-1", superseder.node_id, EdgeType.SUPERSEDED_BY)
            schemas = (tool_schema("lookup", "id"),)
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            live = analyze_liveness(
                graph,
                state,
                derive_roots(state, query, schemas),
                archive_reader=archive.get,
            )
            parallel_spans = [
                span for span in live.spans if set(span.call_ids) == {"c1", "c2"}
            ]
            self.assertEqual(len(parallel_spans), 1)
            self.assertIn(parallel_spans[0].span_id, live.live_span_ids)
            self.assertNotIn("call-1", live.evicted_node_ids)
            self.assertNotIn("result-1", live.evicted_node_ids)

    def test_duplicate_and_out_of_order_results_are_send_ineligible(self) -> None:
        graph = TraceGraph(session_id="protocol-invalid")
        state = build_state(graph, 0)
        query = build_decision_query(state.decision_state)
        live = analyze_liveness(graph, state, derive_roots(state, query))
        out_of_order = (
            {"role": "tool", "tool_call_id": "c1", "content": "early"},
            _call_message("c1", "lookup"),
            {"role": "tool", "tool_call_id": "c1", "content": "duplicate"},
        )
        view = project_context(
            graph,
            live,
            "gdsc_prune_v1",
            ProviderProtocol(model="test", base_messages=out_of_order),
        )
        self.assertFalse(view.protocol_valid)
        self.assertFalse(view.send_eligible)
        self.assertTrue(
            any("duplicate tool results" in item for item in view.protocol_errors)
        )

    def test_soft_and_hard_budgets_never_delete_additional_live_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph, messages, schemas = superseded_fixture(archive)
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            live = analyze_liveness(
                graph,
                state,
                derive_roots(state, query, schemas),
                archive_reader=archive.get,
            )
            soft = project_context(
                graph,
                live,
                ProjectionStrategy(soft_budget=1),
                ProviderProtocol(
                    model="test",
                    base_messages=messages,
                    tools=schemas,
                    hard_context_limit=100_000,
                ),
            )
            self.assertTrue(soft.budget_infeasible)
            self.assertFalse(soft.matched_budget_eligible)
            self.assertTrue(soft.send_eligible)
            self.assertEqual(set(soft.evicted_node_ids), {"call-1", "result-1"})

            hard = project_context(
                graph,
                live,
                ProjectionStrategy(),
                ProviderProtocol(
                    model="test",
                    base_messages=messages,
                    tools=schemas,
                    hard_context_limit=1,
                ),
            )
            self.assertTrue(hard.hard_limit_exceeded)
            self.assertFalse(hard.send_eligible)
            self.assertEqual(hard.messages, soft.messages)

    def test_mixed_assistant_content_restores_the_entire_raw_span(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph, original_messages, schemas = superseded_fixture(archive)
            messages = list(original_messages)
            messages[1] = dict(messages[1])
            messages[1]["content"] = "Important non-tool explanation"
            state = build_state(graph, tool_schemas=schemas)
            query = build_decision_query(state.decision_state, tool_schemas=schemas)
            live = analyze_liveness(
                graph,
                state,
                derive_roots(state, query, schemas),
                archive_reader=archive.get,
            )
            self.assertTrue(live.evicted_span_ids)
            view = project_context(
                graph,
                live,
                "gdsc_prune_v1",
                ProviderProtocol(
                    model="test",
                    base_messages=tuple(messages),
                    tools=schemas,
                ),
            )
            self.assertFalse(view.evicted_span_ids)
            self.assertEqual(view.raw_message_ordinals, (1, 2, 3, 4, 5, 6))
            self.assertTrue(
                any(
                    item["reason"]
                    == "assistant_tool_message_contains_non_tool_content"
                    for item in view.fallback_records
                )
            )

    def test_structured_projection_remains_gate_locked(self) -> None:
        graph = TraceGraph(session_id="structured-gated")
        state = build_state(graph, 0)
        query = build_decision_query(state.decision_state)
        live = analyze_liveness(graph, state, derive_roots(state, query))
        with self.assertRaises(PermissionError):
            project_context(
                graph,
                live,
                "gdsc_structured_v1",
                ProviderProtocol(model="test"),
            )


class LifecycleManagerTests(unittest.TestCase):
    def test_manager_exposes_all_four_phase5_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = ArchiveStore(Path(directory))
            graph, messages, schemas = superseded_fixture(archive)
            manager = LifecycleGraphContextManager(
                model="test-model",
                hard_context_limit=100_000,
            )
            graph_before = graph.to_dict()
            first = manager.compile(
                graph,
                messages=messages,
                system_rules=("fixed policy",),
                tool_schemas=schemas,
                archive_reader=archive.get,
            )
            self.assertEqual(graph.to_dict(), graph_before)
            second = manager.compile(
                graph,
                messages=messages,
                system_rules=("fixed policy",),
                tool_schemas=schemas,
                archive_reader=archive.get,
            )
            self.assertEqual(first.state.lifecycle_hash, second.state.lifecycle_hash)
            self.assertEqual(first.roots.roots_hash, second.roots.roots_hash)
            self.assertEqual(
                first.live_subgraph.live_subgraph_hash,
                second.live_subgraph.live_subgraph_hash,
            )
            self.assertEqual(
                first.context_view.request_hash,
                second.context_view.request_hash,
            )
            self.assertEqual(manager.name, "lifecycle_graph_context")
            self.assertEqual(manager.context_policy_version, "gdsc_prune_v1")
            self.assertEqual(
                first.context_view.projection_strategy["policy_version"],
                "gdsc_prune_v1",
            )


if __name__ == "__main__":
    unittest.main()
