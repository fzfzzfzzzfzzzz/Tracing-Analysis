from __future__ import annotations

from collections import Counter

from tracegraph.benchmark.compression_audit.formal_v1 import freeze_population


def _candidate(source: str, repository: str, index: int) -> dict:
    return {
        "source": source,
        "repository": repository,
        "task_id": f"{repository}-task-{index}",
        "candidate_id": f"{source}-{repository}-{index}",
        "failure_chain": {},
    }


def test_formal_population_has_frozen_quotas_unique_tasks_and_no_repo_leakage():
    rows = []
    for source in ("swe_gym", "ama_bench"):
        for repo_index in range(6):
            for task_index in range(50):
                rows.append(_candidate(source, f"{source}-repo-{repo_index}", task_index))
                # A second sampled run of the same task must not enter the population.
                duplicate = _candidate(source, f"{source}-repo-{repo_index}", task_index)
                duplicate["candidate_id"] += "-second-run"
                rows.append(duplicate)

    selected, report = freeze_population(rows, seed=20260915)

    assert len(selected) == 100
    assert len({(row["source"], row["task_id"]) for row in selected}) == 100
    assert Counter(row["source"] for row in selected) == {"swe_gym": 60, "ama_bench": 40}
    assert Counter(row["split"] for row in selected) == {"dev": 20, "validation": 20, "test": 60}
    assert report["repository_leakage"] == []
    assert report["selection_observed_annotations"] is False
    assert report["selection_observed_benchmark_method_results"] is False
    assert report["candidate_mining_used_source_trajectory_outcome"] is True


def test_formal_population_is_deterministic():
    rows = [
        _candidate(source, f"{source}-repo-{repo}", task)
        for source in ("swe_gym", "ama_bench")
        for repo in range(6)
        for task in range(50)
    ]
    first, first_report = freeze_population(rows, seed=7)
    second, second_report = freeze_population(list(reversed(rows)), seed=7)
    assert [row["candidate_id"] for row in first] == [row["candidate_id"] for row in second]
    assert first_report == second_report
