"""从已保存的官方结果中选择失败较多的 tau3-bench 任务。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import json
from pathlib import Path

from tracegraph.failure_selection import analyze_failure_rich_tasks


def _mapping(values: list[str], *, label: str) -> dict[str, Path]:
    result = {}
    for value in values:
        domain, separator, path = value.partition("=")
        if not separator or not domain or not path:
            raise ValueError(f"{label} must use DOMAIN=PATH: {value!r}")
        result[domain] = Path(path)
    return result


def main() -> None:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="按 DOMAIN=PATH 指定一份已保存的 tau3 结果；每类任务重复一次。",
    )
    parser.add_argument(
        "--split",
        action="append",
        default=[],
        help="可选：按 DOMAIN=PATH 指定 split_tasks.json。",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-per-domain", type=int, default=5)
    args = parser.parse_args()

    input_paths = _mapping(args.input, label="--input")
    split_paths = _mapping(args.split, label="--split")
    payloads = {
        domain: json.loads(path.read_text(encoding="utf-8"))
        for domain, path in input_paths.items()
    }
    split_membership = {
        domain: {
            split: {str(task_id) for task_id in task_ids}
            for split, task_ids in json.loads(path.read_text(encoding="utf-8")).items()
        }
        for domain, path in split_paths.items()
    }
    report = analyze_failure_rich_tasks(
        payloads,
        split_membership=split_membership,
        top_per_domain=args.top_per_domain,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "selected_tasks": report["selected_tasks"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
