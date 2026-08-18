"""Materialize a zero-API common-prefix Raw/Compiled/Drop fork plan."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from tracegraph.prefix_forks import build_fork_plan, discover_frozen_prefixes


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefixes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replicates", type=int, default=2)
    parser.add_argument("--max-agent-tool-actions", type=int, default=3)
    parser.add_argument("--session-cap", type=int, default=340)
    args = parser.parse_args()
    prefixes = discover_frozen_prefixes(args.prefixes)
    plan = build_fork_plan(
        prefixes,
        replicates=args.replicates,
        max_agent_tool_actions=args.max_agent_tool_actions,
        session_cap=args.session_cap,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "representation_fork_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "branch_id", "prefix_id", "prefix_sha256", "domain", "task_id", "object_class",
        "treatment", "replicate", "temperature", "max_agent_tool_actions", "auto_retry",
    ]
    with (args.output / "representation_fork_branches.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(plan["branches"])
    print(json.dumps({key: value for key, value in plan.items() if key != "branches"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
