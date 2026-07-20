"""Export clean factorized v2 failure-chain sheets from controlled and natural graphs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.failure_chain_annotation_v2 import (
    build_failure_chain_items_v2,
    export_failure_chain_package_v2,
)
from tracegraph.graph import TraceGraph


def _load(directory: Path, source_kind: str, *, full_ours_only: bool) -> list[dict]:
    records = []
    for path in sorted(directory.glob("*.json")):
        graph = TraceGraph.load(path)
        manager = graph.metadata.get(
            "evaluated_context_manager", graph.metadata.get("context_manager")
        )
        if full_ours_only and manager != "full_ours":
            continue
        records.append((source_kind, str(path), graph))
    return build_failure_chain_items_v2(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controlled-graphs", type=Path, required=True)
    parser.add_argument("--natural-graphs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controlled-sample-size", type=int, default=32)
    parser.add_argument("--natural-sample-size", type=int, default=28)
    parser.add_argument("--seed", type=int, default=4400)
    args = parser.parse_args()
    controlled = _load(args.controlled_graphs, "controlled", full_ours_only=True)
    natural = _load(args.natural_graphs, "natural", full_ours_only=False)
    key = export_failure_chain_package_v2(
        controlled_items=controlled,
        natural_items=natural,
        output_dir=args.output,
        controlled_sample_size=args.controlled_sample_size,
        natural_sample_size=args.natural_sample_size,
        seed=args.seed,
    )
    print(
        json.dumps(
            {
                "chain_count": key["chain_count"],
                "source_counts": key["source_counts"],
                "controlled_candidates": len(controlled),
                "natural_candidates": len(natural),
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
