from __future__ import annotations

from tracegraph.phase6_metrics import (
    exact_paired_sign_pvalue,
    holm_adjust,
    paired_cluster_bootstrap,
)


def test_bootstrap_is_prefix_clustered_and_deterministic() -> None:
    rows = []
    for prefix_id, left, right in (("p1", 1.0, 0.0), ("p2", 0.5, 0.0)):
        rows.extend(
            [
                {"prefix_id": prefix_id, "manager_id": "left", "fork_type": "R", "x": left},
                {"prefix_id": prefix_id, "manager_id": "right", "fork_type": "R", "x": right},
            ]
        )
    first = paired_cluster_bootstrap(
        rows, "left", "right", "x", fork_type="R", samples=100, seed=7
    )
    second = paired_cluster_bootstrap(
        rows, "left", "right", "x", fork_type="R", samples=100, seed=7
    )
    assert first == second
    assert first["clusters"] == 2
    assert first["paired_mean_difference"] == 0.75


def test_sign_test_and_holm_are_bounded() -> None:
    assert exact_paired_sign_pvalue([1, 1, 1, 1]) == 0.125
    adjusted = holm_adjust({"a": 0.01, "b": 0.04, "c": 0.5})
    assert 0 <= adjusted["a"] <= adjusted["b"] <= adjusted["c"] <= 1
