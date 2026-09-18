"""Run the pinned stock mini-SWE-agent v2.4.6 harness on the exposed pytest task."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from minisweagent.agents import get_agent
from minisweagent.config import builtin_config_dir, get_config_from_spec
from minisweagent.exceptions import Submitted
from minisweagent.models import get_model
from minisweagent.run.benchmarks.swebench import get_sb_environment
from minisweagent.utils.serialize import recursive_merge


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TASK_ID = "realcanary:swebench_lite:pytest-dev__pytest-5221"
EXPECTED_IMAGE = (
    "ghcr.io/epoch-research/swe-bench.eval.x86_64.pytest-dev__pytest-5221@"
    "sha256:798111aafcff014e884ce2dbef8661b850ec0f69abeda32bdf6f5d8e74d6ef10"
)
EXPECTED_SOURCE_REVISION = "a83fcae82d2a08f0ee0c688f9d137b3566c097f8"


def stable_digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_task(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    matches = [row for row in rows if row.get("id") == EXPECTED_TASK_ID]
    if len(matches) != 1:
        raise ValueError("task file must contain exactly the exposed pytest-5221 task")
    task = matches[0]
    if task.get("image") != EXPECTED_IMAGE:
        raise ValueError("pytest-5221 image digest changed")
    return task


def load_config(overlay: Path, seed: int, trajectory: Path | None = None) -> dict:
    base = get_config_from_spec(str(builtin_config_dir / "benchmarks" / "swebench.yaml"))
    custom = get_config_from_spec(str(overlay))
    runtime = {
        "agent": {
            "agent_class": "interactive",
            "mode": "yolo",
            "confirm_exit": False,
            "output_path": trajectory,
        },
        "model": {"model_kwargs": {"seed": seed}},
    }
    return recursive_merge(base, custom, runtime)


def source_revision() -> str:
    source = ROOT / "vendor" / "mini-swe-a83fcae"
    return subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()


def server_identity() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def response_finish_reason(message: dict) -> str | None:
    response = message.get("extra", {}).get("response")
    try:
        return response["choices"][0]["finish_reason"]
    except (KeyError, IndexError, TypeError):
        return None


def run_probe(config: dict, output: Path) -> None:
    if output.exists():
        raise ValueError("probe output must be new")
    output.mkdir(parents=True)
    model = get_model(config=config["model"])
    messages = [
        model.format_message(role="system", content="Every response must use the bash tool."),
        model.format_message(
            role="user", content="Call bash exactly once with command `printf FIRST`."
        ),
    ]
    first = model.query(messages)
    first_actions = first.get("extra", {}).get("actions", [])
    observations = model.format_observation_messages(
        first,
        [{"output": "FIRST", "returncode": 0, "exception_info": ""}],
        {},
    )
    messages.extend([first, *observations])
    messages.append(
        model.format_message(
            role="user", content="Now call bash exactly once with command `printf SECOND`."
        )
    )
    second = model.query(messages)
    second_actions = second.get("extra", {}).get("actions", [])
    report = {
        "pass": (
            len(first_actions) == 1
            and first_actions[0].get("command") == "printf FIRST"
            and len(observations) == 1
            and observations[0].get("role") == "tool"
            and bool(observations[0].get("tool_call_id"))
            and len(second_actions) == 1
            and second_actions[0].get("command") == "printf SECOND"
        ),
        "first_action": first_actions,
        "second_action": second_actions,
        "observation_role": observations[0].get("role") if observations else None,
        "history_roles": [row.get("role") for row in messages],
        "first_finish_reason": response_finish_reason(first),
        "second_finish_reason": response_finish_reason(second),
        "first_has_native_tool_calls": bool(first.get("tool_calls")),
        "second_has_native_tool_calls": bool(second.get("tool_calls")),
        "development_only": True,
        "independent_validation": False,
    }
    write_json(output / "report.json", report)
    if not report["pass"]:
        raise RuntimeError("stock native-history probe failed")


def run_task(config: dict, task: dict, output: Path, seed: int) -> None:
    if output.exists():
        raise ValueError("run output must be new")
    output.mkdir(parents=True)
    instance = {
        "instance_id": "pytest-dev__pytest-5221",
        "problem_statement": task["task"],
        "image_name": task["image"],
    }
    env = get_sb_environment(config, instance)
    model = get_model(config=config["model"])
    agent = get_agent(model, env, config["agent"], default_type="interactive")
    raw_execute = env.execute
    action_log: list[dict] = []

    def instrumented_execute(action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        started = time.time()
        submitted: Submitted | None = None
        try:
            result = raw_execute(action, cwd, timeout=timeout)
        except Submitted as exc:
            submitted = exc
            result = {"output": "", "returncode": 0, "exception_info": "Submitted"}
        status = raw_execute(
            {"command": "git status --porcelain=v1 --untracked-files=all -- ."},
            cwd,
            timeout=60,
        )
        action_log.append(
            {
                "model_call": agent.n_calls,
                "command": action.get("command", ""),
                "returncode": result.get("returncode"),
                "elapsed_seconds": time.time() - started,
                "status_after": status.get("output", ""),
            }
        )
        if submitted is not None:
            raise submitted
        return result

    env.execute = instrumented_execute
    run_error = None
    result: dict = {}
    try:
        result = agent.run(task["task"])
    except Exception as exc:  # stock agent has already serialized the traceback
        run_error = f"{type(exc).__name__}: {exc}"
    finally:
        env.execute = raw_execute

    final_status = raw_execute(
        {"command": "git status --porcelain=v1 --untracked-files=all -- ."}, timeout=60
    )
    final_diff = raw_execute({"command": "git diff --binary HEAD -- ."}, timeout=60)
    evaluation = raw_execute({"command": task["evaluator_command"]}, timeout=60)
    (output / "final_status.txt").write_text(final_status.get("output", ""), encoding="utf-8")
    (output / "final_diff.patch").write_text(final_diff.get("output", ""), encoding="utf-8")
    (output / "evaluation.txt").write_text(evaluation.get("output", ""), encoding="utf-8")
    with (output / "actions.jsonl").open("w", encoding="utf-8") as handle:
        for row in action_log:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    messages = agent.messages
    assistant_messages = [
        row for row in messages if row.get("role") == "assistant"
        and row.get("extra", {}).get("actions")
    ]
    commands = [row["command"] for row in action_log]
    first_edit = next((row for row in action_log if row["status_after"].strip()), None)
    finish_reasons = [response_finish_reason(row) for row in messages]
    metrics = {
        "seed": seed,
        "exit_status": result.get("exit_status") or messages[-1].get("extra", {}).get("exit_status"),
        "run_error": run_error,
        "model_calls": agent.n_calls,
        "assistant_tool_responses": len(assistant_messages),
        "valid_tool_response_ratio": len(assistant_messages) / agent.n_calls if agent.n_calls else 0,
        "tool_actions": len(action_log),
        "finish_reason_length": sum(reason == "length" for reason in finish_reasons),
        "format_error_messages": sum(
            row.get("extra", {}).get("interrupt_type") == "FormatError" for row in messages
        ),
        "first_edit_model_call": first_edit.get("model_call") if first_edit else None,
        "ran_test_command": any(
            re.search(r"(?:^|[;&|]\s*|\s)(?:(?:python|python3)\s+-m\s+)?(?:pytest|tox|nox|unittest)(?:\s|$)", command)
            for command in commands
        ),
        "repeated_command_ratio": (
            1 - len(set(commands)) / len(commands) if commands else 0
        ),
        "submitted_nonempty_patch": bool(result.get("submission", "").strip()),
        "nonempty_worktree": bool(final_status.get("output", "").strip()),
        "nonempty_tracked_diff": bool(final_diff.get("output", "").strip()),
        "hidden_test_returncode": evaluation.get("returncode"),
        "hidden_test_pass": evaluation.get("returncode") == 0,
        "development_only": True,
        "independent_validation": False,
    }
    write_json(output / "metrics.json", metrics)
    write_json(
        output / "identity.json",
        {
            "mini_swe_agent_version": importlib.metadata.version("mini-swe-agent"),
            "mini_swe_agent_revision": source_revision(),
            "litellm_version": importlib.metadata.version("litellm"),
            "openai_version": importlib.metadata.version("openai"),
            "config_hash": stable_digest(config),
            "task_hash": stable_digest(task),
            "image": task["image"],
            "server_identity": server_identity(),
            "seed": seed,
        },
    )
    if env.container_id:
        subprocess.run(["docker", "rm", "-f", env.container_id], capture_output=True, check=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument(
        "--overlay",
        type=Path,
        default=ROOT / "configs" / "minisweagent_v2_4_6_qwen38_27b_stock_pytest5221_260917.yaml",
    )
    parser.add_argument(
        "--task-file",
        type=Path,
        default=ROOT / "data" / "real_canary" / "pytest5221_guarded_attribution_260917_r1" / "tasks.jsonl",
    )
    args = parser.parse_args()
    revision = source_revision()
    if revision != EXPECTED_SOURCE_REVISION:
        raise ValueError(f"unexpected mini-SWE-agent revision: {revision}")
    trajectory = None if args.probe else args.output / "trajectory.json"
    config = load_config(args.overlay.resolve(), args.seed, trajectory)
    if args.probe:
        run_probe(config, args.output.resolve())
    else:
        run_task(config, load_task(args.task_file.resolve()), args.output.resolve(), args.seed)


if __name__ == "__main__":
    main()
