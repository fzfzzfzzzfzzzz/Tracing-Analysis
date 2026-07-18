"""Render a blind P2 failure-chain CSV as a local, read-only HTML view."""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path

from tracegraph.failure_chain_annotation import LABEL_FIELDS


STYLE = """
:root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; }
body { margin: 0; background: #f6f7f9; color: #1f2933; }
header { position: sticky; top: 0; z-index: 2; background: #fff; border-bottom: 1px solid #d9dee7; padding: 14px 22px; }
h1 { margin: 0 0 6px; font-size: 20px; }
main { max-width: 1180px; margin: 0 auto; padding: 20px; }
.notice { color: #52606d; font-size: 14px; }
.legend { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 6px; margin-top: 10px; font-size: 12px; }
.legend div { border: 1px solid #d9dee7; border-radius: 6px; padding: 6px; background: #f9fafb; }
.card { background: #fff; border: 1px solid #d9dee7; border-radius: 8px; margin: 14px 0; padding: 16px; box-shadow: 0 1px 2px rgba(15,23,42,.04); }
.meta { display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 8px; font-size: 13px; }
.meta div { background: #f6f7f9; border: 1px solid #e4e7eb; border-radius: 6px; padding: 7px; overflow-wrap: anywhere; }
.k { color: #52606d; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
h2 { font-size: 15px; margin: 14px 0 6px; }
pre { white-space: pre-wrap; word-break: break-word; background: #fbfcfe; border: 1px solid #e4e7eb; border-radius: 6px; padding: 10px; line-height: 1.42; font-size: 13px; max-height: 300px; overflow: auto; }
.fill { border-top: 1px dashed #cbd2d9; margin-top: 14px; padding-top: 10px; font-size: 13px; color: #52606d; }
@media (max-width: 850px) { .meta, .grid, .legend { grid-template-columns: 1fr; } main { padding: 12px; } }
"""


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def render(csv_path: Path, output: Path, annotator: str) -> int:
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    legend = "".join(
        f"<div><strong>{_escape(field)}</strong>: {_escape(', '.join(labels))}</div>"
        for field, labels in LABEL_FIELDS.items()
    )
    cards = []
    for index, row in enumerate(rows, start=1):
        meta = "".join(
            f'<div><div class="k">{_escape(key)}</div>{_escape(row.get(key))}</div>'
            for key in ("annotation_id", "source_kind", "domain", "task_id", "tool_name")
        )
        cards.append(
            f"""
<section class="card" id="row-{index}">
  <div class="k">ROW {index} / {len(rows)}</div>
  <div class="meta">{meta}</div>
  <div class="grid">
    <div><h2>Failed call</h2><pre>{_escape(row.get('failed_call'))}</pre></div>
    <div><h2>Failure result</h2><pre>{_escape(row.get('failure_result'))}</pre></div>
  </div>
  <h2>Later chain context</h2><pre>{_escape(row.get('later_chain_context'))}</pre>
  <h2>Local trace window</h2><pre>{_escape(row.get('local_trace_window'))}</pre>
  <div class="fill">在 {_escape(csv_path.name)} 中按 annotation_id 填写 5 个标签、confidence 和 notes。</div>
</section>"""
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>P2 Annotator {_escape(annotator)}</title><style>{STYLE}</style></head>
<body><header><h1>P2 Failure-chain 盲标查看器 — Annotator {_escape(annotator)}</h1>
<div class="notice">只读阅读版；不要打开 annotation_key.json。标签仍填写到 {_escape(csv_path.name)}。</div>
<div class="legend">{legend}</div></header><main>{''.join(cards)}</main></body></html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotator", required=True)
    args = parser.parse_args()
    count = render(args.input, args.output, args.annotator)
    print(f"rendered {count} blind rows to {args.output}")


if __name__ == "__main__":
    main()
