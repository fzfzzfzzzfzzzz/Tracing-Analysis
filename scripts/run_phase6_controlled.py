"""不调用外部模型，运行第六阶段第一至第三步本地测试。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import csv
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from tracegraph.archive import ArchiveStore
from tracegraph.decision_state import stable_digest
from tracegraph.phase6_experiment import (
    ABLATION_IDS,
    MANAGER_IDS,
    run_local_method,
)
from tracegraph.phase6_gates import evaluate_e1_eligibility, evaluate_e2_gate
from tracegraph.phase6_metrics import (
    statistical_report,
    summarize_manager_rows,
)
from tracegraph.phase6_scenarios import generate_trace_lifecycle_suite


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        + "\n"
    ).encode("utf-8")


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"Phase 6 artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _write_json(path: Path, value: Any) -> None:
    _write_new(path, _json_bytes(value))


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    payload = b"".join(
        (
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
            + "\n"
        ).encode("utf-8")
        for row in rows
    )
    _write_new(path, payload)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_hash(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": path.as_posix(), "exists": False, "file_count": 0, "sha256": None}
    files = sorted(item for item in path.rglob("*") if item.is_file())
    digest = hashlib.sha256()
    for item in files:
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(item).encode("ascii"))
        digest.update(b"\n")
    return {
        "path": path.as_posix(),
        "exists": True,
        "file_count": len(files),
        "sha256": digest.hexdigest(),
    }


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], text=True, encoding="utf-8", errors="replace"
    ).strip()


def _deterministic_payload(suite: Any) -> dict[str, Any]:
    return {
        "prefixes": [item.to_prefix_dict() for item in suite],
        "gold": [item.to_gold_dict() for item in suite],
    }


def _write_summary_csv(path: Path, summaries: Mapping[str, Mapping[str, Any]]) -> None:
    fields = sorted({"manager_id", *(key for row in summaries.values() for key in row)})
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory) / "metrics.csv"
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for manager_id, values in sorted(summaries.items()):
                writer.writerow({"manager_id": manager_id, **dict(values)})
        lines.append(temporary.read_text(encoding="utf-8"))
    _write_new(path, "".join(lines).encode("utf-8"))


def _error_taxonomy(gates: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    failed = []
    for gate_name, gate in gates.items():
        for item in gate.get("criteria", ()):
            if not item.get("passed"):
                failed.append(
                    {
                        "gate": gate_name,
                        "metric": item.get("metric"),
                        "observed": item.get("observed"),
                        "required": item.get("required"),
                    }
                )
    manager_errors: dict[str, dict[str, int]] = {}
    for manager_id in (*MANAGER_IDS, *ABLATION_IDS):
        selected = [row for row in rows if row["manager_id"] == manager_id]
        manager_errors[manager_id] = {
            "protocol_invalid": sum(not bool(row["protocol_valid"]) for row in selected),
            "unsafe_eviction": sum(float(row["unsafe_eviction_node_rate"]) > 0 for row in selected),
            "stale_as_current": sum(bool(row["superseded_as_current_error"]) for row in selected),
            "reactivation_failure": sum(
                row["fork_type"] == "REACTIVATE" and not bool(row["current_task_success"])
                for row in selected
            ),
            "distractor_false_reactivation": sum(
                bool(row["distractor_false_reactivation"]) for row in selected
            ),
        }
    return {
        "schema_version": "phase6_error_taxonomy_v1",
        "failed_gate_criteria": failed,
        "manager_error_counts": manager_errors,
    }


def _validate_config(config: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "run_id",
        "base_seed",
        "reactivation_budget_tokens",
        "bootstrap_samples",
        "output_root",
        "specification_path",
        "expected_base_commit",
        "protected_roots",
    }
    missing = sorted(required.difference(config))
    if missing:
        raise ValueError(f"missing Phase 6 config fields: {missing}")
    if config["schema_version"] != "phase6_controlled_config_v1":
        raise ValueError("unsupported Phase 6 config schema")
    if bool(config.get("external_provider_calls_allowed")):
        raise ValueError("Phase 6 E0-E2 forbids external provider calls")
    if int(config["base_seed"]) != 20260830:
        raise ValueError("Phase 6 v1 base_seed is frozen at 20260830")
    if int(config["reactivation_budget_tokens"]) != 1024:
        raise ValueError("Phase 6 v1 reactivation budget is frozen at 1024")


def main(argv: list[str] | None = None) -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/phase6_controlled_v1.json"),
        help="本地测试使用的设置文件",
    )
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _validate_config(config)
    repo_root = Path(__file__).resolve().parents[1]
    output_root = (repo_root / str(config["output_root"])).resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(
            f"append-only Phase 6 output already exists: {output_root}"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    archive_root = output_root / "archive"

    commit = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    specification = repo_root / str(config["specification_path"])
    if not specification.is_file():
        raise FileNotFoundError(specification)
    protected_before = [
        _tree_hash(repo_root / str(path)) for path in config["protected_roots"]
    ]

    suite = generate_trace_lifecycle_suite(
        archive_root,
        base_seed=int(config["base_seed"]),
    )
    with tempfile.TemporaryDirectory() as directory:
        replay = generate_trace_lifecycle_suite(
            Path(directory) / "archive",
            base_seed=int(config["base_seed"]),
        )
        deterministic_match = stable_digest(_deterministic_payload(suite)) == stable_digest(
            _deterministic_payload(replay)
        )

    prefixes = [item.to_prefix_dict() for item in suite]
    gold_rows = [item.to_gold_dict() for item in suite]
    forks = [fork.to_dict() for prefix in suite for fork in prefix.forks]
    m0_rows = [
        run_local_method(
            prefix,
            fork,
            "M0_full_history",
            archive_root=archive_root,
            reactivation_budget=int(config["reactivation_budget_tokens"]),
        )
        for prefix in suite
        for fork in prefix.forks
    ]
    archive_failures = ArchiveStore(archive_root).verify_all()
    e1_gate = evaluate_e1_eligibility(
        suite,
        m0_rows,
        archive_failures=archive_failures,
        deterministic_match=deterministic_match,
    )
    rows: list[Mapping[str, Any]] = list(m0_rows)
    e2_gate: dict[str, Any]
    if e1_gate["decision"] == "pass":
        rows = [
            run_local_method(
                prefix,
                fork,
                manager_id,
                archive_root=archive_root,
                reactivation_budget=int(config["reactivation_budget_tokens"]),
            )
            for prefix in suite
            for fork in prefix.forks
            for manager_id in (*MANAGER_IDS, *ABLATION_IDS)
        ]
        e2_gate = evaluate_e2_gate(rows)
    else:
        e2_gate = {
            "schema_version": "phase6_e2_gate_v1",
            "decision": "not_run",
            "reason": "E1 eligibility failed",
            "criteria": [],
        }

    summaries = summarize_manager_rows(rows)
    stats = (
        statistical_report(
            rows,
            bootstrap_samples=int(config["bootstrap_samples"]),
            seed=int(config["base_seed"]),
        )
        if e1_gate["decision"] == "pass"
        else {"schema_version": "phase6_statistical_report_v1", "status": "not_run"}
    )
    gates = {"G0": {"decision": "pass"}, "G1_E1": e1_gate, "G2_E2": e2_gate}
    config_snapshot = dict(config)
    config_snapshot["config_sha256"] = _sha256(config_path)
    _write_json(output_root / "config.snapshot.json", config_snapshot)
    _write_jsonl(output_root / "prefixes.jsonl", prefixes)
    _write_jsonl(output_root / "forks.jsonl", forks)
    _write_jsonl(output_root / "lifecycle_gold.jsonl", gold_rows)
    _write_jsonl(output_root / "manager_outputs.jsonl", rows)
    metrics = {
        "schema_version": "phase6_metrics_v1",
        "manager_summaries": summaries,
        "statistical_report": stats,
        "controlled_synthetic": True,
        "provider_requests": 0,
    }
    _write_json(output_root / "metrics.json", metrics)
    _write_summary_csv(output_root / "metrics.csv", summaries)
    _write_json(output_root / "gate_report.json", gates)
    _write_json(output_root / "error_taxonomy.json", _error_taxonomy(gates, list(rows)))

    protected_after = [
        _tree_hash(repo_root / str(path)) for path in config["protected_roots"]
    ]
    protected_unchanged = protected_before == protected_after
    artifact_names = (
        "config.snapshot.json",
        "prefixes.jsonl",
        "forks.jsonl",
        "lifecycle_gold.jsonl",
        "manager_outputs.jsonl",
        "metrics.json",
        "metrics.csv",
        "gate_report.json",
        "error_taxonomy.json",
    )
    artifact_hashes = {
        name: _sha256(output_root / name) for name in artifact_names
    }
    hashes = {
        "schema_version": "phase6_hash_manifest_v1",
        "artifacts": artifact_hashes,
        "protected_before": protected_before,
        "protected_after": protected_after,
        "protected_unchanged": protected_unchanged,
        "archive_object_count": len(list((archive_root / "objects").glob("*/*.json"))),
        "archive_integrity_failures": archive_failures,
    }
    _write_json(output_root / "hashes.json", hashes)
    manifest = {
        "schema_version": "phase6_controlled_manifest_v1",
        "run_id": config["run_id"],
        "commit": commit,
        "expected_base_commit": config["expected_base_commit"],
        "branch": branch,
        "python": sys.version,
        "platform": platform.platform(),
        "specification_sha256": _sha256(specification),
        "config_sha256": _sha256(config_path),
        "prefix_count": len(prefixes),
        "fork_count": len(forks),
        "manager_output_count": len(rows),
        "managers": list(MANAGER_IDS),
        "ablations": list(ABLATION_IDS),
        "gates": {
            "G0": "pass" if protected_unchanged else "fail",
            "G1_E1": e1_gate["decision"],
            "G2_E2": e2_gate["decision"],
            "E3": "not_run",
            "E4": "not_run",
        },
        "provider_requests": 0,
        "external_network_used": False,
        "controlled_synthetic": True,
        "preimplementation_validation": dict(config.get("preimplementation_validation", {})),
        "artifact_hashes": {**artifact_hashes, "hashes.json": _sha256(output_root / "hashes.json")},
    }
    _write_json(output_root / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if protected_unchanged and e1_gate["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
