"""Replay only the Qwen3.8-27B ACON/AMA bridge failures from matrix r6."""

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
BASE_CONFIG = ROOT / "configs/server_eval_qwen38_27b_unified_methods_b1024_native_260918_r6.json"
OUTPUT = Path(os.environ.get(
    "TRACEGRAPH_METHOD_REPAIR_OUTPUT",
    "/data/fangc/qwen38_27b_method_bridge_repair_smoke_260918_r9",
))
CONSTRUCTION_MAX_OUTPUT_TOKENS = int(os.environ.get(
    "TRACEGRAPH_CONSTRUCTION_MAX_OUTPUT_TOKENS", "2048",
))
if not 2048 <= CONSTRUCTION_MAX_OUTPUT_TOKENS <= 8192:
    raise ValueError("construction output limit must be between 2048 and 8192")
CONSTRUCTION_SUBMISSION_MAX_CHARS = int(os.environ.get(
    "TRACEGRAPH_CONSTRUCTION_SUBMISSION_MAX_CHARS", "4096",
))
RETRIEVAL_SUBMISSION_MAX_CHARS = int(os.environ.get(
    "TRACEGRAPH_RETRIEVAL_SUBMISSION_MAX_CHARS", "4096",
))
REPAIR_SCOPE = os.environ.get("TRACEGRAPH_METHOD_REPAIR_SCOPE", "all")
if REPAIR_SCOPE not in ("all", "ama_r3_only"):
    raise ValueError("unknown repair smoke scope")
ACON_PREFIXES = (
    "controlled:shell_syntax:operations:R0:long",
    "controlled:parameter_schema:software:R1:long",
    "controlled:shell_syntax:operations:R1:long",
    "controlled:parameter_schema:operations:R3:long",
    "controlled:shell_syntax:software:R3:long",
)
AMA_QUERIES = {
    "controlled:shell_syntax:operations:R0:long": (
        "audit_chain", "audit_failure_cause", "distractor_current",
    ),
    "controlled:shell_syntax:operations:R1:long": (
        "audit_chain", "audit_failure_cause", "distractor_current",
    ),
    "controlled:parameter_schema:data:R2:long": (
        "audit_chain", "distractor_current",
    ),
    "controlled:shell_syntax:data:R2:long": ("audit_chain",),
    "controlled:parameter_schema:operations:R3:long": ("audit_chain",),
}


def write_report(rows: list[dict], *, complete: bool) -> None:
    (OUTPUT / "report.json").write_text(json.dumps({
        "purpose": "targeted ACON/AMA method-bridge repair smoke; not a benchmark score",
        "complete": complete,
        "base_config": str(BASE_CONFIG),
        "base_config_sha256": file_sha256(BASE_CONFIG),
        "changed_fields": {
            "models[0].construction_max_output_tokens": CONSTRUCTION_MAX_OUTPUT_TOKENS,
            "models[0].construction_submission_max_chars": CONSTRUCTION_SUBMISSION_MAX_CHARS,
            "models[0].retrieval_submission_max_chars": RETRIEVAL_SUBMISSION_MAX_CHARS,
            "models[0].retrieval_transport": "native_tool_call",
            "ama_code_search.mode": "bwrap",
        },
        "repair_scope": REPAIR_SCOPE,
        "implementation": {
            path: file_sha256(ROOT / path)
            for path in (
                "src/tracegraph/benchmark/server_eval/config.py",
                "src/tracegraph/benchmark/server_eval/external.py",
                "src/tracegraph/benchmark/server_eval/methods.py",
                "src/tracegraph/benchmark/server_eval/provider.py",
            )
        },
        "rows": rows,
    }, ensure_ascii=False, indent=2) + "\n")


def build_row(engine, prefix, method: str, job_id: str, ledger) -> tuple[dict, dict | None]:
    started = time.perf_counter()
    try:
        state = engine.build(prefix, method, 1024, job_id)
        payload = state["payload"]
        memory = payload.get("summary") or payload.get("state_mem") or ""
        return ({
            "job_id": job_id,
            "stage": "build",
            "prefix_id": prefix.prefix_id,
            "method_id": method,
            "status": "complete",
            "elapsed_seconds": time.perf_counter() - started,
            "provider_calls": sum(row["job_id"] == job_id for row in ledger.rows),
            "memory_chars": len(memory),
            "resident_tokens": state["usage"].get("resident_tokens"),
            "ingest_eligible": state["usage"].get("ingest_eligible"),
            "state_hash": state["hash"],
        }, state)
    except Exception as exc:  # Probe records exact method/harness outcomes.
        return ({
            "job_id": job_id,
            "stage": "build",
            "prefix_id": prefix.prefix_id,
            "method_id": method,
            "status": "failed",
            "elapsed_seconds": time.perf_counter() - started,
            "provider_calls": sum(row["job_id"] == job_id for row in ledger.rows),
            "error": f"{type(exc).__name__}: {exc}",
        }, None)


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"refusing existing output: {OUTPUT}")
    verify_file_manifest(DATASET)
    config = load_config(BASE_CONFIG)
    config["models"][0].update(
        construction_max_output_tokens=CONSTRUCTION_MAX_OUTPUT_TOKENS,
        construction_submission_max_chars=CONSTRUCTION_SUBMISSION_MAX_CHARS,
        retrieval_transport="native_tool_call",
        retrieval_submission_max_chars=RETRIEVAL_SUBMISSION_MAX_CHARS,
    )
    config["ama_code_search"] = {"mode": "bwrap", "image": None}
    model = config["models"][0]
    model_id = model["id"]
    prefixes, queries, _ = load_suite_dataset(DATASET, config, ROOT)
    prefix_by_id = {prefix.prefix_id: prefix for prefix in prefixes}
    query_by_key = {(query.prefix_id, query.query_type): query for query in queries}

    OUTPUT.mkdir(parents=True)
    (OUTPUT / "config.snapshot.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    )
    counter = LocalTokenizer(model, ROOT)
    ledger = ServerLedger(OUTPUT, config, {model_id: counter}, set())
    engine = MemoryMethods(config, counter.count, ROOT, ledger.for_model(model_id))
    rows: list[dict] = []
    write_report(rows, complete=False)

    acon_prefixes = () if REPAIR_SCOPE == "ama_r3_only" else ACON_PREFIXES
    ama_queries = ({
        "controlled:parameter_schema:operations:R3:long": ("audit_chain",),
    } if REPAIR_SCOPE == "ama_r3_only" else AMA_QUERIES)

    for prefix_id in acon_prefixes:
        row, _ = build_row(
            engine, prefix_by_id[prefix_id], "acon_official",
            f"repair-smoke:{prefix_id}:acon_official", ledger,
        )
        rows.append(row)
        write_report(rows, complete=False)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    for prefix_id, query_types in ama_queries.items():
        build_id = f"repair-smoke:{prefix_id}:ama_official_bm25:build"
        row, state = build_row(
            engine, prefix_by_id[prefix_id], "ama_official_bm25", build_id, ledger,
        )
        rows.append(row)
        write_report(rows, complete=False)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if state is None:
            continue
        for query_type in query_types:
            job_id = f"repair-smoke:{prefix_id}:ama_official_bm25:{query_type}"
            started = time.perf_counter()
            try:
                artifact = engine.materialize(
                    state, prefix_by_id[prefix_id], query_by_key[(prefix_id, query_type)], job_id,
                )
                materialized = {
                    "job_id": job_id,
                    "stage": "materialize",
                    "prefix_id": prefix_id,
                    "query_type": query_type,
                    "method_id": "ama_official_bm25",
                    "status": "complete",
                    "elapsed_seconds": time.perf_counter() - started,
                    "send_eligible": artifact["retrieval_usage"]["send_eligible"],
                    "token_count": artifact["token_count"],
                    "read_event_ids": artifact["retrieval_usage"]["read_event_ids"],
                }
            except Exception as exc:  # Probe records exact method/harness outcomes.
                materialized = {
                    "job_id": job_id,
                    "stage": "materialize",
                    "prefix_id": prefix_id,
                    "query_type": query_type,
                    "method_id": "ama_official_bm25",
                    "status": "failed",
                    "elapsed_seconds": time.perf_counter() - started,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            rows.append(materialized)
            write_report(rows, complete=False)
            print(json.dumps(materialized, ensure_ascii=False), flush=True)

    write_report(rows, complete=True)


if __name__ == "__main__":
    main()
