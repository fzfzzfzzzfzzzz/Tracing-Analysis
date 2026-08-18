"""Deterministic, fail-closed GDSC-Core compiler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .capture import estimate_tokens
from .compiler_checks import verify_compilation
from .decision_query import DecisionQuery
from .decision_state import DecisionStateGraph, StateAtom, StateAtomType, stable_digest
from .graph import TraceGraph
from .omission_risk import DeterministicRiskModel, OmissionRiskModel
from .prompt_bundle import PromptBundle
from .provider_cost import (
    PromptBudget,
    PromptCost,
    ProtocolClosure,
    ProviderProtocol,
    close_protocol_messages,
    coerce_budget,
    request_sha256,
    serialized_request_cost,
)
from .representation_verifiers import verify_candidate
from .representations import (
    RepresentationCandidate,
    RepresentationType,
    generate_representations,
)


@dataclass(frozen=True, slots=True)
class CompilerConfig:
    beam_width: int = 16
    risk_weight: float = 512.0
    risk_threshold: float = 0.5
    ablations: frozenset[str] = frozenset()
    version: str = "gdsc_core_v1"

    def __post_init__(self) -> None:
        if not 1 <= self.beam_width <= 128:
            raise ValueError("beam_width must be between 1 and 128")
        if self.risk_weight < 0:
            raise ValueError("risk_weight must be non-negative")
        if not 0.0 <= self.risk_threshold <= 1.0:
            raise ValueError("risk_threshold must be between zero and one")
        supported = {
            "no_graph",
            "no_lifecycle",
            "keep_drop_only",
            "node_cost_only",
            "no_policy_checker",
            "no_negative_guard",
        }
        unknown = set(self.ablations).difference(supported)
        if unknown:
            raise ValueError(f"unknown GDSC ablations: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class _BeamState:
    selections: tuple[RepresentationCandidate, ...]
    serialized_cost: int
    risk: float
    signature: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Assembly:
    messages: tuple[dict[str, Any], ...]
    closure: ProtocolClosure
    costs: PromptCost
    request_hash: str
    closure_provenance: tuple[dict[str, Any], ...]


def _system_messages(protocol: ProviderProtocol) -> list[dict[str, Any]]:
    if not protocol.system_rules:
        return []
    return [{"role": "system", "content": "\n\n".join(protocol.system_rules)}]


def _selection_fragment(candidate: RepresentationCandidate) -> str:
    return json.dumps(candidate.payload, ensure_ascii=False, sort_keys=True, default=str)


def _assemble(
    event_graph: TraceGraph,
    selections: tuple[RepresentationCandidate, ...],
    protocol: ProviderProtocol,
) -> _Assembly:
    raw_ordinals: set[int] = set()
    fragments: list[dict[str, Any]] = []
    source_node_ids: set[str] = set()
    for candidate in selections:
        source_node_ids.update(candidate.source_ids)
        if candidate.representation_type == RepresentationType.OMIT:
            continue
        if candidate.representation_type == RepresentationType.RAW_MESSAGE:
            found = False
            for source in candidate.source_ids:
                node = event_graph.nodes.get(source)
                ordinal = node.metadata.get("source_message_ordinal") if node else None
                if isinstance(ordinal, int) and 1 <= ordinal <= len(protocol.base_messages):
                    raw_ordinals.add(ordinal)
                    found = True
            if found:
                continue
        fragments.append(
            {
                "representation_id": candidate.representation_id,
                "type": candidate.representation_type.value,
                "payload": candidate.payload,
                "source_ids": list(candidate.source_ids),
            }
        )

    closure = close_protocol_messages(protocol.base_messages, raw_ordinals)
    selected_raw = [dict(protocol.base_messages[item - 1]) for item in sorted(raw_ordinals)]
    closed_raw = [dict(protocol.base_messages[item - 1]) for item in closure.ordinals]
    compiled_messages = _system_messages(protocol)
    if fragments:
        compiled_messages.append(
            {
                "role": "system",
                "content": json.dumps(
                    {"gdsc_decision_state": fragments},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            }
        )
    preclosure_messages = compiled_messages + selected_raw
    final_messages = tuple(compiled_messages + closed_raw)

    graph_selected = sum(
        event_graph.nodes[source].token_count or estimate_tokens(event_graph.nodes[source].content)
        for source in source_node_ids
        if source in event_graph.nodes
    )
    compiled = protocol.count(preclosure_messages)
    protocol_closed = protocol.count(final_messages)
    serialized = serialized_request_cost(protocol, final_messages)
    request = protocol.request(final_messages)
    return _Assembly(
        messages=final_messages,
        closure=closure,
        costs=PromptCost(graph_selected, compiled, protocol_closed, serialized),
        request_hash=request_sha256(request),
        closure_provenance=tuple(record.to_dict() for record in closure.records),
    )


def _candidate_order(candidate: RepresentationCandidate) -> tuple[int, int, str]:
    richness = {
        RepresentationType.STRUCTURED_STATE_DELTA: 0,
        RepresentationType.NEGATIVE_GUARD: 1,
        RepresentationType.VERIFIED_SUMMARY: 2,
        RepresentationType.RAW_MESSAGE: 3,
        RepresentationType.ARCHIVE_HANDLE: 4,
        RepresentationType.OMIT: 5,
    }
    return (richness[candidate.representation_type], candidate.estimated_cost, candidate.representation_id)


def _candidate_set(
    atom: StateAtom,
    state: DecisionStateGraph,
    query: DecisionQuery,
    risk_model: OmissionRiskModel,
    config: CompilerConfig,
) -> tuple[RepresentationCandidate, ...]:
    assessment = risk_model.assess(atom, state, query, RepresentationType.OMIT)
    omit_allowed = assessment.omit_allowed and assessment.probability < config.risk_threshold
    candidates = generate_representations(
        atom,
        allow_omit=omit_allowed,
        omission_risk=assessment.probability,
        omission_reason=",".join(assessment.reasons),
    )
    if "keep_drop_only" in config.ablations:
        candidates = tuple(
            item
            for item in candidates
            if item.representation_type in {RepresentationType.RAW_MESSAGE, RepresentationType.OMIT}
        )
    if "no_negative_guard" in config.ablations:
        candidates = tuple(
            item for item in candidates if item.representation_type != RepresentationType.NEGATIVE_GUARD
        )
    if (
        atom.atom_type
        in {StateAtomType.GLOBAL_POLICY_RULE, StateAtomType.APPLICABLE_POLICY_RULE}
        and "no_policy_checker" not in config.ablations
    ):
        # GDSC-Core retains full policy.  Policy-specific compression belongs to
        # GDSC-Policy and cannot be used to inflate the core token claim.
        raw = tuple(item for item in candidates if item.representation_type == RepresentationType.RAW_MESSAGE)
        if raw:
            candidates = raw
    if atom.hard:
        candidates = tuple(
            item
            for item in candidates
            if item.representation_type not in {RepresentationType.OMIT, RepresentationType.ARCHIVE_HANDLE}
        )
    return tuple(sorted(candidates, key=_candidate_order))


def _bundle_from_assembly(
    *,
    event_graph: TraceGraph,
    state: DecisionStateGraph,
    query: DecisionQuery,
    protocol: ProviderProtocol,
    assembly: _Assembly,
    selections: tuple[RepresentationCandidate, ...],
    budget: PromptBudget,
    log: tuple[dict[str, Any], ...],
    config: CompilerConfig,
) -> PromptBundle:
    soft_infeasible = budget.soft_limit is not None and assembly.costs.serialized_request > budget.soft_limit
    hard_exceeded = budget.hard_limit is not None and assembly.costs.serialized_request > budget.hard_limit
    return PromptBundle(
        messages=assembly.messages,
        tools=protocol.tools,
        representation_manifest=tuple(item.to_dict() for item in selections),
        closure_provenance=assembly.closure_provenance,
        request_hash=assembly.request_hash,
        costs=assembly.costs,
        compiler_decision_log=log,
        provider_protocol=protocol.manifest(),
        provenance_manifest={
            "event_graph_session_id": event_graph.session_id,
            "event_graph_schema_version": event_graph.schema_version,
            "event_graph_sha256": stable_digest(event_graph.to_dict()),
            "decision_state_hash": state.state_hash,
            "decision_query_hash": query.query_hash,
            "task_sha256": stable_digest(
                [
                    node.content
                    for node in event_graph.nodes.values()
                    if node.node_type.value in {"goal", "subgoal"}
                ]
            ),
            "tool_schema_sha256": stable_digest(protocol.tools),
            "policy_sha256": stable_digest(
                [
                    atom.value
                    for atom in state.atoms
                    if atom.atom_type
                    in {
                        StateAtomType.GLOBAL_POLICY_RULE,
                        StateAtomType.APPLICABLE_POLICY_RULE,
                    }
                ]
            ),
            "serializer": "canonical_request_json_v1",
            "tokenizer": protocol.manifest()["tokenizer"],
            "representation_schema": "gdsc_representation_v1",
            "risk_model": log[0].get("risk_model") if log else None,
            "ablation": sorted(config.ablations),
        },
        matched_budget_eligible=not soft_infeasible and not hard_exceeded and assembly.closure.valid,
        budget_infeasible=soft_infeasible,
        hard_limit_exceeded=hard_exceeded,
        compiler_version=config.version,
    )


def compile(
    event_graph: TraceGraph,
    decision_state: DecisionStateGraph,
    query: DecisionQuery,
    provider_protocol: ProviderProtocol,
    budget: int | PromptBudget | None,
    risk_model: OmissionRiskModel | None = None,
    *,
    config: CompilerConfig | None = None,
) -> PromptBundle:
    """Compile a final request; infeasible soft budgets return an explicit fallback."""

    effective_config = config or CompilerConfig()
    effective_risk = risk_model or DeterministicRiskModel()
    effective_budget = coerce_budget(budget, provider_protocol)
    event_ids = set(event_graph.nodes)
    atom_map = decision_state.atom_map()
    candidates_by_atom: list[tuple[StateAtom, tuple[RepresentationCandidate, ...]]] = []
    for atom in decision_state.atoms:
        candidates = tuple(
            candidate
            for candidate in _candidate_set(
                atom,
                decision_state,
                query,
                effective_risk,
                effective_config,
            )
            if verify_candidate(
                candidate,
                atom=atom,
                atoms=atom_map,
                event_ids=event_ids,
            ).ok
        )
        if not candidates:
            raise ValueError(f"no verified representation for state atom {atom.atom_id}")
        candidates_by_atom.append((atom, candidates))

    log: list[dict[str, Any]] = [
        {
            "event": "compiler_start",
            "compiler_version": effective_config.version,
            "beam_width": effective_config.beam_width,
            "risk_model": effective_risk.version,
            "soft_budget": effective_budget.soft_limit,
            "hard_limit": effective_budget.hard_limit,
            "canonical_tie_break": "serialized_cost,risk,representation_ids",
        }
    ]
    beam = (_BeamState((), 0, 0.0, ()),)
    for atom, candidates in candidates_by_atom:
        expanded: list[_BeamState] = []
        for current in beam:
            for candidate in candidates:
                chosen = current.selections + (candidate,)
                assembly = _assemble(event_graph, chosen, provider_protocol)
                marginal = assembly.costs.serialized_request - current.serialized_cost
                candidate = candidate.with_marginal_cost(marginal)
                chosen = current.selections + (candidate,)
                omission_risk = candidate.omission_risk or 0.0
                total_risk = current.risk + omission_risk
                cost_for_rank = (
                    sum(item.estimated_cost for item in chosen)
                    if "node_cost_only" in effective_config.ablations
                    else assembly.costs.serialized_request
                )
                expanded.append(
                    _BeamState(
                        chosen,
                        cost_for_rank,
                        total_risk,
                        tuple(item.representation_id for item in chosen),
                    )
                )
        expanded.sort(
            key=lambda item: (
                item.serialized_cost + effective_config.risk_weight * item.risk,
                item.serialized_cost,
                item.risk,
                item.signature,
            )
        )
        beam = tuple(expanded[: effective_config.beam_width])
        log.append(
            {
                "event": "beam_step",
                "atom_id": atom.atom_id,
                "candidate_count": len(candidates),
                "expanded_count": len(expanded),
                "retained_count": len(beam),
            }
        )

    finals: list[tuple[_BeamState, _Assembly]] = []
    for item in beam:
        finals.append((item, _assemble(event_graph, item.selections, provider_protocol)))
    feasible = [
        pair
        for pair in finals
        if pair[1].closure.valid
        and (
            effective_budget.soft_limit is None
            or pair[1].costs.serialized_request <= effective_budget.soft_limit
        )
        and (
            effective_budget.hard_limit is None
            or pair[1].costs.serialized_request <= effective_budget.hard_limit
        )
    ]
    pool = feasible or finals
    if not pool:
        # This occurs only for an empty DecisionStateGraph.  The empty bundle is
        # still auditable and may contain full system/tool schema cost.
        empty = _BeamState((), 0, 0.0, ())
        pool = [(empty, _assemble(event_graph, (), provider_protocol))]
    if feasible:
        selection_key = lambda pair: (  # noqa: E731 - named below for deterministic audit
            pair[1].costs.serialized_request + effective_config.risk_weight * pair[0].risk,
            pair[1].costs.serialized_request,
            pair[0].risk,
            pair[0].signature,
        )
    else:
        # No matched-budget solution: preserve the lowest-risk verified state,
        # then minimize cost.  This is the explicit conservative fallback.
        selection_key = lambda pair: (  # noqa: E731 - same tuple signature as above
            pair[0].risk,
            pair[1].costs.serialized_request,
            pair[0].signature,
        )
    selected, assembly = min(pool, key=selection_key)
    log.append(
        {
            "event": "compiler_selected",
            "feasible_candidate_count": len(feasible),
            "representation_ids": list(selected.signature),
            "serialized_tokens": assembly.costs.serialized_request,
            "conservative_fallback": not bool(feasible),
        }
    )
    bundle = _bundle_from_assembly(
        event_graph=event_graph,
        state=decision_state,
        query=query,
        protocol=provider_protocol,
        assembly=assembly,
        selections=selected.selections,
        budget=effective_budget,
        log=tuple(log),
        config=effective_config,
    )
    verification = verify_compilation(
        state=decision_state,
        selections=selected.selections,
        event_ids=event_ids,
        closure=assembly.closure,
        bundle=bundle,
        budget=effective_budget,
    )
    # Soft infeasibility is explicitly represented, not an invariant failure.
    hard_errors = [
        error
        for check in verification.checks
        if check.name != "budget" or bundle.hard_limit_exceeded
        for error in check.errors
    ]
    if hard_errors and not bundle.hard_limit_exceeded:
        raise ValueError(f"GDSC compiler invariant failed: {hard_errors}")
    return bundle
