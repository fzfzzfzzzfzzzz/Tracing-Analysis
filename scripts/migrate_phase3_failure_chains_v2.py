"""Migrate the frozen phase-three failure-chain package to factorized v2 labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.failure_chain_annotation_v2 import migrate_v1_package_to_v2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = migrate_v1_package_to_v2(args.v1_package, args.output)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
