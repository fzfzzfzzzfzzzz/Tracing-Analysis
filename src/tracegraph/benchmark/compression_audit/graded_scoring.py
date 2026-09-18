"""Legacy audit grading and the multi-score failure-memory scorecard.

The binary audit gate remains the definition of complete recovery.  This module
keeps a judge-free legacy diagnostic and adds an explicitly disaggregated
scorecard whose semantic component is supplied only by a frozen judge verdict.
"""

from __future__ import annotations

import math
from typing import Any


GRADED_AUDIT_REVISION = "failure-memory-retention-graded-v1"
GRADED_AUDIT_WEIGHTS = {
    "strict_fact_recovery": 0.35,
    "evidence_grounding": 0.30,
    "causal_reconstruction": 0.20,
    "scope_consistency": 0.10,
    "safety": 0.05,
}

SCORECARD_REVISION = "failure-memory-scorecard-v1"
FAILURE_MEMORY_WEIGHTS = {
    "fact_retention": 0.40,
    "semantic_causal": 0.30,
    "scope": 0.10,
    "safety": 0.10,
    "provenance": 0.10,
}
_JUDGE_LABELS = {"supported", "missing", "contradicted", "uncertain"}


def evidence_f1(precision: float, recall: float, *, applicable: bool = True) -> float:
    """Return citation F1, treating a genuinely inapplicable criterion as satisfied."""

    if not applicable:
        return 1.0
    precision = _unit_interval("evidence_precision", precision)
    recall = _unit_interval("evidence_recall", recall)
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


def graded_audit_result(
    *,
    protocol_valid: bool,
    strict_fact_recovery: float,
    evidence_grounding: float,
    causal_reconstruction: float,
    scope_consistency: float,
    safety: float,
) -> dict[str, Any]:
    """Build the frozen deterministic graded score and its auditable components.

    Invalid answer envelopes receive zero because their contents cannot be scored
    reliably.  Semantic-judge output is deliberately absent from this function.
    """

    components = {
        "strict_fact_recovery": _unit_interval(
            "strict_fact_recovery", strict_fact_recovery
        ),
        "evidence_grounding": _unit_interval("evidence_grounding", evidence_grounding),
        "causal_reconstruction": _unit_interval(
            "causal_reconstruction", causal_reconstruction
        ),
        "scope_consistency": _unit_interval("scope_consistency", scope_consistency),
        "safety": _unit_interval("safety", safety),
    }
    weighted = sum(
        GRADED_AUDIT_WEIGHTS[name] * value for name, value in components.items()
    )
    return {
        "graded_audit_revision": GRADED_AUDIT_REVISION,
        "graded_audit_score": round(100.0 * weighted, 6) if protocol_valid else 0.0,
        "graded_audit_components": components,
        "graded_audit_weights": dict(GRADED_AUDIT_WEIGHTS),
        "graded_audit_protocol_valid": bool(protocol_valid),
    }


def semantic_support_score(facts: Any, contradictions: Any = ()) -> float | None:
    """Score frozen judge labels without treating missing review as method failure.

    Supported facts add one, contradicted facts subtract one, and missing or
    uncertain facts add nothing.  The result is clipped to [0, 1].  ``None``
    means that no valid semantic verdict was available.
    """

    if not isinstance(facts, dict) or not facts:
        return None
    labels = tuple(facts.values())
    if any(label not in _JUDGE_LABELS for label in labels):
        return None
    if not isinstance(contradictions, (list, tuple)):
        return None
    numerator = sum(label == "supported" for label in labels)
    numerator -= sum(label == "contradicted" for label in labels)
    numerator -= len(contradictions)
    return max(0.0, numerator / len(labels))


def failure_memory_scorecard(
    *,
    protocol_valid: bool,
    fact_retention: float | None,
    semantic_causal: float | None,
    scope: float | None,
    safety: float | None,
    provenance: float | None,
    semantic_required: bool = True,
) -> dict[str, Any]:
    """Build the v1 multi-score failure-memory card and convenience total.

    Components that are genuinely inapplicable are omitted and the remaining
    weights are renormalized.  A malformed method answer receives zero on every
    applicable component.  A valid answer with no calibrated semantic verdict
    keeps a ``None`` semantic component and has no aggregate score; this is a
    scoring/integration gap, not evidence that the method has zero semantics.
    """

    raw = {
        "fact_retention": fact_retention,
        "semantic_causal": semantic_causal,
        "scope": scope,
        "safety": safety,
        "provenance": provenance,
    }
    applicable = {name: value is not None for name, value in raw.items()}
    applicable["semantic_causal"] = bool(semantic_required)
    components = {
        name: (_unit_interval(name, float(value)) if value is not None else None)
        for name, value in raw.items()
    }
    if not protocol_valid:
        components = {
            name: (0.0 if applicable[name] else None) for name in components
        }

    semantic_required_but_missing = (
        protocol_valid and semantic_required and semantic_causal is None
    )
    present = {
        name: value for name, value in components.items() if value is not None
    }
    weight_total = sum(FAILURE_MEMORY_WEIGHTS[name] for name in present)
    aggregate = None
    if present and not semantic_required_but_missing:
        aggregate = 100.0 * sum(
            FAILURE_MEMORY_WEIGHTS[name] * value for name, value in present.items()
        ) / weight_total

    return {
        "scorecard_revision": SCORECARD_REVISION,
        "fact_retention_score": _percent(components["fact_retention"]),
        "semantic_causal_score": _percent(components["semantic_causal"]),
        "scope_score": _percent(components["scope"]),
        "safety_score": _percent(components["safety"]),
        "provenance_score": _percent(components["provenance"]),
        "overall_failure_memory_score": (
            round(aggregate, 6) if aggregate is not None else None
        ),
        "failure_memory_components": components,
        "failure_memory_weights": dict(FAILURE_MEMORY_WEIGHTS),
        "failure_memory_applicable": applicable,
        "failure_memory_score_valid": aggregate is not None,
    }


def _percent(value: float | None) -> float | None:
    return round(100.0 * value, 6) if value is not None else None


def _unit_interval(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be finite and in [0, 1]")
    return number
