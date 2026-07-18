"""Retokenize saved TraceGraph files with content-only accounting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tracegraph.retokenize import retokenize_trace, retokenize_tree


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows = (
        retokenize_tree(args.input)
        if args.input.is_dir()
        else [retokenize_trace(args.input)]
    )
    report = {
        "trace_count": len(rows),
        "changed_trace_count": sum(row["changed_nodes"] > 0 for row in rows),
        "changed_node_count": sum(row["changed_nodes"] for row in rows),
        "old_total_tokens": sum(row["old_total_tokens"] for row in rows),
        "new_total_tokens": sum(row["new_total_tokens"] for row in rows),
        "validation_error_count": sum(
            len(row["validation_errors"]) for row in rows
        ),
        "traces": rows,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
