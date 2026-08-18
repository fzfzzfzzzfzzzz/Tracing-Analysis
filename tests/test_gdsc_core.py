from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tracegraph.archive import ArchiveStore
from tracegraph.compiler import CompilerConfig, compile
from tracegraph.decision_query import build_decision_query
from tracegraph.decision_state import (
    DecisionStateGraph,
    StateAtom,
    StateAtomType,
)
from tracegraph.graph import TraceGraph
from tracegraph.negative_guards import build_negative_guard
from tracegraph.omission_risk import DeterministicRiskModel, LogisticRiskArtifact
from tracegraph.provider_cost import (
    PromptBudget,
    ProviderProtocol,
    close_protocol_messages,
    provider_prompt_request,
    request_sha256,
    serialized_request_cost,
)
from tracegraph.representation_verifiers import (
    verify_archive_round_trip,
    verify_structured_equivalence,
    verify_summary_claims,
)
from tracegraph.representations import RepresentationType, generate_representations
from tracegraph.schema import EdgeType, NodeType, SemanticOutcome
from tracegraph.state_reducer import reduce_event_graph


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


def prefix_graph(*, completed: bool = True, side_effect: bool = False) -> TraceGraph:
    graph = TraceGraph(session_id="session-fixed")
    graph.create_node(
        NodeType.GOAL,
        "Cancel order 7",
        0,
        node_id="goal",
        metadata={"source_message_ordinal": 1},
    )
    call = graph.create_node(
        NodeType.TOOL_CALL,
        {
            "tool_name": "cancel_order",
            "arguments": {"order_id": "7"},
            "call_id": "call-1",
        },
        1,
        node_id="call-1",
        side_effect=side_effect,
        raw_ref="sha256:" + "1" * 64,
        metadata={
            "tool_name": "cancel_order",
            "operation_key": "cancel_order:7",
            "call_id": "call-1",
            "source_message_ordinal": 2,
        },
    )
    if completed:
        result = graph.create_node(
            NodeType.OBSERVATION,
            {"status": "cancelled"},
            2,
            node_id="result-1",
            raw_ref="sha256:" + "2" * 64,
            metadata={
                "semantic_outcome": SemanticOutcome.POSITIVE.value,
                "source_message_ordinal": 3,
            },
        )
        graph.connect(call.node_id, result.node_id, EdgeType.PRODUCES)
    return graph


class DecisionStateTests(unittest.TestCase):
    def test_state_hash_is_stable_and_future_suffix_does_not_change_prefix(self) -> None:
        graph = prefix_graph()
        first = reduce_event_graph(graph, 2)
        second = reduce_event_graph(graph, 2)
        self.assertEqual(first.state_hash, second.state_hash)

        future = graph.create_node(
            NodeType.TOOL_CALL,
            {"tool_name": "cancel_order", "arguments": {"order_id": "8"}},
            9,
            node_id="future-call",
            metadata={"tool_name": "cancel_order", "operation_key": "cancel_order:8"},
        )
        future_result = graph.create_node(
            NodeType.ERROR,
            {"error": "future"},
            10,
            node_id="future-result",
            metadata={"semantic_outcome": SemanticOutcome.NEGATIVE.value},
        )
        graph.connect(future.node_id, future_result.node_id, EdgeType.FAILED_WITH)
        graph.connect("result-1", future_result.node_id, EdgeType.SUPERSEDED_BY)
        self.assertEqual(first.state_hash, reduce_event_graph(graph, 2).state_hash)

        restored = DecisionStateGraph.from_dict(first.to_dict())
        self.assertEqual(restored.state_hash, first.state_hash)

    def test_pending_operation_required_slot_and_confirmation_are_hard(self) -> None:
        graph = prefix_graph(completed=False, side_effect=True)
        schema = tool_schema("cancel_order", "order_id", "reason")
        state = reduce_event_graph(graph, tool_schemas=(schema,))

        self.assertEqual(len(state.find_atoms(StateAtomType.PENDING_OPERATION)), 1)
        missing = state.find_atoms(StateAtomType.UNKNOWN_SLOT)
        self.assertEqual([atom.value["slot"] for atom in missing], ["reason"])
        self.assertTrue(missing[0].hard)
        confirmations = state.find_atoms(StateAtomType.CONFIRMATION_REQUIREMENT)
        self.assertEqual(len(confirmations), 1)
        self.assertTrue(confirmations[0].hard)

        query = build_decision_query(state, tool_schemas=(schema, tool_schema("lookup", "id")))
        self.assertEqual(query.candidate_tools, ("cancel_order", "lookup"))
        self.assertIn("reason", query.required_slots)
        self.assertEqual(query.side_effect_level, "irreversible")

    def test_completed_side_effect_has_receipt(self) -> None:
        state = reduce_event_graph(prefix_graph(side_effect=True))
        receipts = state.find_atoms(StateAtomType.SIDE_EFFECT_RECEIPT)
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0].hard)
        self.assertEqual(receipts[0].value["result"], {"status": "cancelled"})

    def test_later_slot_value_supersedes_old_value(self) -> None:
        graph = prefix_graph()
        graph.create_node(
            NodeType.TOOL_CALL,
            {"tool_name": "cancel_order", "arguments": {"order_id": "8"}},
            3,
            node_id="call-2",
            metadata={"tool_name": "cancel_order", "operation_key": "cancel_order:8"},
        )
        state = reduce_event_graph(graph)
        slots = [atom for atom in state.atoms if atom.key == "slot:cancel_order:order_id"]
        self.assertEqual({atom.status for atom in slots}, {"current", "superseded"})
        current = [atom for atom in slots if atom.status == "current"]
        self.assertEqual(current[0].value["value"], "8")


class ProviderCostTests(unittest.TestCase):
    def test_parallel_tool_calls_close_all_results_with_provenance(self) -> None:
        messages = (
            {"role": "user", "content": "look up both"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "a"}},
                    {"id": "c2", "function": {"name": "b"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "one"},
            {"role": "tool", "tool_call_id": "c2", "content": "two"},
        )
        closure = close_protocol_messages(messages, {3})
        self.assertTrue(closure.valid)
        self.assertEqual(closure.ordinals, (1, 2, 3, 4))
        self.assertEqual({record.added_ordinal for record in closure.records}, {1, 2, 4})

    def test_missing_tool_result_fails_protocol_check(self) -> None:
        messages = (
            {"role": "user", "content": "do it"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "a"}}]},
        )
        closure = close_protocol_messages(messages, {2})
        self.assertFalse(closure.valid)
        self.assertIn("has no result", closure.errors[0])

    def test_serialized_cost_includes_tool_schema_and_hash_is_stable(self) -> None:
        messages = ({"role": "user", "content": "hello"},)
        plain = ProviderProtocol(base_messages=messages)
        tooled = ProviderProtocol(base_messages=messages, tools=(tool_schema("lookup", "id"),))
        self.assertGreater(
            serialized_request_cost(tooled, messages),
            serialized_request_cost(plain, messages),
        )
        prompt = provider_prompt_request(
            model=tooled.model,
            messages=messages,
            tools=tooled.tools,
        )
        self.assertEqual(prompt, tooled.request(messages))
        self.assertEqual(request_sha256(prompt), request_sha256(tooled.request(messages)))
        self.assertNotIn("tool_choice", prompt)


class RepresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.atom = StateAtom.create(
            StateAtomType.STATE_DELTA,
            "order:7:status",
            {"before": "pending", "after": "cancelled"},
            ("event-1",),
            raw_refs=("sha256:" + "a" * 64,),
        )

    def test_structured_and_summary_verifiers_detect_tamper(self) -> None:
        candidates = generate_representations(
            self.atom,
            allow_omit=True,
            omission_risk=0.1,
            omission_reason="recoverable_state_delta",
        )
        structured = next(
            item
            for item in candidates
            if item.representation_type == RepresentationType.STRUCTURED_STATE_DELTA
        )
        summary = next(
            item
            for item in candidates
            if item.representation_type == RepresentationType.VERIFIED_SUMMARY
        )
        self.assertTrue(verify_structured_equivalence(structured, self.atom).ok)
        tampered_payload = dict(structured.payload)
        tampered_payload["value"] = "wrong"
        self.assertFalse(
            verify_structured_equivalence(replace(structured, payload=tampered_payload), self.atom).ok
        )
        self.assertTrue(verify_summary_claims(summary, {self.atom.atom_id: self.atom}).ok)

    def test_archive_round_trip_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchiveStore(Path(directory) / "archive")
            reference = store.put({"raw": "value"})
            atom = replace(self.atom, raw_refs=(reference,))
            handle = next(
                item
                for item in generate_representations(
                    atom,
                    allow_omit=False,
                    omission_risk=0.0,
                    omission_reason="not_requested",
                )
                if item.representation_type == RepresentationType.ARCHIVE_HANDLE
            )
            self.assertTrue(verify_archive_round_trip(handle, store.get).ok)
            bad = replace(handle, raw_refs=("sha256:" + "f" * 64,))
            self.assertFalse(verify_archive_round_trip(bad, store.get).ok)

    def test_generic_negative_guard_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "generic"):
            build_negative_guard(
                action_family="cancel_order",
                entity_scope=("order:7",),
                violated_predicate="confirmation_present",
                observed_failure="denied",
                failed_argument_delta={},
                admissible_alternatives=("change arguments or use alternative path",),
                expiry_condition="confirmed",
                source_event_ids=("event-1",),
                verifier="policy:cancel_confirmation",
            )


class RiskArtifactTests(unittest.TestCase):
    def test_logistic_artifact_round_trip_hash_and_gate(self) -> None:
        artifact = LogisticRiskArtifact(
            intercept=-1.0,
            coefficients={"hard": 2.0},
            feature_names=("hard",),
            threshold=0.5,
            calibration={"method": "platt"},
            metrics={
                "harm_positives": 20,
                "high_risk_recall": 0.91,
                "ece": 0.09,
                "brier": 0.1,
                "constant_brier": 0.2,
            },
            training_provenance={"split": "task_held_out", "dataset_hash": "abc"},
        )
        payload = artifact.to_dict()
        restored = LogisticRiskArtifact.from_dict(payload)
        self.assertTrue(restored.eligible_for_runtime)
        payload["threshold"] = 0.9
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            LogisticRiskArtifact.from_dict(payload)


class CompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = prefix_graph()
        self.state = reduce_event_graph(self.graph)
        self.query = build_decision_query(
            self.state,
            tool_schemas=(tool_schema("cancel_order", "order_id"),),
        )
        self.protocol = ProviderProtocol(
            system_rules=("Follow the retail policy exactly.",),
            base_messages=(
                {"role": "user", "content": "Cancel order 7"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "cancel_order",
                                "arguments": '{"order_id":"7"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "cancelled"},
            ),
            tools=(tool_schema("cancel_order", "order_id"),),
            hard_context_limit=20_000,
        )

    def test_compile_is_deterministic_and_preserves_hard_coverage(self) -> None:
        first = compile(
            self.graph,
            self.state,
            self.query,
            self.protocol,
            20_000,
            DeterministicRiskModel(),
        )
        second = compile(
            self.graph,
            self.state,
            self.query,
            self.protocol,
            20_000,
            DeterministicRiskModel(),
        )
        self.assertEqual(first.request_hash, second.request_hash)
        self.assertEqual(first.representation_manifest, second.representation_manifest)
        represented = {
            atom_id
            for item in first.representation_manifest
            if item["representation_type"] != RepresentationType.OMIT.value
            for atom_id in item["covered_atoms"]
        }
        self.assertTrue({atom.atom_id for atom in self.state.atoms if atom.hard} <= represented)
        self.assertTrue(first.matched_budget_eligible)
        self.assertGreater(first.costs.serialized_request, first.costs.compiled)

    def test_soft_budget_is_explicit_conservative_fallback(self) -> None:
        bundle = compile(
            self.graph,
            self.state,
            self.query,
            self.protocol,
            PromptBudget(soft_limit=1, hard_limit=20_000),
        )
        self.assertTrue(bundle.budget_infeasible)
        self.assertFalse(bundle.matched_budget_eligible)
        self.assertFalse(bundle.hard_limit_exceeded)

    def test_hard_limit_is_flagged_and_all_ablations_are_reproducible(self) -> None:
        hard = compile(
            self.graph,
            self.state,
            self.query,
            self.protocol,
            PromptBudget(soft_limit=None, hard_limit=1),
        )
        self.assertTrue(hard.hard_limit_exceeded)
        for ablation in (
            "no_graph",
            "no_lifecycle",
            "keep_drop_only",
            "node_cost_only",
            "no_policy_checker",
            "no_negative_guard",
        ):
            left = compile(
                self.graph,
                self.state,
                self.query,
                self.protocol,
                20_000,
                config=CompilerConfig(ablations=frozenset({ablation})),
            )
            right = compile(
                self.graph,
                self.state,
                self.query,
                self.protocol,
                20_000,
                config=CompilerConfig(ablations=frozenset({ablation})),
            )
            self.assertEqual(left.request_hash, right.request_hash, ablation)


if __name__ == "__main__":
    unittest.main()
