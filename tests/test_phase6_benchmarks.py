from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tracegraph.phase6_benchmarks import (
    BudgetInput,
    estimate_budget,
    load_benchmark_config,
    prepare_benchmarks,
    sha256_file,
)


def _git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "fixture"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fixture(tmp_path: Path, *, duplicate_split: bool = False) -> Path:
    code_root = tmp_path / "vendor" / "fixture"
    commit = _git_repo(code_root)
    data_root = tmp_path / "data" / "fixture"
    data_root.mkdir(parents=True)
    data_file = data_root / "tasks.jsonl"
    rows = [
        {"instance_id": "task-1", "split": "train"},
        {"instance_id": "task-2", "split": "train"},
    ]
    data_file.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    selections = {
        "schema_version": "phase6_benchmark_task_splits_v1",
        "benchmark_id": "fixture",
        "splits": {
            "development": ["task-1"],
            "external_validation": ["task-1" if duplicate_split else "task-2"],
        },
    }
    selection_file = tmp_path / "selection.json"
    selection_file.write_text(json.dumps(selections), encoding="utf-8")
    config = {
        "schema_version": "phase6_benchmark_preparation_v1",
        "run_id": "fixture-v1",
        "prepared_at": "2026-08-30",
        "output_root": "output",
        "external_provider_calls_allowed": False,
        "benchmarks": [
            {
                "id": "fixture",
                "official_name": "Fixture",
                "availability": "public",
                "local_code_root": "vendor/fixture",
                "local_data_root": "data/fixture",
                "selection_file": "selection.json",
                "task_id_fields": ["instance_id"],
                "source_split_fields": ["split"],
                "default_source_split": "train",
                "expected_rows": 2,
                "expected_files": [
                    {
                        "path": "tasks.jsonl",
                        "bytes": data_file.stat().st_size,
                        "sha256": sha256_file(data_file),
                    }
                ],
                "upstream": {
                    "code_commit": commit,
                    "data_revision": "a" * 40,
                },
            }
        ],
        "budget": {
            "default_model": "qwen3.8-27b",
            "methods_per_task": 5,
            "trials_per_method": 1,
            "requests_per_trial": 1,
            "input_tokens_per_request": 1000,
            "output_tokens_per_request": 100,
            "maximum_cost_cny": 100.0,
            "prices_per_million_tokens": {
                "qwen3.8-27b": {"input": 3.0, "output": 12.0}
            },
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def test_ready_inputs_write_deterministic_hashes_and_no_requests(tmp_path: Path) -> None:
    config_path = _fixture(tmp_path)
    first = prepare_benchmarks(config_path, workspace=tmp_path, output_root=tmp_path / "out-a")
    prepare_benchmarks(config_path, workspace=tmp_path, output_root=tmp_path / "out-b")

    assert first["status"] == "ready"
    assert first["provider_requests_made"] == 0
    for filename in (
        "source_config_snapshot.json",
        "preparation_report.json",
        "budget_dry_run.json",
        "input_file_manifest.jsonl",
        "task_inventory.csv",
        "hashes.json",
        "manifest.json",
    ):
        assert (tmp_path / "out-a" / filename).read_bytes() == (
            tmp_path / "out-b" / filename
        ).read_bytes()


def test_same_task_cannot_cross_experiment_splits(tmp_path: Path) -> None:
    config_path = _fixture(tmp_path, duplicate_split=True)
    report = prepare_benchmarks(config_path, workspace=tmp_path, output_root=tmp_path / "out")
    assert report["status"] == "blocked"
    assert any("同时出现在" in error for error in report["sources"][0]["errors"])


def test_missing_local_data_has_plain_chinese_reason(tmp_path: Path) -> None:
    config_path = _fixture(tmp_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["benchmarks"][0]["local_data_root"] = "data/missing"
    payload["benchmarks"][0]["expected_files"] = []
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    report = prepare_benchmarks(config_path, workspace=tmp_path, output_root=tmp_path / "out")
    assert report["status"] == "blocked"
    assert any("本地还没有数据目录" in error for error in report["sources"][0]["errors"])


def test_changed_data_file_is_rejected(tmp_path: Path) -> None:
    config_path = _fixture(tmp_path)
    with (tmp_path / "data" / "fixture" / "tasks.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"instance_id":"task-3","split":"train"}\n')

    report = prepare_benchmarks(config_path, workspace=tmp_path, output_root=tmp_path / "out")
    assert report["status"] == "blocked"
    assert any("文件指纹不一致" in error for error in report["sources"][0]["errors"])


def test_pending_dataset_cannot_be_presented_as_official(tmp_path: Path) -> None:
    config_path = _fixture(tmp_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["benchmarks"][0]["availability"] = "pending"
    payload["benchmarks"][0]["upstream"]["data_revision"] = None
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    report = prepare_benchmarks(config_path, workspace=tmp_path, output_root=tmp_path / "out")
    assert report["status"] == "blocked"
    assert any("不能用旧 CodeQA" in error for error in report["sources"][0]["errors"])


def test_budget_is_only_a_calculation_and_honors_cap(tmp_path: Path) -> None:
    config = load_benchmark_config(_fixture(tmp_path))
    report = estimate_budget(
        config,
        BudgetInput(
            task_count=10,
            input_tokens_per_request=50_000,
            output_tokens_per_request=4_096,
        ),
    )
    assert report["request_count"] == 50
    assert report["estimated_cost_cny"] == pytest.approx(9.9576)
    assert report["status"] == "within_limit"
    assert report["provider_requests_made"] == 0


def test_budget_counts_every_request_in_a_multi_turn_task(tmp_path: Path) -> None:
    config = load_benchmark_config(_fixture(tmp_path))
    report = estimate_budget(
        config,
        BudgetInput(
            task_count=20,
            requests_per_trial=55,
            input_tokens_per_request=50_000,
            output_tokens_per_request=4_096,
        ),
    )
    assert report["request_count"] == 5_500
    assert report["estimated_cost_cny"] == pytest.approx(1_095.336)
    assert report["status"] == "over_limit"
    assert report["provider_requests_made"] == 0


def test_zero_budget_count_is_rejected_instead_of_using_the_default(tmp_path: Path) -> None:
    config = load_benchmark_config(_fixture(tmp_path))
    with pytest.raises(ValueError, match="must be positive"):
        estimate_budget(
            config,
            BudgetInput(task_count=1, requests_per_trial=0),
        )


def test_output_directory_is_append_only(tmp_path: Path) -> None:
    config_path = _fixture(tmp_path)
    output = tmp_path / "out"
    prepare_benchmarks(config_path, workspace=tmp_path, output_root=output)
    with pytest.raises(FileExistsError):
        prepare_benchmarks(config_path, workspace=tmp_path, output_root=output)
