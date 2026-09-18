"""Mine review candidates from pinned local SWE-Gym/OpenHands and AMA exports."""

from __future__ import annotations

import json
from pathlib import Path

from tracegraph.benchmark.compression_audit.dataset import load_config
from tracegraph.compression_audit_real import write_candidate_bundle
from tracegraph.plain_cli import PlainArgumentParser


def main() -> int:
    parser = PlainArgumentParser(
        description="从固定版本的真实轨迹提出待人工检查的失败链候选，不生成标准答案。"
    )
    parser.add_argument("--config", type=Path, required=True, help="固定来源版本的配置文件")
    parser.add_argument("--swe-source", type=Path, required=True, help="本地 SWE-Gym 轨迹文件")
    parser.add_argument("--ama-source", type=Path, required=True, help="本地 AMA-Bench 轨迹文件")
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的候选输出目录")
    parser.add_argument("--swe-limit", type=int, help="最多保留的 SWE-Gym 候选数")
    parser.add_argument("--ama-limit", type=int, help="最多保留的 AMA-Bench 候选数")
    args = parser.parse_args()

    config = load_config(args.config)
    sources = {str(item["id"]): item for item in config["real"]["sources"]}
    manifest = write_candidate_bundle(
        swe_source=args.swe_source,
        ama_source=args.ama_source,
        swe_revision=str(sources["swe_gym_openhands"]["trajectory_revision"]),
        ama_revision=str(sources["ama_bench"]["trajectory_revision"]),
        output_root=args.output,
        swe_limit=args.swe_limit,
        ama_limit=args.ama_limit,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
