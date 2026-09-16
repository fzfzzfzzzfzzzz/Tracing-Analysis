"""Freeze the one-task Qwen3 thinking attribution experiment.

Only an already exposed SWE-bench Lite task may be exported.  The source task,
image and hidden evaluator stay byte-for-byte identical except for the agent
step ceiling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


TARGET = "realcanary:swebench_lite:pytest-dev__pytest-5221"
ALREADY_EXPOSED = {
    "realcanary:swebench_lite:pytest-dev__pytest-5413",
    "realcanary:swebench_lite:pytest-dev__pytest-7220",
    TARGET,
}
PROTECTED_UNEXPOSED = [
    "realcanary:swebench_lite:pytest-dev__pytest-7490",
    "realcanary:swebench_lite:pytest-dev__pytest-5227",
    "realcanary:swebench_lite:pytest-dev__pytest-7168",
    "realcanary:swebench_lite:pytest-dev__pytest-5495",
    "realcanary:swebench_lite:pytest-dev__pytest-5103",
]
JSON_ACTION_TEXT = """Every response must use the required structured action with exactly two nonempty fields: `thought` for concise planning and `command` for one bash action. The harness will execute `command` and return its observation."""
NATIVE_ACTION_TEXT = """Every response must call the provided `bash` tool exactly once with one nonempty `command` argument. You may think before the tool call. The harness will execute that command and return its observation."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--source-tasks", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--config-output", type=Path, required=True)
    parser.add_argument("--task-output", type=Path, required=True)
    args = parser.parse_args()

    paths = [args.config_output, args.task_output]
    if any(path.exists() for path in paths):
        raise ValueError("refusing to overwrite a frozen attribution artifact")

    source_config = json.loads(args.source_config.read_text(encoding="utf-8"))
    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    if source_manifest["tasks_jsonl_sha256"] != sha256(args.source_tasks):
        raise ValueError("source canary task file differs from its manifest")
    if (source_config["history_budgets"] != [1536]
            or source_config["methods"] != ["full_history"]):
        raise ValueError("unexpected source experiment configuration")

    selected = None
    seen = set()
    with args.source_tasks.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            seen.add(row["id"])
            if row["id"] == TARGET:
                selected = row
    if selected is None or not ALREADY_EXPOSED <= seen or not set(PROTECTED_UNEXPOSED) <= seen:
        raise ValueError("source task inventory differs")
    if selected["max_model_calls"] != 12:
        raise ValueError("source task step ceiling differs")

    task = dict(selected)
    task["max_model_calls"] = 40
    if JSON_ACTION_TEXT not in task["system_prompt"]:
        raise ValueError("source task action prompt differs")
    task["system_prompt"] = task["system_prompt"].replace(
        JSON_ACTION_TEXT, NATIVE_ACTION_TEXT
    ).replace(
        "In a separate final response, set `command` to only:",
        "To submit, call the `bash` tool once with `command` exactly:",
    )

    config = json.loads(json.dumps(source_config))
    config["history_budgets"] = [16384]
    for model in config["models"]:
        model["display_name"] = (
            "Qwen/Qwen3-14B pytest-5221 thinking attribution b16384 s40 r1"
        )
        model["enable_thinking"] = True
        model["thinking_kinds"] = ["mini_action", "mini_format_repair"]
        model["thinking_sampling"] = {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
        }
        model["action_transport"] = "native_tool_call"

    args.config_output.parent.mkdir(parents=True, exist_ok=True)
    args.task_output.mkdir(parents=True, exist_ok=False)
    write_json(args.config_output, config)
    tasks_path = args.task_output / "tasks.jsonl"
    tasks_path.write_text(
        json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_json(args.task_output / "source_manifest.json", {
        "schema_version": "tracegraph_qwen3_thinking_attribution_v1",
        "frozen_at": "2026-09-15",
        "development_only": True,
        "independent_validation": False,
        "purpose": "attribute pytest-5221 failure under stronger inference settings",
        "source_config": str(args.source_config),
        "source_config_sha256": sha256(args.source_config),
        "source_tasks": str(args.source_tasks),
        "source_tasks_sha256": sha256(args.source_tasks),
        "source_manifest": str(args.source_manifest),
        "source_manifest_sha256": sha256(args.source_manifest),
        "selected_task_id": TARGET,
        "selected_task_was_already_exposed": True,
        "controlled_changes": {
            "enable_thinking": [False, True],
            "thinking_scope": ["all prior non-thinking requests", "mini agent requests only"],
            "history_budget_tokens": [1536, 16384],
            "max_model_calls": [12, 40],
            "action_transport": ["json_schema", "native_tool_call"],
            "action_prompt": ["thought/command JSON", "one native bash tool call"],
            "agent_sampling": [
                "temperature=0 greedy",
                "Qwen3 README: temperature=0.6, top_p=0.95, top_k=20, min_p=0",
            ],
        },
        "held_constant": [
            "model weights and tokenizer revision",
            "vLLM runtime receipt and launch arguments",
            "SWE-bench image digest",
            "problem statement",
            "hidden test patch and evaluator command",
            "memory method full_history",
            "sampling temperature and seed",
        ],
        "protected_unexposed_task_ids": PROTECTED_UNEXPOSED,
        "protected_tasks_model_requests": 0,
        "tasks_jsonl_sha256": sha256(tasks_path),
        "config_sha256": sha256(args.config_output),
    })
    print(json.dumps({
        "config": str(args.config_output),
        "tasks": str(tasks_path),
        "task_id": TARGET,
        "protected_unexposed": len(PROTECTED_UNEXPOSED),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
