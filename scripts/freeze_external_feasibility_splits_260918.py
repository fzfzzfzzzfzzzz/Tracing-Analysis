"""Freeze content-blind development feasibility splits for external benchmarks.

The source pools were frozen before this script.  Selection uses identifiers and
stratum labels only; no task text, trajectory, answer, or ground truth is read.
Selected items become development-only as soon as a model request is made and
are removed from the remaining confirmation candidates recorded here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SEED = 20260918
DEFAULT_OUTPUT = (
    ROOT / "data" / "external_benchmark_freezes" / "feasibility_260918"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def rank(benchmark: str, item_id: str) -> str:
    return hashlib.sha256(f"{SEED}:{benchmark}:{item_id}".encode()).hexdigest()


def validate_frozen_file(directory: Path, filename: str) -> Path:
    manifest = load_json(directory / "manifest.json")
    path = directory / filename
    expected = str(manifest["files"][filename])
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"frozen source hash mismatch for {path}: {actual}")
    return path


def with_status(
    rows: Iterable[dict[str, Any]], *, benchmark: str, item_key: str, status: str
) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        item_id = str(row[item_key])
        identity = (
            f"{row['domain']}:{item_id}" if benchmark == "tau3" else item_id
        )
        output.append(
            {
                **row,
                "benchmark": benchmark,
                "selection_seed": SEED,
                "selection_rank": rank(benchmark, identity),
                "status": status,
            }
        )
    return output


def freeze_tau3(root: Path) -> dict[str, Any]:
    source_dir = root / "tau3_260917"
    source = validate_frozen_file(source_dir, "confirmatory_pool_task_ids.jsonl")
    rows = load_jsonl(source)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["domain"])].append(row)
    if {key: len(value) for key, value in groups.items()} != {
        "airline": 38,
        "retail": 101,
    }:
        raise ValueError("unexpected tau3 source strata")
    selected = []
    for domain in ("airline", "retail"):
        selected.extend(
            sorted(
                groups[domain],
                key=lambda row: rank(
                    "tau3", f"{domain}:{str(row['task_id'])}"
                ),
            )[:10]
        )
    selected_ids = {
        (str(row["domain"]), str(row["task_id"])) for row in selected
    }
    remaining = [
        row
        for row in rows
        if (str(row["domain"]), str(row["task_id"])) not in selected_ids
    ]
    if len(selected_ids) != 20 or len(remaining) != 119:
        raise AssertionError("tau3 compound-key partition is invalid")
    return {
        "source": source,
        "item_key": "task_id",
        "selected": with_status(
            selected,
            benchmark="tau3",
            item_key="task_id",
            status="development_feasibility",
        ),
        "remaining": with_status(
            remaining,
            benchmark="tau3",
            item_key="task_id",
            status="untouched_confirmation_candidate",
        ),
        "allocation": {"airline": 10, "retail": 10},
    }


def proportional_minimum_one_allocation(
    sizes: dict[str, int], total: int
) -> dict[str, int]:
    if total < len(sizes):
        raise ValueError("sample is too small to cover every stratum")
    allocation = {key: 1 for key in sizes}
    targets = {key: total * value / sum(sizes.values()) for key, value in sizes.items()}
    while sum(allocation.values()) < total:
        candidates = [key for key in sizes if allocation[key] < sizes[key]]
        selected = max(
            candidates,
            key=lambda key: (targets[key] - allocation[key], sizes[key], key),
        )
        allocation[selected] += 1
    return allocation


def freeze_ama(root: Path) -> dict[str, Any]:
    source_dir = root / "ama_bench_260917"
    source = validate_frozen_file(
        source_dir, "confirmatory_pool_episode_ids.jsonl"
    )
    rows = load_jsonl(source)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["task_type"])].append(row)
    allocation = proportional_minimum_one_allocation(
        {key: len(value) for key, value in groups.items()}, 20
    )
    selected = []
    for task_type, count in sorted(allocation.items()):
        selected.extend(
            sorted(
                groups[task_type],
                key=lambda row: rank("ama_bench", str(row["episode_id"])),
            )[:count]
        )
    selected_ids = {str(row["episode_id"]) for row in selected}
    remaining = [row for row in rows if str(row["episode_id"]) not in selected_ids]
    return {
        "source": source,
        "item_key": "episode_id",
        "selected": with_status(
            selected,
            benchmark="ama_bench",
            item_key="episode_id",
            status="development_feasibility",
        ),
        "remaining": with_status(
            remaining,
            benchmark="ama_bench",
            item_key="episode_id",
            status="untouched_confirmation_candidate",
        ),
        "allocation": allocation,
    }


def appworld_family(task_id: str) -> str:
    return task_id.rsplit("_", 1)[0]


def freeze_appworld(root: Path) -> dict[str, Any]:
    source_dir = root / "appworld_260918"
    source = validate_frozen_file(source_dir, "dev_remaining_task_ids.jsonl")
    canary_source = validate_frozen_file(source_dir, "canary_task_ids.jsonl")
    rows = load_jsonl(source)
    canary_families = {
        appworld_family(str(row["task_id"])) for row in load_jsonl(canary_source)
    }
    family_excluded = [
        row
        for row in rows
        if appworld_family(str(row["task_id"])) in canary_families
    ]
    eligible = [
        row
        for row in rows
        if appworld_family(str(row["task_id"])) not in canary_families
    ]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        groups[appworld_family(str(row["task_id"]))].append(row)
    if len(groups) != 14 or len(eligible) != 42 or len(family_excluded) != 10:
        raise ValueError("unexpected AppWorld family structure")

    selected = []
    for family, family_rows in sorted(groups.items()):
        selected.append(
            min(
                family_rows,
                key=lambda row: rank("appworld", str(row["task_id"])),
            )
        )
    selected_ids = {str(row["task_id"]) for row in selected}
    second_candidates = [
        row for row in eligible if str(row["task_id"]) not in selected_ids
    ]
    selected.extend(
        sorted(
            second_candidates,
            key=lambda row: rank("appworld_second", str(row["task_id"])),
        )[: 20 - len(selected)]
    )
    selected_ids = {str(row["task_id"]) for row in selected}
    if len(selected_ids) != 20:
        raise AssertionError("AppWorld selection is not 20 unique tasks")
    selected_family_counts: dict[str, int] = defaultdict(int)
    for row in selected:
        selected_family_counts[appworld_family(str(row["task_id"]))] += 1
    if max(selected_family_counts.values()) > 2:
        raise AssertionError("AppWorld selected more than two tasks from one family")
    remaining = [row for row in eligible if str(row["task_id"]) not in selected_ids]
    return {
        "source": source,
        "item_key": "task_id",
        "selected": with_status(
            selected,
            benchmark="appworld",
            item_key="task_id",
            status="development_feasibility",
        ),
        "remaining": with_status(
            remaining,
            benchmark="appworld",
            item_key="task_id",
            status="untouched_development_candidate",
        ),
        "family_excluded": with_status(
            family_excluded,
            benchmark="appworld",
            item_key="task_id",
            status="excluded_canary_family",
        ),
        "allocation": dict(sorted(selected_family_counts.items())),
        "canary_families": sorted(canary_families),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to replace frozen split: {output}")
    output.mkdir(parents=True)
    root = ROOT / "data" / "external_benchmark_freezes"
    results = {
        "tau3": freeze_tau3(root),
        "ama_bench": freeze_ama(root),
        "appworld": freeze_appworld(root),
    }

    manifest_benchmarks = {}
    for benchmark, result in results.items():
        benchmark_dir = output / benchmark
        benchmark_dir.mkdir()
        selected_path = benchmark_dir / "development_feasibility_ids.jsonl"
        remaining_path = benchmark_dir / "remaining_ids.jsonl"
        write_jsonl(selected_path, result["selected"])
        write_jsonl(remaining_path, result["remaining"])
        files = {
            selected_path.name: sha256_file(selected_path),
            remaining_path.name: sha256_file(remaining_path),
        }
        counts = {
            "development_feasibility": len(result["selected"]),
            "remaining": len(result["remaining"]),
        }
        if result.get("family_excluded") is not None:
            excluded_path = benchmark_dir / "excluded_canary_family_ids.jsonl"
            write_jsonl(excluded_path, result["family_excluded"])
            files[excluded_path.name] = sha256_file(excluded_path)
            counts["excluded_canary_family"] = len(result["family_excluded"])
        manifest_benchmarks[benchmark] = {
            "source_file": str(result["source"].relative_to(ROOT)).replace("\\", "/"),
            "source_sha256": sha256_file(result["source"]),
            "selection_used_task_content": False,
            "allocation": result["allocation"],
            "counts": counts,
            "files": files,
        }
        if result.get("canary_families") is not None:
            manifest_benchmarks[benchmark]["excluded_canary_families"] = result[
                "canary_families"
            ]

    manifest = {
        "schema_version": "external_development_feasibility_freeze_v1",
        "created_on": "2026-09-18",
        "selection_seed": SEED,
        "development_only": True,
        "selection_used_task_content": False,
        "rules": [
            "selected IDs are excluded from later confirmation results",
            "no task text, trajectory, answer, or ground truth was read for selection",
            "AppWorld tasks sharing a family with a canary are excluded",
            "confirmation and AppWorld test splits remain unopened",
        ],
        "benchmarks": manifest_benchmarks,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
