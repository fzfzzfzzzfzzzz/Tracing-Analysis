"""Deterministic development-only acceptance metrics for context policy v4."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from ..context_engine import GraphConstrainedPolicy, MemorySnapshot, ContextPlan
from ..context_engine.protocol_v4 import _protocol_errors
from ..graph import TraceGraph
from ..schema import EdgeType, LifecycleProfile, Node, NodeType, StorageState


@dataclass(frozen=True, slots=True)
class PolicyAcceptanceCase:
    name: str
    snapshot: MemorySnapshot
    plan: ContextPlan
    full_history_plan: ContextPlan
    required_evidence_ids: tuple[str, ...] = ()
    unresolved_failure_ids: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()
    causal_chain_ids: tuple[str, ...] = ()
    unrelated_query: bool = False


def _selected_nodes(case: PolicyAcceptanceCase) -> set[str]:
    spans = case.snapshot.span_map()
    return {
        node_id
        for span_id in case.plan.selected_span_ids
        for node_id in spans[span_id].node_ids
    }


def _coverage(selected: set[str], required: tuple[str, ...]) -> tuple[int, int]:
    return len(selected.intersection(required)), len(required)


def evaluate_policy_cases(
    cases: tuple[PolicyAcceptanceCase, ...],
    *,
    executed_unauthorized_side_effects: int = 0,
) -> dict[str, Any]:
    if not cases:
        raise ValueError("at least one policy acceptance case is required")
    evidence_hit = evidence_total = 0
    failure_hit = failure_total = 0
    constraint_hit = constraint_total = 0
    causal_hit = causal_total = 0
    unsafe_removed: set[str] = set()
    compression: list[float] = []
    unrelated_retrieved = unrelated_candidates = 0
    protocol_closed = 0
    archive_ok = archive_total = 0
    for case in cases:
        selected = _selected_nodes(case)
        for required, counters in (
            (case.required_evidence_ids, "evidence"),
            (case.unresolved_failure_ids, "failure"),
            (case.constraint_ids, "constraint"),
            (case.causal_chain_ids, "causal"),
        ):
            hit, total = _coverage(selected, required)
            if counters == "evidence":
                evidence_hit += hit
                evidence_total += total
            elif counters == "failure":
                failure_hit += hit
                failure_total += total
            elif counters == "constraint":
                constraint_hit += hit
                constraint_total += total
            else:
                causal_hit += hit
                causal_total += total
        protected = set(case.required_evidence_ids)
        protected.update(case.unresolved_failure_ids)
        protected.update(case.constraint_ids)
        unsafe_removed.update(protected.difference(selected))
        full_tokens = int(case.full_history_plan.token_statistics["provider_input_tokens"])
        candidate_tokens = int(case.plan.token_statistics["provider_input_tokens"])
        compression.append((full_tokens - candidate_tokens) / full_tokens)
        if case.unrelated_query:
            unrelated_retrieved += len(case.plan.retrieved_span_ids)
            unrelated_candidates += max(
                1, len(case.snapshot.spans) - len(case.snapshot.selected_span_ids)
            )
        if case.plan.send_eligible and not _protocol_errors(case.plan.messages):
            protocol_closed += 1
        for record in case.snapshot.lifecycle_records:
            if record.storage == StorageState.ARCHIVED.value:
                archive_total += 1
                if not record.uncertain:
                    archive_ok += 1

    def ratio(hit: int, total: int) -> float:
        return 1.0 if total == 0 else hit / total

    return {
        "schema_version": "context_policy_v4_acceptance_v1",
        "development_only": True,
        "independent_validation": False,
        "provider_requests": 0,
        "evidence_coverage": ratio(evidence_hit, evidence_total),
        "unresolved_failure_coverage": ratio(failure_hit, failure_total),
        "constraint_coverage": ratio(constraint_hit, constraint_total),
        "archive_recoverability": ratio(archive_ok, archive_total),
        "protocol_closure": protocol_closed / len(cases),
        "unsafe_removal_count": len(unsafe_removed),
        "executed_unauthorized_side_effects": executed_unauthorized_side_effects,
        "causal_retrieval_completeness": ratio(causal_hit, causal_total),
        "unrelated_question_retrieval_rate": ratio(
            unrelated_retrieved, unrelated_candidates
        ),
        "safe_compression_ratio_median": statistics.median(compression),
        "single_aggregate_score": None,
        "case_count": len(cases),
    }


def _add_node(
    graph: TraceGraph,
    node_id: str,
    node_type: NodeType,
    content: Any,
    step: int,
    ordinal: int,
    *,
    active: bool = False,
    metadata: dict[str, Any] | None = None,
    side_effect: bool = False,
    storage: StorageState = StorageState.RAW_IN_CONTEXT,
) -> None:
    graph.add_node(Node(
        node_id=node_id,
        node_type=node_type,
        content=content,
        step_id=step,
        active=active,
        raw_ref=f"fixture://{node_id}",
        side_effect=side_effect,
        metadata={"source_message_ordinal": ordinal, **(metadata or {})},
        lifecycle_profile=LifecycleProfile(storage=storage),
    ))


def deterministic_policy_cases() -> tuple[PolicyAcceptanceCase, ...]:
    """Build a fixed safety/retrieval set without external data or provider calls."""

    graph = TraceGraph("policy-v4-acceptance")
    messages: list[dict[str, Any]] = []

    def message(role: str, content: str, **extra: Any) -> int:
        messages.append({"role": role, "content": content, **extra})
        return len(messages)

    constraint_ordinal = message("system", "Never repeat a recorded side effect.")
    _add_node(
        graph, "constraint", NodeType.CONSTRAINT, "Never repeat a recorded side effect.",
        1, constraint_ordinal, active=True,
    )
    call_id = "historical-call"
    pad = " historical-detail" * 80
    call_ordinal = message(
        "assistant", "", tool_calls=[{
            "id": call_id,
            "type": "function",
            "function": {"name": "legacy_shell", "arguments": '{"entity":"alpha"}'},
        }],
    )
    _add_node(
        graph, "old-call", NodeType.TOOL_CALL,
        {"tool_name": "legacy_shell", "arguments": {"entity": "alpha"}},
        2, call_ordinal, metadata={"call_id": call_id, "tool_name": "legacy_shell"},
    )
    error_ordinal = message("tool", f"shell_syntax_mismatch{pad}", tool_call_id=call_id)
    _add_node(
        graph, "old-error", NodeType.ERROR,
        {"error": "shell_syntax_mismatch", "entity": "alpha"},
        3, error_ordinal, metadata={"call_id": call_id},
    )
    decision_ordinal = message("assistant", f"Use the native shell instead.{pad}")
    _add_node(
        graph, "decision", NodeType.DECISION, "Use the native shell instead.",
        4, decision_ordinal,
    )
    replacement_id = "replacement-call"
    replacement_ordinal = message(
        "assistant", "", tool_calls=[{
            "id": replacement_id,
            "type": "function",
            "function": {"name": "native_shell", "arguments": '{"entity":"alpha"}'},
        }],
    )
    _add_node(
        graph, "replacement-call", NodeType.TOOL_CALL,
        {"tool_name": "native_shell", "arguments": {"entity": "alpha"}},
        5, replacement_ordinal,
        metadata={"call_id": replacement_id, "tool_name": "native_shell"},
    )
    result_ordinal = message("tool", f"success{pad}", tool_call_id=replacement_id)
    _add_node(
        graph, "replacement-result", NodeType.OBSERVATION, {"status": "success"},
        6, result_ordinal, metadata={"call_id": replacement_id},
    )
    graph.connect("old-call", "old-error", EdgeType.FAILED_WITH)
    graph.connect("old-error", "decision", EdgeType.RESOLVED_BY)
    graph.connect("decision", "replacement-call", EdgeType.LEADS_TO)
    graph.connect("replacement-call", "replacement-result", EdgeType.PRODUCES)

    unresolved_ordinal = message("assistant", "Unresolved checksum mismatch E77.")
    _add_node(
        graph, "unresolved", NodeType.ERROR, {"error": "checksum_mismatch_E77"},
        7, unresolved_ordinal,
    )
    receipt_ordinal = message("assistant", "Receipt: notification already sent once.")
    _add_node(
        graph, "receipt", NodeType.OBSERVATION, {"receipt": "sent"},
        8, receipt_ordinal, side_effect=True,
    )
    current_ordinal = message("assistant", "Current alpha status is healthy.")
    _add_node(
        graph, "current", NodeType.OBSERVATION,
        {"entity": "alpha", "field": "status", "value": "healthy"},
        9, current_ordinal, active=True, metadata={"critical_evidence": True},
    )
    archived_ordinal = message("assistant", "Archived receipt can be recovered.")
    _add_node(
        graph, "archived", NodeType.OBSERVATION, {"receipt": "archived"},
        10, archived_ordinal, storage=StorageState.ARCHIVED,
    )
    for index in range(8):
        ordinal = message("assistant", f"irrelevant-{index}" + " filler" * 100)
        _add_node(
            graph, f"irrelevant-{index}", NodeType.OBSERVATION,
            f"irrelevant-{index}", 11 + index, ordinal,
        )

    goal_context = {
        "messages": messages,
        "tool_schemas": [
            {"function": {"name": "legacy_shell"}},
            {"function": {"name": "native_shell"}},
        ],
        "archive_reader": lambda _: {"verified": True},
    }
    policy = GraphConstrainedPolicy()
    snapshot = policy.snapshot(graph, goal_context, 160)
    full_policy = GraphConstrainedPolicy("full-history", selection_mode="full")
    full_snapshot = full_policy.snapshot(graph, goal_context, 4096)

    def case(name: str, query: dict[str, Any], *, unrelated: bool) -> PolicyAcceptanceCase:
        plan = policy.materialize(
            snapshot, query, {"max_input_tokens": 4096, "retrieval_budget_tokens": 1024}
        )
        full = full_policy.materialize(full_snapshot, query, {"max_input_tokens": 4096})
        return PolicyAcceptanceCase(
            name=name,
            snapshot=snapshot,
            plan=plan,
            full_history_plan=full,
            required_evidence_ids=("current", "receipt"),
            unresolved_failure_ids=("unresolved",),
            constraint_ids=("constraint",),
            causal_chain_ids=(
                "old-call", "old-error", "decision", "replacement-call", "replacement-result"
            ) if not unrelated else (),
            unrelated_query=unrelated,
        )

    return (
        case(
            "explicit-historical-id",
            {"text": "Explain that failure", "referenced_event_ids": ["old-error"]},
            unrelated=False,
        ),
        case("unrelated-current-question", {"text": "What is the weather?"}, unrelated=True),
    )


def verify_policy_v4_acceptance() -> dict[str, Any]:
    report = evaluate_policy_cases(deterministic_policy_cases())
    exact = (
        "evidence_coverage",
        "unresolved_failure_coverage",
        "constraint_coverage",
        "archive_recoverability",
        "protocol_closure",
    )
    if any(report[name] != 1.0 for name in exact):
        raise RuntimeError("policy v4 exact safety or closure gate failed")
    if report["unsafe_removal_count"] != 0:
        raise RuntimeError("policy v4 removed protected evidence")
    if report["executed_unauthorized_side_effects"] != 0:
        raise RuntimeError("policy v4 recorded an unauthorized side effect")
    if report["causal_retrieval_completeness"] < 0.95:
        raise RuntimeError("policy v4 causal retrieval completeness is below 0.95")
    if report["unrelated_question_retrieval_rate"] != 0.0:
        raise RuntimeError("policy v4 retrieved history for an unrelated question")
    if report["safe_compression_ratio_median"] < 0.20:
        raise RuntimeError("policy v4 safe compression median is below 20%")
    return report
