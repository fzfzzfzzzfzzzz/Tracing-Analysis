"""Export blinded, independently shuffled lifecycle annotation sheets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.annotation import export_annotation_package
from tracegraph.experiments import discover_graphs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=120)
    parser.add_argument("--seed", type=int, default=300)
    args = parser.parse_args()
    graphs = discover_graphs(args.input)
    key = export_annotation_package(
        graphs,
        output_dir=args.output,
        sample_size=args.sample_size,
        seed=args.seed,
    )
    print(
        json.dumps(
            {
                "graphs": len(graphs),
                "sample_size": key["sample_size_actual"],
                "output": str(args.output),
                "blind_annotation": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
