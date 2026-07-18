"""Score P2 failure-chain labels and emit the phase-three gate input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.failure_chain_annotation import (
    score_failure_chain_annotations,
    write_failure_chain_score,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotator-a", type=Path, required=True)
    parser.add_argument("--annotator-b", type=Path, required=True)
    parser.add_argument("--annotation-key", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = score_failure_chain_annotations(
        args.annotator_a,
        args.annotator_b,
        args.annotation_key,
        adjudication=args.adjudication,
    )
    write_failure_chain_score(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
