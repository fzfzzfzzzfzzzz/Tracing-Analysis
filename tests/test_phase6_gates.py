from __future__ import annotations

import tempfile
from pathlib import Path

from tracegraph.archive import ArchiveStore
from tracegraph.decision_state import stable_digest
from tracegraph.phase6_experiment import run_local_method
from tracegraph.phase6_gates import evaluate_e1_eligibility, evaluate_e2_gate
from tracegraph.phase6.scenarios import generate_trace_lifecycle_suite


def test_e1_and_e2_controlled_gates_pass_without_provider_requests() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        archive_root = root / "archive"
        suite = generate_trace_lifecycle_suite(archive_root)
        replay = generate_trace_lifecycle_suite(root / "replay")
        deterministic = stable_digest(
            [item.to_gold_dict() for item in suite]
        ) == stable_digest([item.to_gold_dict() for item in replay])
        rows = [
            run_local_method(prefix, fork, manager, archive_root=archive_root)
            for prefix in suite
            for fork in prefix.forks
            for manager in (
                "M0_full_history",
                "M3_lifecycle_eviction_only",
                "M4_lifecycle_flat_reactivation",
                "M5_lifecycle_causal_reactivation",
            )
        ]
        e1 = evaluate_e1_eligibility(
            suite,
            [row for row in rows if row["manager_id"] == "M0_full_history"],
            archive_failures=ArchiveStore(archive_root).verify_all(),
            deterministic_match=deterministic,
        )
        assert e1["decision"] == "pass", e1
        e2 = evaluate_e2_gate(rows)
        assert e2["decision"] == "pass", e2
        assert sum(int(row["provider_requests"]) for row in rows) == 0
