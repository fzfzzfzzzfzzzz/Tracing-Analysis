"""Freeze an AMA-Bench external canary and an untouched confirmatory pool."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SEED = 20260917
EXPECTED_SOURCE_SHA256 = "45c36052e1520d87ad9de4114f71c9df42d4aac9cf158c0c353e800b653d65ff"
CANARY_TASK_TYPES = ("alfworld", "gaia_level2", "spider2", "webarena", "swebench")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def rank(episode_id: str) -> str:
    return hashlib.sha256(f"{SEED}:{episode_id}".encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(
            "data/external/compression_audit_sources_v1/"
            "ama_bench_a577737/open_end_qa_set.jsonl"
        ),
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("data/compression_audit/real_annotations_v1/candidates.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/external_benchmark_freezes/ama_bench_260917"),
    )
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(f"refusing to replace frozen split: {args.output}")
    source_sha256 = file_sha256(args.source)
    if source_sha256 != EXPECTED_SOURCE_SHA256:
        raise ValueError(f"AMA source hash mismatch: {source_sha256}")

    source_rows = load_jsonl(args.source)
    selected_rows = load_jsonl(args.selection)
    by_id = {str(row["episode_id"]): row for row in source_rows}
    if len(source_rows) != 208 or len(by_id) != 208:
        raise ValueError("expected 208 unique AMA-Bench episodes")

    exposed_rows = [row for row in selected_rows if row.get("source") == "ama_bench"]
    exposed_ids = {str(row["task_id"]) for row in exposed_rows}
    if len(exposed_rows) != 40 or len(exposed_ids) != 40:
        raise ValueError("expected 40 exposed AMA-Bench episodes")
    if not exposed_ids <= set(by_id):
        raise ValueError("selection contains unknown AMA-Bench episode IDs")

    eligible = [row for key, row in by_id.items() if key not in exposed_ids]
    canary_rows = []
    for task_type in CANARY_TASK_TYPES:
        candidates = [row for row in eligible if str(row.get("task_type")) == task_type]
        if not candidates:
            raise ValueError(f"no untouched AMA episode for task type: {task_type}")
        canary_rows.append(min(candidates, key=lambda row: rank(str(row["episode_id"]))))
    canary_ids = {str(row["episode_id"]) for row in canary_rows}
    confirmatory_rows = [row for row in eligible if str(row["episode_id"]) not in canary_ids]
    if len(canary_rows) != 5 or len(confirmatory_rows) != 163:
        raise AssertionError("AMA split counts changed unexpectedly")

    args.output.mkdir(parents=True)
    exposed_path = args.output / "exposed_episode_ids.jsonl"
    canary_path = args.output / "canary_episode_ids.jsonl"
    confirmatory_path = args.output / "confirmatory_pool_episode_ids.jsonl"

    write_jsonl(
        exposed_path,
        (
            {
                "episode_id": str(row["task_id"]),
                "candidate_id": str(row["candidate_id"]),
                "task_type": str(by_id[str(row["task_id"])].get("task_type")),
                "compression_audit_split": str(row["split"]),
                "exposure_reason": "used_by_compression_audit_v1",
            }
            for row in sorted(exposed_rows, key=lambda item: str(item["task_id"]))
        ),
    )
    write_jsonl(
        canary_path,
        (
            {
                "episode_id": str(row["episode_id"]),
                "task_type": str(row.get("task_type")),
                "selection_seed": SEED,
                "purpose": "external_harness_canary",
            }
            for row in sorted(canary_rows, key=lambda item: str(item["episode_id"]))
        ),
    )
    write_jsonl(
        confirmatory_path,
        (
            {
                "episode_id": str(row["episode_id"]),
                "task_type": str(row.get("task_type")),
                "status": "untouched_confirmatory_pool",
            }
            for row in sorted(confirmatory_rows, key=lambda item: str(item["episode_id"]))
        ),
    )

    manifest = {
        "schema_version": "ama_bench_external_freeze_v1",
        "benchmark": "AMA-Bench",
        "created_on": "2026-09-17",
        "source_revision": "a5777378066f53229a94557a7b192435cd027909",
        "source_sha256": source_sha256,
        "selection_seed": SEED,
        "counts": {
            "source_total": len(source_rows),
            "exposed": len(exposed_rows),
            "canary": len(canary_rows),
            "confirmatory_pool": len(confirmatory_rows),
        },
        "canary_task_types": list(CANARY_TASK_TYPES),
        "files": {
            exposed_path.name: file_sha256(exposed_path),
            canary_path.name: file_sha256(canary_path),
            confirmatory_path.name: file_sha256(confirmatory_path),
        },
        "rules": [
            "exposed episodes are excluded from external confirmatory results",
            "canary episodes are development-only after the first model request",
            "confirmatory_pool must remain untouched until method configuration is frozen",
        ],
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
