"""Freeze untouched tau3 canary tasks without reading task semantics."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


SEED = 20260917
DOMAIN_CANARY_COUNTS = {"retail": 3, "airline": 2}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def rank(domain: str, task_id: str) -> str:
    return hashlib.sha256(f"{SEED}:{domain}:{task_id}".encode()).hexdigest()


def main() -> None:
    repo = Path("vendor/tau3-bench")
    simulations = repo / "data/simulations"
    output = Path("data/external_benchmark_freezes/tau3_260917")
    if output.exists():
        raise FileExistsError(f"refusing to replace frozen split: {output}")

    exposed: dict[str, set[str]] = {domain: set() for domain in DOMAIN_CANARY_COUNTS}
    result_files = sorted(simulations.glob("*/results.json"))
    for path in result_files:
        result = json.loads(path.read_text(encoding="utf-8"))
        domain = str(
            result.get("info", {}).get("environment_info", {}).get("domain_name", "")
        )
        if domain not in exposed:
            continue
        exposed[domain].update(str(row["task_id"]) for row in result.get("simulations", []))

    task_files = {
        domain: repo / f"data/tau2/domains/{domain}/tasks.json"
        for domain in DOMAIN_CANARY_COUNTS
    }
    tasks = {
        domain: json.loads(path.read_text(encoding="utf-8"))
        for domain, path in task_files.items()
    }
    all_ids = {
        domain: {str(row["id"]) for row in rows}
        for domain, rows in tasks.items()
    }
    if any(not exposed[domain] <= all_ids[domain] for domain in exposed):
        raise ValueError("historical results reference unknown task IDs")

    canary: dict[str, list[str]] = {}
    confirmatory: dict[str, list[str]] = {}
    for domain, count in DOMAIN_CANARY_COUNTS.items():
        eligible = sorted(
            all_ids[domain] - exposed[domain], key=lambda task_id: rank(domain, task_id)
        )
        if len(eligible) < count:
            raise ValueError(f"not enough untouched tasks for {domain}")
        canary[domain] = eligible[:count]
        confirmatory[domain] = sorted(eligible[count:], key=lambda value: (len(value), value))

    output.mkdir(parents=True)
    exposed_path = output / "exposed_task_ids.jsonl"
    canary_path = output / "canary_task_ids.jsonl"
    confirmatory_path = output / "confirmatory_pool_task_ids.jsonl"
    write_jsonl(
        exposed_path,
        (
            {"domain": domain, "task_id": task_id, "exposure_reason": "prior_project_run"}
            for domain in sorted(exposed)
            for task_id in sorted(exposed[domain], key=lambda value: (len(value), value))
        ),
    )
    write_jsonl(
        canary_path,
        (
            {
                "domain": domain,
                "task_id": task_id,
                "selection_seed": SEED,
                "purpose": "external_harness_canary",
            }
            for domain in sorted(canary)
            for task_id in canary[domain]
        ),
    )
    write_jsonl(
        confirmatory_path,
        (
            {"domain": domain, "task_id": task_id, "status": "untouched_confirmatory_pool"}
            for domain in sorted(confirmatory)
            for task_id in confirmatory[domain]
        ),
    )

    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    manifest = {
        "schema_version": "tau3_external_freeze_v1",
        "benchmark": "tau3",
        "created_on": "2026-09-17",
        "source_commit": commit,
        "selection_seed": SEED,
        "historical_result_file_count": len(result_files),
        "counts_by_domain": {
            domain: {
                "source_total": len(all_ids[domain]),
                "exposed": len(exposed[domain]),
                "canary": len(canary[domain]),
                "confirmatory_pool": len(confirmatory[domain]),
            }
            for domain in sorted(DOMAIN_CANARY_COUNTS)
        },
        "task_file_sha256": {
            domain: file_sha256(path) for domain, path in task_files.items()
        },
        "files": {
            exposed_path.name: file_sha256(exposed_path),
            canary_path.name: file_sha256(canary_path),
            confirmatory_path.name: file_sha256(confirmatory_path),
        },
        "rules": [
            "historically executed tasks are excluded from the new confirmatory pool",
            "canary tasks are development-only after the first model request",
            "task content was not used for canary selection",
            "confirmatory_pool must remain untouched until method configuration is frozen",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
