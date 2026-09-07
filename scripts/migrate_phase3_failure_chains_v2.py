"""把第三阶段失败记录检查包转换为第二版格式。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import json
from pathlib import Path

from tracegraph.context_engine.annotation_v2 import migrate_v1_package_to_v2


def main() -> None:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument("--v1-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = migrate_v1_package_to_v2(args.v1_package, args.output)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
