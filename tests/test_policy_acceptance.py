from __future__ import annotations

import pytest

from tracegraph.evaluation.policy_acceptance import (
    evaluate_policy_cases,
    verify_policy_v4_acceptance,
)


def test_deterministic_policy_v4_acceptance_gates() -> None:
    report = verify_policy_v4_acceptance()
    assert report["provider_requests"] == 0
    assert report["development_only"] is True
    assert report["independent_validation"] is False
    assert report["safe_compression_ratio_median"] >= 0.20
    assert report["causal_retrieval_completeness"] >= 0.95
    assert report["unrelated_question_retrieval_rate"] == 0.0
    assert report["single_aggregate_score"] is None


def test_acceptance_evaluator_requires_at_least_one_case() -> None:
    with pytest.raises(ValueError, match="at least one"):
        evaluate_policy_cases(())
