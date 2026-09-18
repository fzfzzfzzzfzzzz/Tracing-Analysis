#!/usr/bin/env python3
"""Build the explicitly development-only dataset from reviewer B's GLM labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.benchmark.compression_audit.provisional import (
    build_single_ai_development_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--controlled-dataset", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_single_ai_development_dataset(
        candidates_path=args.candidates,
        reviews_path=args.reviews,
        controlled_dataset=args.controlled_dataset,
        output=args.output,
        adjudication_path=args.adjudication,
    )
    print(json.dumps({key: result[key] for key in (
        "accepted_count", "rejected_count", "source_counts",
        "original_split_counts", "recoverability_counts", "label_source_counts",
        "formal_v1_ready",
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
