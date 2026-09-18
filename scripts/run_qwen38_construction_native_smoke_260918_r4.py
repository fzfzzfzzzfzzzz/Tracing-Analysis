"""Run six real method-build jobs through the typed construction adapter."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from tracegraph.benchmark.compression_audit.build import verify_file_manifest
from tracegraph.benchmark.compression_audit.io import file_sha256
from tracegraph.benchmark.server_eval.config import LocalTokenizer, load_config
from tracegraph.benchmark.server_eval.data_workflow import load_suite_dataset
from tracegraph.benchmark.server_eval.methods import MemoryMethods
from tracegraph.benchmark.server_eval.provider import ServerLedger


ROOT = Path("/home/fangc/tracegraph-server-eval-260917-ama-scorecard-r5")
DATASET = Path("/data/fangc/tracegraph/data/server_eval_v0_1_handoff_260912")
BASE_CONFIG = ROOT / "configs/server_eval_qwen38_27b_native_calibration_b1024_260918_r2.json"
OUTPUT = Path(os.environ.get(
    "TRACEGRAPH_CONSTRUCTION_SMOKE_OUTPUT",
    "/data/fangc/qwen38_27b_construction_native_smoke_260918_r4",
))
PREFIX_IDS = (
    "controlled:parameter_schema:software:R0:long",
    "controlled:parameter_schema:data:R2:long",
)
METHODS = ("rolling_summary", "acon_official", "ama_official_bm25")


def write_report(rows: list[dict], *, complete: bool) -> None:
    (OUTPUT / "report.json").write_text(json.dumps({
        "purpose": "typed native-tool construction smoke; not a benchmark score",
        "complete": complete,
        "base_config": str(BASE_CONFIG),
        "base_config_sha256": file_sha256(BASE_CONFIG),
        "changed_fields": {
            "methods": list(METHODS),
            "models[0].construction_transport": "native_tool_call",
        },
        "prefix_ids": list(PREFIX_IDS),
        "implementation": {
            path: file_sha256(ROOT / path)
            for path in (
                "src/tracegraph/benchmark/server_eval/config.py",
                "src/tracegraph/benchmark/server_eval/methods.py",
                "src/tracegraph/benchmark/server_eval/provider.py",
            )
        },
        "rows": rows,
    }, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"refusing existing output: {OUTPUT}")
    verify_file_manifest(DATASET)
    config = load_config(BASE_CONFIG)
    config["methods"] = list(METHODS)
    config["models"][0]["construction_transport"] = "native_tool_call"
    model = config["models"][0]
    model_id = model["id"]
    prefixes, _, _ = load_suite_dataset(DATASET, config, ROOT)
    by_id = {prefix.prefix_id: prefix for prefix in prefixes}
    if set(PREFIX_IDS) - set(by_id):
        raise SystemExit("a frozen smoke prefix is missing")

    OUTPUT.mkdir(parents=True)
    (OUTPUT / "config.snapshot.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    )
    counter = LocalTokenizer(model, ROOT)
    ledger = ServerLedger(OUTPUT, config, {model_id: counter}, set())
    engine = MemoryMethods(config, counter.count, ROOT, ledger.for_model(model_id))
    rows: list[dict] = []
    write_report(rows, complete=False)

    for prefix_id in PREFIX_IDS:
        for method in METHODS:
            job_id = f"construction-smoke:{prefix_id}:{method}"
            started = time.perf_counter()
            try:
                state = engine.build(by_id[prefix_id], method, 1024, job_id)
                usage = state["usage"]
                payload = state["payload"]
                memory = payload.get("summary") or payload.get("state_mem") or ""
                row = {
                    "job_id": job_id,
                    "prefix_id": prefix_id,
                    "method_id": method,
                    "status": "complete",
                    "elapsed_seconds": time.perf_counter() - started,
                    "provider_calls": sum(
                        item["job_id"] == job_id and item["kind"] == "construction"
                        for item in ledger.rows
                    ),
                    "memory_chars": len(memory),
                    "memory_preview": memory[:500],
                    "ingest_eligible": usage.get("ingest_eligible"),
                    "resident_tokens": usage.get("resident_tokens"),
                    "construction_chunking": usage.get("construction_chunking"),
                    "state_hash": state["hash"],
                }
            except Exception as exc:  # Probe must record every method outcome.
                row = {
                    "job_id": job_id,
                    "prefix_id": prefix_id,
                    "method_id": method,
                    "status": "failed",
                    "elapsed_seconds": time.perf_counter() - started,
                    "provider_calls": sum(
                        item["job_id"] == job_id and item["kind"] == "construction"
                        for item in ledger.rows
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            rows.append(row)
            write_report(rows, complete=False)
            print(json.dumps(row, ensure_ascii=False), flush=True)

    write_report(rows, complete=True)


if __name__ == "__main__":
    main()
