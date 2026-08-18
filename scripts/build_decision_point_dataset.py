"""Build the deterministic E1 decision-point construct package."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from tracegraph.decision_point_dataset import build_decision_point_dataset, discover_graph_records


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = sorted({key for row in rows for key in row}) or ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()}
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = discover_graph_records(args.input)
    dataset = build_decision_point_dataset(records)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "decision_point_dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for name in ("decision_points", "candidate_objects", "representation_rows"):
        _write_csv(args.output / f"{name}.csv", dataset[name])
    print(json.dumps({
        "dataset_sha256": dataset["dataset_sha256"],
        "sources": len(dataset["sources"]),
        "decision_points": len(dataset["decision_points"]),
        "candidate_objects": len(dataset["candidate_objects"]),
        "representation_rows": len(dataset["representation_rows"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
