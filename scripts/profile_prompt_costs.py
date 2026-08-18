"""Profile the five GDSC prompt-cost layers from frozen JSON/JSONL artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from tracegraph.capture import estimate_tokens
from tracegraph.provider_cost import canonical_request_json
from tracegraph.trajectory_artifacts import sha256_json


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


LAYERS = ("graph_selected", "compiled", "protocol_closed", "serialized_request", "provider_actual")


def _objects(path: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    paths = [path] if path.is_file() else sorted(path.rglob("*.json")) + sorted(path.rglob("*.jsonl"))
    for source in paths:
        try:
            if source.suffix == ".jsonl":
                for index, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
                    if line.strip():
                        value = json.loads(line)
                        if isinstance(value, dict):
                            yield f"{source.as_posix()}:{index}", value
            else:
                value = json.loads(source.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    if isinstance(value.get("rows"), list):
                        for index, row in enumerate(value["rows"], start=1):
                            if isinstance(row, dict):
                                yield f"{source.as_posix()}#rows/{index}", row
                    else:
                        yield source.as_posix(), value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue


def _matrix_context_view_paths(report_path: Path, manager: str) -> list[Path]:
    """Resolve the frozen per-session context views selected by a matrix report."""

    report = json.loads(report_path.read_text(encoding="utf-8"))
    sessions = report.get("sessions")
    if not isinstance(sessions, list):
        raise ValueError("live matrix report must contain a sessions list")
    paths: list[Path] = []
    for session in sessions:
        if not isinstance(session, Mapping) or session.get("manager") != manager:
            continue
        trace_file = session.get("trace_file")
        if not isinstance(trace_file, str):
            raise ValueError("selected matrix session is missing trace_file")
        path = Path(trace_file).with_name("context_views.jsonl")
        if not path.is_file():
            raise FileNotFoundError(path)
        paths.append(path)
    if not paths:
        raise ValueError(f"matrix report has no sessions for manager={manager!r}")
    return sorted(paths)


def _eligibility_headrooms(path: Path) -> dict[str, float]:
    """Read per-domain oracle headroom without conflating it with prompt-cost rows."""

    report = json.loads(path.read_text(encoding="utf-8"))
    domains = report.get("domains")
    if not isinstance(domains, Mapping):
        raise ValueError("eligibility report must contain a domains object")
    values: dict[str, float] = {}
    for domain, payload in sorted(domains.items()):
        if not isinstance(payload, Mapping):
            continue
        value = payload.get("median_oracle_headroom")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            values[str(domain)] = float(value)
    return values


def _integer(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return max(0, int(value))
    return None


def _first(mapping: Mapping[str, Any], names: tuple[str, ...]) -> int | None:
    for name in names:
        value = _integer(mapping.get(name))
        if value is not None:
            return value
    return None


def profile_row(source: str, value: Mapping[str, Any]) -> dict[str, Any] | None:
    costs = value.get("costs") if isinstance(value.get("costs"), Mapping) else value
    metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
    graph = _first(costs, ("graph_selected", "graph_selected_tokens", "selected_tokens"))
    compiled = _first(costs, ("compiled", "compiled_tokens"))
    closed = _first(costs, ("protocol_closed", "protocol_closed_tokens"))
    serialized = _first(costs, ("serialized_request", "serialized_request_tokens"))
    actual = _first(costs, ("provider_actual", "provider_actual_tokens", "prompt_tokens", "input_tokens"))
    provenance = "declared"

    items = value.get("items")
    if graph is None and isinstance(items, list):
        graph = sum(
            int(item.get("token_count") or 0) for item in items if isinstance(item, Mapping)
        )
    if compiled is None and graph is not None:
        compiled = graph
        provenance = "legacy_graph_proxy"
    if closed is None and value.get("protocol_closed_messages") is not None:
        closed = estimate_tokens(value["protocol_closed_messages"])
    if closed is None:
        closed = _first(metadata, ("protocol_closed_message_tokens", "protocol_closed_tokens"))
    request = value.get("request") or value.get("serialized_request_payload")
    if serialized is None and isinstance(request, Mapping):
        serialized = estimate_tokens(canonical_request_json(request))
        provenance = "deterministic_serialization_estimate"
    if not any(item is not None for item in (graph, compiled, closed, serialized, actual)):
        return None
    mismatch = None
    if graph is not None and serialized:
        mismatch = (serialized - graph) / serialized
    closure_mismatch = None
    if graph is not None and closed:
        closure_mismatch = (closed - graph) / closed
    headroom_bps = _first(value, ("provider_token_oracle_headroom_bps",))
    oracle_ratio = headroom_bps / 10_000 if headroom_bps is not None else None
    if oracle_ratio is None:
        raw = _first(value, ("raw_serialized_request_tokens", "full_serialized_request_tokens"))
        oracle = _first(value, ("oracle_serialized_request_tokens",))
        if raw and oracle is not None:
            oracle_ratio = (raw - oracle) / raw
    return {
        "source": source,
        "record_id": str(value.get("request_hash") or value.get("branch_id") or sha256_json(value)[:24]),
        "graph_selected": graph,
        "compiled": compiled,
        "protocol_closed": closed,
        "serialized_request": serialized,
        "provider_actual": actual,
        "graph_to_protocol_closed_mismatch_ratio": closure_mismatch,
        "graph_to_serialized_mismatch_ratio": mismatch,
        "provider_token_oracle_headroom": oracle_ratio,
        "accounting_provenance": provenance,
    }


def build_report(
    rows: list[dict[str, Any]],
    *,
    minimum_headroom: float = 0.30,
    expected_records: int | None = None,
    supplemental_headrooms: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    complete = [row for row in rows if all(row[layer] is not None for layer in LAYERS[:-1])]
    serialized_mismatches = [
        float(row["graph_to_serialized_mismatch_ratio"])
        for row in rows
        if row["graph_to_serialized_mismatch_ratio"] is not None
    ]
    closure_mismatches = [
        float(row["graph_to_protocol_closed_mismatch_ratio"])
        for row in rows
        if row["graph_to_protocol_closed_mismatch_ratio"] is not None
    ]
    mismatches = serialized_mismatches + closure_mismatches
    headrooms = [
        float(row["provider_token_oracle_headroom"])
        for row in rows
        if row["provider_token_oracle_headroom"] is not None
    ]
    domain_headrooms = dict(sorted((supplemental_headrooms or {}).items()))
    gate_headrooms = headrooms + list(domain_headrooms.values())
    report: dict[str, Any] = {
        "schema_version": "gdsc_prompt_cost_profile_v1",
        "five_layers": list(LAYERS),
        "record_count": len(rows),
        "complete_estimated_layer_count": len(complete),
        "provider_actual_count": sum(row["provider_actual"] is not None for row in rows),
        "mismatch_observation_count": len(mismatches),
        "closure_mismatch_observation_count": len(closure_mismatches),
        "serialized_mismatch_observation_count": len(serialized_mismatches),
        "median_graph_to_protocol_closed_mismatch_ratio": (
            statistics.median(closure_mismatches) if closure_mismatches else None
        ),
        "median_graph_to_serialized_mismatch_ratio": (
            statistics.median(serialized_mismatches) if serialized_mismatches else None
        ),
        "median_oracle_headroom": statistics.median(headrooms) if headrooms else None,
        "candidate_domain_median_oracle_headroom": domain_headrooms,
        "development_gate": {
            "expected_record_count": expected_records is None or len(rows) == expected_records,
            "cost_mismatch_reproduced": bool(mismatches) and any(abs(value) > 0 for value in mismatches),
            "candidate_headroom_at_least_30_percent": bool(gate_headrooms)
            and max(gate_headrooms) >= minimum_headroom,
        },
        "interpretation": (
            "Missing serialized/provider layers remain null. Legacy graph-selected counts are never "
            "presented as provider-actual usage."
        ),
        "rows": rows,
    }
    report["development_gate"]["passed"] = all(report["development_gate"].values())
    report["report_sha256"] = sha256_json(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-headroom", type=float, default=0.30)
    parser.add_argument("--expected-records", type=int)
    parser.add_argument(
        "--live-matrix-report",
        type=Path,
        help="Use only context views referenced by this frozen live-matrix report.",
    )
    parser.add_argument(
        "--manager",
        help="Manager to select with --live-matrix-report (for example full_ours).",
    )
    parser.add_argument(
        "--eligibility-report",
        type=Path,
        help="Supplement the R0 gate with per-domain provider-token oracle headroom.",
    )
    args = parser.parse_args()
    if bool(args.live_matrix_report) != bool(args.manager):
        parser.error("--live-matrix-report and --manager must be provided together")
    inputs = (
        _matrix_context_view_paths(args.live_matrix_report, args.manager)
        if args.live_matrix_report and args.manager
        else [args.input]
    )
    rows = [
        row
        for input_path in inputs
        for source, value in _objects(input_path)
        if (row := profile_row(source, value))
    ]
    report = build_report(
        rows,
        minimum_headroom=args.minimum_headroom,
        expected_records=args.expected_records,
        supplemental_headrooms=(
            _eligibility_headrooms(args.eligibility_report)
            if args.eligibility_report
            else None
        ),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "prompt_cost_profile.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (args.output / "prompt_cost_rows.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["source"])
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
