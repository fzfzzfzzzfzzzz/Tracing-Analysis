"""Scientific provenance and publication eligibility for context managers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


_MANAGER_PROVENANCE: dict[str, dict[str, Any]] = {
    "full_trajectory": {
        "implementation_kind": "native_baseline",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "No context reduction; actual live reward is valid only when this condition ran.",
    },
    "last_k": {
        "implementation_kind": "native_baseline",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "Deterministic last-k nodes baseline.",
    },
    "token_length_pruning": {
        "implementation_kind": "native_baseline",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "Deterministic newest-until-token-budget baseline.",
    },
    "summary_only": {
        "implementation_kind": "deterministic_proxy",
        "main_result_eligible": False,
        "reference": "research_protocol",
        "note": "Truncation proxy; replace with a frozen live summarizer for main results.",
    },
    "llm_only_pruning": {
        "implementation_kind": "deterministic_proxy",
        "main_result_eligible": False,
        "reference": "research_protocol",
        "note": "Heuristic scorer proxy; main results require a frozen LLM scorer.",
    },
    "agentdiet_style": {
        "implementation_kind": "deterministic_proxy",
        "main_result_eligible": False,
        "reference": "https://arxiv.org/abs/2509.23586",
        "official_code": None,
        "note": "Style proxy only; no official AgentDiet repository was identified in the audit.",
    },
    "acon_style": {
        "implementation_kind": "deterministic_proxy",
        "main_result_eligible": False,
        "reference": "https://arxiv.org/abs/2510.00615",
        "official_code": "https://github.com/microsoft/acon",
        "note": (
            "Style proxy only; official ACON needs observation/history optimizer hooks, "
            "a frozen compressor model, guideline config, prompts, and cost accounting."
        ),
    },
    "acon_official": {
        "implementation_kind": "external_official_adapter",
        "main_result_eligible": False,
        "runtime_eligibility_required": True,
        "reference": "https://arxiv.org/abs/2510.00615",
        "official_code": "https://github.com/microsoft/acon",
        "source_snapshot_sha": "d63f9ae18959dc7215ff62899c94c5e8c56847ae",
        "note": (
            "Live-only adapter for hash-verified official ObservationOptimizer and "
            "HistoryOptimizer classes. A run becomes eligible only when runtime provenance "
            "is verified, provider usage is recorded, and no fallback occurs."
        ),
    },
    "acon_official_with_failure_cards": {
        "implementation_kind": "external_official_adapter_plus_native_method",
        "main_result_eligible": False,
        "runtime_eligibility_required": True,
        "reference": "phase3_p4_research_protocol",
        "official_code": "https://github.com/microsoft/acon",
        "source_snapshot_sha": "d63f9ae18959dc7215ff62899c94c5e8c56847ae",
        "note": (
            "P4 live-only combination: the hash-verified official ACON context "
            "plan is held fixed and bounded native Failure Cards are added as "
            "independent context fragments. Runtime ACON provenance and usage "
            "requirements still apply."
        ),
    },
    "ours_without_graph_edges": {
        "implementation_kind": "native_ablation",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "Graph-edge signal disabled.",
    },
    "ours_without_lifecycle_states": {
        "implementation_kind": "native_ablation",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "Lifecycle inference disabled.",
    },
    "ours_without_failure_retention": {
        "implementation_kind": "native_ablation",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "Failure Card retention disabled; raw traces remain archived.",
    },
    "ours_without_constraint_retention": {
        "implementation_kind": "native_ablation",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "Constraint hard retention disabled.",
    },
    "raw_hard_failure_retention": {
        "implementation_kind": "native_phase2_baseline",
        "main_result_eligible": True,
        "reference": "phase2_research_protocol",
        "note": (
            "Frozen phase-two behavior: unresolved negative evidence, active records, "
            "and audit-required calls can enter unbounded mandatory raw context."
        ),
    },
    "full_ours": {
        "implementation_kind": "native_method",
        "main_result_eligible": True,
        "reference": "phase3_research_protocol",
        "note": (
            "Scoped compact failure-card manager with expiry rules, archive/context "
            "separation, and a bounded negative-evidence budget."
        ),
    },
    "decision_state_compiler": {
        "implementation_kind": "native_gdsc_method",
        "main_result_eligible": True,
        "runtime_eligibility_required": True,
        "reference": "gdsc_v2_research_protocol",
        "context_policy_version": "gdsc_core_v1",
        "note": (
            "Graph-constrained, provenance-preserving decision-state compiler. "
            "Main-result eligibility requires serialized-cost, construct, safety, "
            "and stage-gate evidence from the same frozen runtime."
        ),
    },
    "lifecycle_graph_context": {
        "implementation_kind": "native_lifecycle_graph_context",
        "main_result_eligible": False,
        "runtime_eligibility_required": True,
        "reference": "phase5_liveness_v1",
        "context_policy_version": "gdsc_prune_v1",
        "structured_policy_version": "gdsc_structured_v1",
        "implementation_status": "f5_g1_no_go",
        "note": (
            "Phase 5 identity for LiveSubgraph and deletion-only GDSC-Prune. "
            "F5-G1 failed the frozen aggregate serialized-cost criterion, so "
            "GDSC-Structured and all external pilots remain blocked. "
            "Main-result eligibility is false."
        ),
    },
    "acon_official_with_gdsc_state": {
        "implementation_kind": "external_official_adapter_plus_gdsc_state_layer",
        "main_result_eligible": False,
        "runtime_eligibility_required": True,
        "reference": "gdsc_v2_research_protocol",
        "official_code": "https://github.com/microsoft/acon",
        "source_snapshot_sha": "d63f9ae18959dc7215ff62899c94c5e8c56847ae",
        "context_policy_version": "gdsc_core_v1",
        "note": (
            "Hash-pinned official ACON context plan with the native GDSC verified "
            "state/safety layer. Eligibility requires both ACON and GDSC runtime gates."
        ),
    },
}


def manager_provenance(names: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Return a defensive copy and fail closed for unregistered managers."""

    result: dict[str, dict[str, Any]] = {}
    for name in names:
        if name not in _MANAGER_PROVENANCE:
            raise ValueError(f"missing scientific provenance for manager: {name}")
        result[name] = deepcopy(_MANAGER_PROVENANCE[name])
    return result
