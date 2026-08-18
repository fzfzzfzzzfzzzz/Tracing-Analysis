#!/usr/bin/env python3
"""Record a new create-only official-free pricing recheck for Phase 5.2 resume."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tracegraph.lifecycle_annotation import load_phase52_config  # noqa: E402
from tracegraph.trajectory_artifacts import sha256_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checked-at", type=date.fromisoformat, default=date.today())
    parser.add_argument("--confirm-official-free", action="store_true")
    args = parser.parse_args()
    if not args.confirm_official_free:
        raise RuntimeError("recheck the official pages and pass --confirm-official-free")
    config = load_phase52_config(REPO_ROOT / "configs/phase52_lifecycle_modeling.json")
    base = dict(config["pricing_snapshot"])
    base["checked_at"] = args.checked_at.isoformat()
    if any(base[key] != "free" for key in ("input_price", "cached_input_price", "output_price")):
        raise RuntimeError("paid pricing cannot be recorded for this Phase 5.2 run")
    snapshot = {
        **base,
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "manual_official_page_recheck_confirmed": True,
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    root = REPO_ROOT / config["output_root"] / "pricing_snapshots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{args.checked_at.isoformat()}_{snapshot['snapshot_sha256'][:16]}.json"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        json.dumps(
            {"path": path.as_posix(), "snapshot_sha256": snapshot["snapshot_sha256"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
