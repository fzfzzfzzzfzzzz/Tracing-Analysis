"""Render deterministic R2.1 component summaries from frozen attribution rows."""

from __future__ import annotations

import argparse
import csv
import html
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


COMPONENTS = (
    "raw_serialized_tokens",
    "raw_fixed_policy_tools_tokens",
    "raw_dynamic_history_marginal_tokens",
    "raw_policy_marginal_tokens",
    "raw_tool_schema_marginal_tokens",
    "compiled_serialized_tokens",
    "compiled_state_marginal_tokens",
    "compiled_closed_history_marginal_tokens",
    "compiled_tool_schema_marginal_tokens",
    "compiled_protocol_closure_increment",
    "compiled_serializer_and_schema_overhead",
    "constructive_hard_floor_tokens",
    "target_tokens_at_30_percent",
)


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) not in {None, ""}]
    return statistics.median(values)


def component_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    grouped["overall"].extend(rows)
    for row in rows:
        grouped[str(row["domain"])].append(row)
    return [
        {
            "scope": scope,
            "decision_points": len(scope_rows),
            **{f"median_{key}": _median(scope_rows, key) for key in COMPONENTS},
        }
        for scope, scope_rows in sorted(
            grouped.items(), key=lambda item: (item[0] != "overall", item[0])
        )
    ]


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_svg(path: Path, summaries: Sequence[Mapping[str, Any]]) -> None:
    width, height = 1120, 620
    left, top, plot_height = 90, 90, 390
    groups = list(summaries)
    series = (
        ("Raw total", "median_raw_serialized_tokens", "#355c7d"),
        ("GDSC-Core v1", "median_compiled_serialized_tokens", "#6c5b7b"),
        ("Fixed policy + tools floor", "median_raw_fixed_policy_tools_tokens", "#c06c84"),
        ("Constructive hard-state floor", "median_constructive_hard_floor_tokens", "#f67280"),
        ("30% target", "median_target_tokens_at_30_percent", "#2a9d8f"),
    )
    maximum = max(float(group[key]) for group in groups for _, key, _ in series)
    scale = plot_height / maximum
    group_width = 300
    bar_width = 42
    gap = 8
    chunks = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="560" y="36" text-anchor="middle" font-family="sans-serif" font-size="22" font-weight="700">GDSC R2.1 serialized-request attainability</text>',
        '<text x="560" y="62" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#444">Median tokens; full policy and native τ³ tool schemas retained</text>',
    ]
    for tick in range(0, int(maximum) + 1000, 1000):
        y = top + plot_height - tick * scale
        chunks.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="1080" y2="{y:.1f}" stroke="#e6e6e6"/>'
        )
        chunks.append(
            f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="11" fill="#555">{tick}</text>'
        )
    for group_index, group in enumerate(groups):
        base_x = left + 45 + group_index * group_width
        for series_index, (label, key, color) in enumerate(series):
            value = float(group[key])
            x = base_x + series_index * (bar_width + gap)
            bar_height = value * scale
            y = top + plot_height - bar_height
            chunks.extend(
                [
                    f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_height:.1f}" rx="2" fill="{color}"/>',
                    f'<text x="{x + bar_width / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-family="sans-serif" font-size="10" fill="#333">{value:.0f}</text>',
                ]
            )
        chunks.append(
            f'<text x="{base_x + 120}" y="{top + plot_height + 28}" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="700">{html.escape(str(group["scope"]))}</text>'
        )
    legend_x = 105
    legend_y = 555
    for index, (label, _, color) in enumerate(series):
        x = legend_x + index * 200
        chunks.extend(
            [
                f'<rect x="{x}" y="{legend_y}" width="14" height="14" fill="{color}"/>',
                f'<text x="{x + 20}" y="{legend_y + 12}" font-family="sans-serif" font-size="11">{html.escape(label)}</text>',
            ]
        )
    chunks.append(
        '<text x="560" y="600" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#555">Leave-one-component-out marginals are reported in CSV and are not assumed additive.</text>'
    )
    chunks.append("</svg>")
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.rows.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("attribution row file is empty")
    summaries = component_rows(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output / "cost_attribution_component_medians.csv", summaries)
    _write_svg(args.output / "fixed_cost_reachability.svg", summaries)


if __name__ == "__main__":
    main()
