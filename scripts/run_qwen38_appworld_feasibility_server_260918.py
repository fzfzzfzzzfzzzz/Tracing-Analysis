#!/usr/bin/env python3
"""Run one frozen AppWorld development-feasibility task with the canary harness."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import run_qwen38_appworld_canary_server_260918 as canary


FREEZE = Path(
    "/data/fangc/external_benchmark_freezes/feasibility_260918/"
    "appworld/development_feasibility_ids.jsonl"
)
EXPECTED_FREEZE_SHA256 = (
    "a2cd54fb447494386ef683a3c72ecc38ef216b23bfaffa6db007555f518a9c61"
)


def load_ids() -> set[str]:
    if hashlib.sha256(FREEZE.read_bytes()).hexdigest() != EXPECTED_FREEZE_SHA256:
        raise RuntimeError("AppWorld feasibility freeze hash mismatch")
    rows = [
        json.loads(line)
        for line in FREEZE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = {
        str(row["task_id"])
        for row in rows
        if row.get("benchmark") == "appworld"
        and row.get("status") == "development_feasibility"
        and row.get("split") == "dev"
    }
    if len(ids) != 20:
        raise RuntimeError(f"expected 20 AppWorld feasibility IDs, got {len(ids)}")
    return ids


if __name__ == "__main__":
    canary.CANARY_IDS = load_ids()
    canary.OUTPUT_ROOT = Path("/data/fangc/outputs/appworld_feasibility_260918")
    raise SystemExit(canary.main())
