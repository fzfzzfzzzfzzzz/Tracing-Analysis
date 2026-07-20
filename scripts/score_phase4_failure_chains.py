"""Score factorized phase-four failure-chain v2 annotations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.failure_chain_annotation_v2 import (
    score_failure_chain_annotations_v2,
    write_failure_chain_score_v2,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotator-a", type=Path, required=True)
    parser.add_argument("--annotator-b", type=Path, required=True)
    parser.add_argument("--annotation-key", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--minimum-complete-chains", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = score_failure_chain_annotations_v2(
        args.annotator_a,
        args.annotator_b,
        args.annotation_key,
        adjudication=args.adjudication,
        minimum_complete_chains=args.minimum_complete_chains,
    )
    write_failure_chain_score_v2(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
