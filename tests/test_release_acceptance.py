from __future__ import annotations

import json
import os
from pathlib import Path

from tracegraph.benchmark.compression_audit.dataset import (
    build_benchmark,
    validate_benchmark,
)
from tracegraph.benchmark.compression_audit.metrics import score_run
from tracegraph.benchmark.compression_audit.runtime import run_deterministic


ROOT = Path(__file__).resolve().parents[1]
FROZEN_ROOT = Path(os.environ.get("TRACEGRAPH_FROZEN_ROOT", ROOT))
CONFIG = ROOT / "configs" / "compression_audit_v1.json"


def test_fresh_development_benchmark_is_reproducible_and_provider_free(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    run = tmp_path / "run"
    score = tmp_path / "score"
    built = build_benchmark(
        CONFIG,
        dataset,
        workspace=ROOT,
        source_workspace=FROZEN_ROOT,
    )
    assert built["development_only"] is True
    assert built["independent_validation"] is False
    assert built["external_provider_calls"] == 0
    validation = validate_benchmark(dataset)
    assert validation["diagnostic_ready"] is True
    assert validation["v1_ready"] is False

    run_manifest = run_deterministic(
        dataset,
        run,
        methods=("M5_lifecycle_causal_reactivation",),
        query_types=("distractor_current",),
        config_path=CONFIG,
    )
    assert run_manifest["provider_requests"] == 0
    assert run_manifest["development_only"] is True
    result = score_run(dataset, run, score, bootstrap_samples=10)
    assert result["report"]["development_only"] is True
    assert result["report"]["independent_validation"] is False
    assert result["report"]["single_aggregate_score"] == "overall_failure_memory_score"
    assert result["report"]["aggregate_score_requires_component_reporting"] is True
    assert result["gates"]["development_only"] is True
    assert json.loads((run / "manifest.json").read_text(encoding="utf-8"))[
        "provider_requests"
    ] == 0
