"""Freeze the 100-task real annotation population and two independent packets."""

from __future__ import annotations

import json
from pathlib import Path

from tracegraph.benchmark.compression_audit.formal_v1 import prepare_annotation_packets
from tracegraph.plain_cli import PlainArgumentParser


def main() -> int:
    parser = PlainArgumentParser(
        description="从固定真实轨迹冻结 compression_audit_v1 的双人标注包，不生成 gold。"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--swe-source", type=Path, action="append", required=True,
                        help="可重复传入全部固定 SWE-Gym Parquet 分片")
    parser.add_argument("--ama-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260915)
    args = parser.parse_args()
    result = prepare_annotation_packets(
        config_path=args.config,
        swe_sources=args.swe_source,
        ama_source=args.ama_source,
        output_root=args.output,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
