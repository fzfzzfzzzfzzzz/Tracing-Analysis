#!/usr/bin/env python3
"""Run one frozen AppWorld canary through the pinned ACON Full History runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ACON_ROOT = Path("/home/fangc/acon-d63f9ae18959")
ACON_COMMIT = "d63f9ae18959dc7215ff62899c94c5e8c56847ae"
APPWORLD_ROOT = Path("/home/fangc/appworld-42b5bcf3cd33")
APPWORLD_COMMIT = "42b5bcf3cd334fee33f0c37c02070a9f5807add5"
APPWORLD_RUN_ROOT = ACON_ROOT / "experiments" / "appworld"
OUTPUT_ROOT = Path("/data/fangc/outputs/appworld_external_canary_260918")
TRACEGRAPH_ROOT = Path("/home/fangc/tracegraph-appworld-memory-260918")
MODEL = "Qwen3.8-27B-rev-1d4bf0f"
SEED = 20260918
CANARY_IDS = {
    "23cf851_1",
    "6c2c621_1",
    "3ab5b8b_2",
    "383cbac_1",
    "50e1ac9_1",
}


def git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def utc_now() -> str:
    # AppWorld freezes Python's wall clock to the task date. Ask the host clock
    # in a subprocess so provenance timestamps remain real.
    return subprocess.check_output(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%S.%NZ"], text=True
    ).strip()


def real_monotonic() -> float:
    # time.monotonic() is also frozen inside AppWorld's task context.
    return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def messages_sha256(messages: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def install_transport_patch(receipt_path: Path) -> None:
    import productive_agents.llm as llm_module

    vLLM = llm_module.vLLM
    # ACON's fallback price lookup assumes a gpt-4o entry that is absent in the
    # pinned revision. Local model dollar cost is unknown/zero; token usage is
    # retained in the provider receipts and must remain the primary cost metric.
    llm_module.calculate_api_cost = lambda model_name, input_tokens, output_tokens: 0.0

    def generate(
        self: Any,
        prompt: Any,
        n: int = 1,
        max_tokens: int = 8192,
        temperature: float | None = None,
        seed: int | None = None,
        **_: Any,
    ) -> Any:
        messages = self._build_messages(prompt)
        request_seed = SEED if seed is None else int(seed)
        attempts: list[dict[str, Any]] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        started = real_monotonic()

        for attempt in range(2):
            attempt_seed = request_seed + attempt
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                n=n,
                max_tokens=8192,
                temperature=1.0,
                top_p=0.95,
                seed=attempt_seed,
                extra_body={
                    "top_k": 20,
                    "min_p": 0.0,
                    "chat_template_kwargs": {"enable_thinking": True},
                },
                timeout=900.0,
            )
            usage = getattr(response, "usage", None)
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_prompt_tokens += prompt_tokens
            total_completion_tokens += completion_tokens
            contents = [(choice.message.content or "") for choice in response.choices]
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "seed": attempt_seed,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "finish_reasons": [choice.finish_reason for choice in response.choices],
                    "empty": not any(content.strip() for content in contents),
                }
                )
            if any(content.strip() for content in contents):
                self._update_token_usage(total_prompt_tokens, total_completion_tokens)
                append_jsonl(
                    receipt_path,
                    {
                        "schema_version": "appworld_provider_usage_v1",
                        "timestamp": utc_now(),
                        "model": self.model_name,
                        "settings": {
                            "context_window": 65536,
                            "max_tokens": 8192,
                            "temperature": 1.0,
                            "top_p": 0.95,
                            "top_k": 20,
                            "min_p": 0.0,
                            "enable_thinking": True,
                        },
                        "request_sha256": messages_sha256(messages),
                        "request_message_count": len(messages),
                        "attempts": attempts,
                        "prompt_tokens": total_prompt_tokens,
                        "completion_tokens": total_completion_tokens,
                        "elapsed_seconds": round(real_monotonic() - started, 3),
                    },
                )
                return contents if n > 1 else contents[0]

        self._update_token_usage(total_prompt_tokens, total_completion_tokens)
        append_jsonl(
            receipt_path,
            {
                "schema_version": "appworld_provider_usage_v1",
                "timestamp": utc_now(),
                "model": self.model_name,
                "settings": {
                    "context_window": 65536,
                    "max_tokens": 8192,
                    "enable_thinking": True,
                },
                "request_sha256": messages_sha256(messages),
                "request_message_count": len(messages),
                "attempts": attempts,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "elapsed_seconds": round(real_monotonic() - started, 3),
                "error": "empty_response_after_retry",
            },
        )
        raise RuntimeError("Qwen returned an empty response twice")

    vLLM.generate = generate


def install_tracegraph_memory_patch(
    *,
    budget: int,
    trace_path: Path,
    task_id: str,
) -> None:
    """Replace only ACON's model-facing history projection."""

    if not TRACEGRAPH_ROOT.is_dir():
        raise RuntimeError(f"TraceGraph bundle not found: {TRACEGRAPH_ROOT}")
    sys.path.insert(0, str(TRACEGRAPH_ROOT / "src"))
    from tracegraph.integrations.appworld_memory import AppWorldTraceGraphMemory
    import productive_agents.agents.memory as memory_module
    import productive_agents.agents.unified_agent as unified_agent_module

    original = memory_module.MemoryManager

    class TraceGraphMemoryManager(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._tracegraph_projection = AppWorldTraceGraphMemory(
                budget=budget,
                trace_path=trace_path,
                session_id=f"appworld_{task_id}",
            )

        def get_conversation_history(
            self, exclude_system: bool = True
        ) -> list[dict[str, str]]:
            current = [dict(message) for message in self.get_current_session()]
            system: list[dict[str, str]] = []
            if current and current[0].get("role") == "system":
                system = [current[0]]
                current = current[1:]
            projected = self._tracegraph_projection.project(current)
            return projected if exclude_system else [*system, *projected]

    memory_module.MemoryManager = TraceGraphMemoryManager
    unified_agent_module.MemoryManager = TraceGraphMemoryManager


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default="23cf851_1", choices=sorted(CANARY_IDS))
    parser.add_argument("--run-label", default="full_history_qwen38_v1")
    parser.add_argument(
        "--method", choices=("full_history", "tracegraph"), default="full_history"
    )
    parser.add_argument("--budget", type=int, default=8192)
    args = parser.parse_args()

    if git_head(ACON_ROOT) != ACON_COMMIT:
        raise RuntimeError("ACON source commit mismatch")
    if git_head(APPWORLD_ROOT) != APPWORLD_COMMIT:
        raise RuntimeError("AppWorld source commit mismatch")

    output_dir = OUTPUT_ROOT / args.run_label / f"task_{args.task_id}"
    output_dir.mkdir(parents=True, exist_ok=False)
    usage_path = output_dir / "provider_usage.jsonl"

    sys.path.insert(0, str(APPWORLD_RUN_ROOT))
    os.chdir(APPWORLD_RUN_ROOT)
    install_transport_patch(usage_path)
    if args.method == "tracegraph":
        install_tracegraph_memory_patch(
            budget=args.budget,
            trace_path=output_dir / "tracegraph_context_views.jsonl",
            task_id=args.task_id,
        )
    from run import main as run_appworld_task

    experiment_name = f"qwen38_{args.run_label}"
    run_started = utc_now()
    results = run_appworld_task(
        task_id=args.task_id,
        split="dev",
        output_dir=str(output_dir),
        exp_config={
            "debug_mode": False,
            "max_iter": 50,
            "experiment_name": experiment_name,
            "prompt_file": "./prompts/prompts_v1.json",
            "co_config": None,
            "seed": SEED,
            "temperature": 1.0,
            "max_tokens": 8192,
            "use_thinking": True,
        },
        model_name=MODEL,
        debug_mode=False,
        experiment_name=experiment_name,
        max_iter=50,
    )

    evaluation = subprocess.run(
        [
            "appworld",
            "evaluate",
            experiment_name,
            "--task-id",
            args.task_id,
            "--root",
            str(APPWORLD_RUN_ROOT),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    receipt = {
        "schema_version": "appworld_canary_run_receipt_v1",
        "started_at": run_started,
        "finished_at": utc_now(),
        "task_id": args.task_id,
        "split": "dev",
        "method": args.method,
        "context_budget": args.budget if args.method == "tracegraph" else None,
        "model": MODEL,
        "source": {"acon": ACON_COMMIT, "appworld": APPWORLD_COMMIT},
        "results": results,
        "official_evaluation": {
            "returncode": evaluation.returncode,
            "stdout": evaluation.stdout,
            "stderr": evaluation.stderr,
        },
    }
    (output_dir / "run_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if evaluation.returncode == 0 else evaluation.returncode


if __name__ == "__main__":
    raise SystemExit(main())
