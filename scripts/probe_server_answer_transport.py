"""Send one recorded audit-answer request to validate a server transport.

This is deliberately a one-shot diagnostic: the durable provider ledger records
the request before sending, and the script never retries an uncertain outcome.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from tracegraph.benchmark.compression_audit.development_adapters import event_record
from tracegraph.benchmark.compression_audit.development_experiment import answer_request
from tracegraph.benchmark.compression_audit.development_protocol import (
    normalize_server_submission,
)
from tracegraph.benchmark.compression_audit.io import load_jsonl
from tracegraph.benchmark.server_eval.config import (
    LocalTokenizer,
    answer_response_format,
    load_config,
)
from tracegraph.benchmark.server_eval.data_workflow import load_suite_dataset
from tracegraph.benchmark.server_eval.provider import ServerLedger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.output.exists():
        raise ValueError("probe output must be a new directory")
    workspace = args.workspace.resolve()
    config = load_config(args.config)
    models = {model["id"]: model for model in config["models"]}
    model = models[args.model_id]
    prefixes, queries, _ = load_suite_dataset(args.dataset, config, workspace)
    prefix_map = {prefix.prefix_id: prefix for prefix in prefixes}
    query_map = {query.query_id: query for query in queries}
    trials = load_jsonl(args.prepared / "trials.jsonl")
    trial = next(
        row
        for row in trials
        if row["phase"] == "main"
        and row["model_id"] == args.model_id
        and row["method_id"] == "full_history"
        and row["query_type"] == "audit_chain"
    )
    rubrics = {
        row["query_id"]: row for row in load_jsonl(args.prepared / "rubrics.jsonl")
    }
    prefix = prefix_map[trial["prefix_id"]]
    query = query_map[trial["query_id"]]
    rubric = rubrics[query.query_id]
    records = [event_record(event) for event in prefix.events]
    template = answer_request(
        query,
        records,
        response_format=answer_response_format(model),
        server_field_transport=True,
        server_tool_transport=model.get("answer_transport") == "native_tool_call",
    )
    args.output.mkdir(parents=True)
    counters = {
        item["id"]: LocalTokenizer(item, workspace) for item in config["models"]
    }
    ledger = ServerLedger(args.output, config, counters, set())
    response = ledger.call(
        args.model_id,
        template,
        job_id="answer-transport-probe:" + trial["episode_id"],
        kind="answer",
    )
    message = response["choices"][0]["message"]
    content = message.get("content")
    thinking_match = re.search(
        r"<think>\s*(.*?)\s*</think>", content or "", flags=re.DOTALL
    )
    parse_error = None
    try:
        normalize_server_submission(
            response,
            sorted(rubric["strict_values"]),
            sorted(rubric["necessary_facts"]),
        )
        submission_valid = True
    except (ValueError, KeyError, TypeError, IndexError) as error:
        submission_valid = False
        parse_error = str(error)
    result = {
        "format": "server_answer_transport_probe_v1",
        "model_id": args.model_id,
        "episode_id": trial["episode_id"],
        "prefix_id": trial["prefix_id"],
        "query_id": trial["query_id"],
        "thinking_present": bool(thinking_match and thinking_match.group(1).strip()),
        "submission_tool_called": bool(message.get("tool_calls")),
        "submission_valid": submission_valid,
        "parse_error": parse_error,
        "provider_requests": 1,
        "automatic_retry": False,
    }
    (args.output / "probe_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
