"""Reconcile embedded request snapshots against immutable pre/post-send logs."""

import json
from pathlib import Path

from tracegraph.compression_audit_live import reconcile_live_recordings
from tracegraph.plain_cli import PlainArgumentParser


def main() -> int:
    parser = PlainArgumentParser(
        description="核对持久化账本，在新目录修复请求副本；不改答案、不调用模型。"
    )
    parser.add_argument("--source-run", type=Path, required=True, help="已完成的原始运行目录")
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的修复副本目录")
    args = parser.parse_args()
    result = reconcile_live_recordings(args.source_run, args.output)
    print(json.dumps(result["reconciliation"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
