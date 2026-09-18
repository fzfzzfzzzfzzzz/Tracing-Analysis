"""Freeze a content-blind AppWorld development canary from official dev IDs."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE_ROOT = ROOT / "data" / "external_benchmark_freezes" / "appworld_260918"
SOURCE = FREEZE_ROOT / "dev_source_ids.txt"
SOURCE_SHA256 = "9fa976589300ea8905708257144d801d1604b06d85fb0181e381df8a3ba85001"
SEED = 20260918
COUNT = 5
APPWORLD_COMMIT = "42b5bcf3cd334fee33f0c37c02070a9f5807add5"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def main() -> None:
    if sha256(SOURCE) != SOURCE_SHA256:
        raise ValueError("AppWorld dev ID list hash changed")
    task_ids = [line.strip() for line in SOURCE.read_text(encoding="utf-8").splitlines()]
    if not task_ids or any(not task_id for task_id in task_ids):
        raise ValueError("AppWorld dev ID list is empty or malformed")
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("AppWorld dev ID list contains duplicates")

    shuffled = list(task_ids)
    random.Random(SEED).shuffle(shuffled)
    selected = []
    selected_families = set()
    for task_id in shuffled:
        family = task_id.rsplit("_", 1)[0]
        if family in selected_families:
            continue
        selected.append(task_id)
        selected_families.add(family)
        if len(selected) == COUNT:
            break
    if len(selected) != COUNT:
        raise ValueError("not enough distinct AppWorld task families")

    canary_rows = [
        {
            "task_id": task_id,
            "task_family": task_id.rsplit("_", 1)[0],
            "split": "dev",
            "purpose": "external_harness_canary",
            "selection_seed": SEED,
        }
        for task_id in selected
    ]
    remaining_rows = [
        {"task_id": task_id, "split": "dev", "purpose": "unexposed_dev_pool"}
        for task_id in task_ids
        if task_id not in set(selected)
    ]
    canary_path = FREEZE_ROOT / "canary_task_ids.jsonl"
    remaining_path = FREEZE_ROOT / "dev_remaining_task_ids.jsonl"
    write_jsonl(canary_path, canary_rows)
    write_jsonl(remaining_path, remaining_rows)
    manifest = {
        "schema_version": "appworld_external_freeze_v1",
        "benchmark": "AppWorld",
        "source_commit": APPWORLD_COMMIT,
        "created_on": "2026-09-18",
        "selection_seed": SEED,
        "selection_used_task_content": False,
        "counts": {
            "dev_total": len(task_ids),
            "canary": len(canary_rows),
            "dev_remaining": len(remaining_rows),
        },
        "files": {
            "dev_source_ids.txt": SOURCE_SHA256,
            "canary_task_ids.jsonl": sha256(canary_path),
            "dev_remaining_task_ids.jsonl": sha256(remaining_path),
        },
        "unread_test_split_hashes": {
            "train.txt": "93d9fe71e7a2e3b7529803d4a20b604f4ebf5ae806f321081140238068189d37",
            "test_normal.txt": "c3af41497b6f2f0860a2ff8c09b335dca527e2cf48e59b4aabdb301b6b68db8f",
            "test_challenge.txt": "3c32b481042ac97f7d3477d53f5d196245c885c438d652944edc8a9a28e0f028",
        },
        "rules": [
            "canary tasks are development-only after the first model request",
            "task content and ground truth were not used for selection",
            "test_normal and test_challenge IDs/content remain unopened locally",
        ],
    }
    (FREEZE_ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
