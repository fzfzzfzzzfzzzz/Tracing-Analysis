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
        "note": "Unresolved-failure hard retention disabled.",
    },
    "ours_without_constraint_retention": {
        "implementation_kind": "native_ablation",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "Constraint hard retention disabled.",
    },
    "full_ours": {
        "implementation_kind": "native_method",
        "main_result_eligible": True,
        "reference": "research_protocol",
        "note": "Graph-constrained lifecycle context manager.",
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
