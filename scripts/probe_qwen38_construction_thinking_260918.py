"""Replay six saved failed/weak construction requests with construction-only thinking.

This is a transport/generation probe, not a benchmark score.  It preserves each
saved prompt, model, token ceiling, and seed, changing only the thinking flag and
the Qwen3.8 thinking sampling parameters.
"""

from __future__ import annotations

import copy
import json
import time
import urllib.request
from pathlib import Path


SOURCE = Path("/data/fangc/qwen38_27b_unified_methods_b1024_260918_r1")
OUTPUT = Path("/data/fangc/probes/qwen38_construction_thinking_260918_r3.json")
ENDPOINT = "http://127.0.0.1:8001/v1/chat/completions"
PREFIXES = (
    "controlled:parameter_schema:software:R0:long",
    "controlled:parameter_schema:data:R2:long",
)
METHODS = ("rolling_summary", "acon_official", "ama_official_bm25")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def message(response: dict) -> dict:
    choices = response.get("choices") or []
    return choices[0].get("message") or {} if choices else {}


def post(body: dict) -> tuple[dict, float]:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
        headers={"Authorization": "Bearer local-self-hosted", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode())
    return payload, time.perf_counter() - started


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"refusing existing output: {OUTPUT}")
    attempts = [
        row for row in load_jsonl(SOURCE / "provider_attempts.jsonl")
        if row.get("kind") == "construction"
    ]
    ledger = [
        row for row in load_jsonl(SOURCE / "provider_ledger.jsonl")
        if row.get("kind") == "construction"
    ]
    if len(attempts) != len(ledger) or any(
        attempt["job_id"] != result["job_id"]
        for attempt, result in zip(attempts, ledger, strict=True)
    ):
        raise SystemExit("saved construction attempts and responses do not align")

    selected: list[tuple[dict, dict]] = []
    for prefix in PREFIXES:
        for method in METHODS:
            job_id = f"build:qwen38_27b-b1024:{prefix}:{method}"
            candidates = [
                pair for pair in zip(attempts, ledger, strict=True)
                if pair[0]["job_id"] == job_id
            ]
            if not candidates:
                raise SystemExit(f"missing saved request: {job_id}")
            null_candidates = [
                pair for pair in candidates if message(pair[1]["response"]).get("content") is None
            ]
            selected.append((null_candidates or candidates)[0])

    rows = []
    for attempt, original in selected:
        body = copy.deepcopy(attempt["request"])
        body["chat_template_kwargs"] = {"enable_thinking": True}
        body.update(temperature=1.0, top_p=0.95, top_k=20, min_p=0)
        response, latency = post(body)
        old_message = message(original["response"])
        new_message = message(response)
        rows.append({
            "source_attempt_hash": attempt["hash"],
            "job_id": attempt["job_id"],
            "changed_fields": {
                "chat_template_kwargs.enable_thinking": True,
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 20,
                "min_p": 0,
            },
            "original": {
                "completion_tokens": original.get("completion_tokens"),
                "content": old_message.get("content"),
                "reasoning_content": old_message.get("reasoning_content"),
                "finish_reason": (original["response"].get("choices") or [{}])[0].get("finish_reason"),
            },
            "probe_request": body,
            "probe_response": response,
            "probe_latency_seconds": latency,
            "probe_summary": {
                "content_present": bool(new_message.get("content")),
                "content_chars": len(new_message.get("content") or ""),
                "reasoning_chars": len(new_message.get("reasoning_content") or ""),
                "completion_tokens": (response.get("usage") or {}).get("completion_tokens"),
                "finish_reason": (response.get("choices") or [{}])[0].get("finish_reason"),
            },
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "purpose": "construction-only thinking probe; not a benchmark score",
        "source_run": str(SOURCE),
        "endpoint": ENDPOINT,
        "rows": rows,
    }, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "output": str(OUTPUT),
        "rows": [
            {"job_id": row["job_id"], **row["probe_summary"]}
            for row in rows
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
