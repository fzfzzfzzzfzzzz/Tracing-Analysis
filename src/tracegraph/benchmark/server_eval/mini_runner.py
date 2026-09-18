"""Explicit real-environment entry point; no automatic server or container startup."""

import json
from pathlib import Path

from ..compression_audit.development_experiment import development_implementation, write_json
from ..compression_audit.io import file_sha256, stable_digest, load_jsonl
from .config import load_config, live_blockers, LocalTokenizer, EmbeddingTokenizer
from .external import source_status
from .methods import MemoryMethods
from .mini import (
    CheckpointEnvironment, MiniModel, native_action_tools, official_agent,
    parse_native_action_response, run_task, validate_restore_source,
)
from .provider import ServerLedger


def prepare_mini(config_path, tasks_path, output, workspace):
    config = load_config(config_path)
    tasks = load_jsonl(tasks_path)
    if output.exists() or not tasks or len({t["id"] for t in tasks}) != len(tasks):
        raise ValueError("unique tasks and new output required")
    for task in tasks:
        for key in ("id", "image", "cwd", "task", "system_prompt", "evaluator_command", "state_contract"):
            if not isinstance(task[key], str) or not task[key]:
                raise ValueError("incomplete real task")
        if type(task["max_model_calls"]) is not int or not 1 <= task["max_model_calls"] <= 100:
            raise ValueError("max_model_calls must be between 1 and 100")
        guardrails = task.get("agent_guardrails", {})
        boolean_guardrails = {
            "block_empty_diff_submission", "require_non_test_source_change",
            "require_clean_diff_check", "require_successful_test_on_current_diff",
            "block_repeated_failed_command",
        }
        allowed_guardrails = boolean_guardrails | {"stagnation_nudge_after"}
        if not isinstance(guardrails, dict) or set(guardrails) - allowed_guardrails:
            raise ValueError("agent_guardrails contains unsupported fields")
        if any(type(guardrails[key]) is not bool for key in set(guardrails) & boolean_guardrails):
            raise ValueError("agent_guardrails boolean fields must be booleans")
        threshold = guardrails.get("stagnation_nudge_after")
        if threshold is not None and (type(threshold) is not int or not 2 <= threshold <= 50):
            raise ValueError("stagnation_nudge_after must be an integer between 2 and 50")
    calls = sum(t["max_model_calls"] for t in tasks)
    multiplier = len(config["models"]) * len(config["history_budgets"])
    upper = multiplier * sum(calls * (1 + config["limits"]["build_calls_per_prefix"]
        + config["limits"]["retrieve_calls_per_query"]
        + (config["limits"].get("embedding_calls_per_prefix", 512) + 1
           if method == "ama_official_embedding" else 0)) for method in config["methods"])
    output.mkdir(parents=True)
    write_json(output / "config.snapshot.json", config)
    write_json(output / "tasks.snapshot.json", tasks)
    result = {"config_hash": stable_digest(config), "tasks_hash": stable_digest(tasks),
        "task_file_sha256": file_sha256(tasks_path), "request_upper_bound": upper,
        "blockers": live_blockers(config, workspace) + source_status(config, workspace),
        "execution": "one explicitly selected task/model/method/budget per invocation",
        "real_tasks_executed": 0, "provider_requests": 0,
        "development_only": True, "independent_validation": False}
    write_json(output / "plan.json", result)
    return result


def calibrate_mini_agent(config_path: Path, output: Path, workspace: Path, *, model_id: str,
                         execute=False, transport=None, counters=None):
    """Calibrate the exact thinking plus native-bash transport used by mini tasks."""

    if not execute:
        raise ValueError("mini agent calibration requires explicit --execute")
    if output.exists():
        raise ValueError("new mini agent calibration output required")
    config = load_config(config_path)
    blockers = live_blockers(config, workspace) + source_status(config, workspace)
    if blockers:
        raise ValueError("mini agent calibration blocked: " + str(blockers))
    model = next((row for row in config["models"] if row["id"] == model_id), None)
    if model is None:
        raise ValueError("unknown model slot")
    if (model.get("action_transport") != "native_tool_call"
            or not model.get("enable_thinking")
            or (model.get("thinking_kinds") is not None
                and "mini_action" not in model["thinking_kinds"])):
        raise ValueError("mini agent calibration requires thinking native tool actions")

    counters = counters or {row["id"]: LocalTokenizer(row, workspace)
                            for row in config["models"]}
    output.mkdir(parents=True)
    ledger = ServerLedger(output, config, counters, set(), transport=transport)
    response = ledger.for_model(model_id).call({
        "messages": [
            {"role": "system", "content": (
                "You are a software agent. Every response must call bash exactly once."
            )},
            {"role": "user", "content": (
                "Call the bash tool exactly once with command `printf OK`. Do not answer in text."
            )},
        ],
        "tools": native_action_tools(),
        "tool_choice": "auto",
    }, job_id="mini-agent-transport-probe", kind="mini_action")
    message = response.get("choices", [{}])[0].get("message", {})
    reasoning = (message.get("reasoning_content") or message.get("reasoning")
                 or message.get("content") or "")
    error = None
    try:
        action = parse_native_action_response(response)
    except ValueError as exc:
        action, error = {"command": "", "thought": ""}, str(exc)
    passed = bool(reasoning.strip()) and action["command"] == "printf OK" and error is None
    identity = {
        "calibration_kind": "mini_agent_transport_v1",
        "config_hash": stable_digest(config),
        "implementation_digest": development_implementation(workspace)["digest"],
        "model_id": model_id,
        "served_model": model["served_model"],
    }
    report = {
        "mode": "live",
        "calibration_kind": "mini_agent_transport_v1",
        "pass": passed,
        "model_id": model_id,
        "served_model": model["served_model"],
        "action_transport": model["action_transport"],
        "thinking_returned": bool(reasoning.strip()),
        "reasoning_chars": len(reasoning),
        "tool_name": "bash" if action["command"] else None,
        "command": action["command"],
        "parse_error": error,
        "provider_requests": len(ledger.rows),
        "development_only": True,
        "independent_validation": False,
    }
    write_json(output / "identity.json", identity)
    write_json(output / "report.json", report)
    return report


def validate_mini_calibration(calibration: Path, config: dict, workspace: Path, model_id: str,
                              budget: int):
    report = json.loads((calibration / "report.json").read_text(encoding="utf-8"))
    identity = json.loads((calibration / "identity.json").read_text(encoding="utf-8"))
    if report.get("calibration_kind") == "mini_agent_transport_v1":
        rows = load_jsonl(calibration / "provider_ledger.jsonl")
        expected = {
            "calibration_kind": "mini_agent_transport_v1",
            "config_hash": stable_digest(config),
            "implementation_digest": development_implementation(workspace)["digest"],
            "model_id": model_id,
            "served_model": next(row["served_model"] for row in config["models"]
                                 if row["id"] == model_id),
        }
        if (identity != expected or report.get("mode") != "live" or not report.get("pass")
                or report.get("model_id") != model_id or report.get("command") != "printf OK"
                or report.get("tool_name") != "bash" or not report.get("thinking_returned")
                or len(rows) != 1 or not rows[0]["valid_usage"]
                or stable_digest(rows[0]["response"]) != rows[0]["response_hash"]):
            raise ValueError("mini agent transport calibration identity or gate failed")
        return
    cell = next((row for row in report["cells"]
                 if row["cell_id"] == f"{model_id}-b{budget}"), None)
    if (report["mode"] != "live" or not cell or not cell["calibration"]["pass"]
            or identity["config_hash"] != stable_digest(config)):
        raise ValueError("audit calibration identity or gate failed")


def execute_mini(prepared: Path, output: Path, workspace: Path, *, task_id, model_id, method,
                 budget, execute=False, restore: Path | None = None, calibration: Path | None = None,
                 memory_ledger: Path | None = None, memory_revision: Path | None = None,
                 memory_budget: int = 0, replay_parent_revision_id: str | None = None):
    if not execute:
        raise ValueError("mini execution requires explicit --execute")
    config = load_config(prepared / "config.snapshot.json")
    tasks = json.loads((prepared / "tasks.snapshot.json").read_text(encoding="utf-8"))
    plan = json.loads((prepared / "plan.json").read_text(encoding="utf-8"))
    if plan["config_hash"] != stable_digest(config) or plan["tasks_hash"] != stable_digest(tasks):
        raise ValueError("mini plan changed")
    if output.exists() or method not in config["methods"] or budget not in config["history_budgets"]:
        raise ValueError("new output and a frozen method/budget required")
    revision = renderer = None
    if memory_ledger is not None and memory_revision is not None:
        raise ValueError("choose either a memory ledger or a revision snapshot")
    memory_source = memory_ledger or memory_revision
    if memory_source is not None:
        from ..recursive_memory import load_ledger_snapshot, load_revision_snapshot
        from ..recursive_memory_plugins import DeterministicMemoryRenderer
        revision = (load_ledger_snapshot(memory_ledger).current
                    if memory_ledger is not None else load_revision_snapshot(memory_revision))
        if type(memory_budget) is not int or not 0 < memory_budget < budget:
            raise ValueError("revision memory requires a positive sub-budget below total budget")
        renderer = DeterministicMemoryRenderer()
    elif memory_budget:
        raise ValueError("memory budget requires a ledger snapshot")
    blockers = live_blockers(config, workspace) + source_status(config, workspace)
    if blockers:
        raise ValueError("mini live preflight blocked: " + str(blockers))
    if calibration is None:
        raise ValueError("real environment run requires a passed audit calibration directory")
    validate_mini_calibration(calibration, config, workspace, model_id, budget)
    task = next(t for t in tasks if t["id"] == task_id)
    checkpoint = json.loads(restore.read_text(encoding="utf-8")) if restore else None
    if checkpoint:
        validate_restore_source(restore.parent, checkpoint)
        old_identity = json.loads((restore.parent / "identity.json").read_text(encoding="utf-8"))
        if any(old_identity[k] != v for k, v in {"model_id": model_id, "method": method,
                "budget": budget, "plan_hash": stable_digest(plan)}.items()):
            raise ValueError("restore model/memory/config differs")
        old_revision_id = old_identity.get("revision_id")
        new_revision_id = revision.revision_id if revision is not None else None
        if old_revision_id is not None and old_revision_id != new_revision_id:
            if (memory_revision is None
                    or replay_parent_revision_id != old_revision_id):
                raise ValueError("restored revision memory differs")
        if (checkpoint["task_hash"] != stable_digest(task) or stable_digest({k: v for k, v in
                checkpoint.items() if k != "checkpoint_hash"}) != checkpoint["checkpoint_hash"]):
            raise ValueError("restore task differs")
    agent_class = official_agent(config["sources"]["mini"], workspace)
    counters = {m["id"]: LocalTokenizer(m, workspace) for m in config["models"]}
    if config.get("embedding"):
        counters[config["embedding"]["id"]] = EmbeddingTokenizer(config["embedding"], workspace)
    if model_id not in counters:
        raise ValueError("unknown model slot")
    output.mkdir(parents=True)
    done = set()
    if checkpoint:
        for filename in ("provider_attempts.jsonl", "provider_ledger.jsonl", "completed_steps.jsonl"):
            source = restore.parent / filename
            if source.exists():
                (output / filename).write_bytes(source.read_bytes())
        done = {r["job_id"] for r in load_jsonl(output / "completed_steps.jsonl")}
    ledger = ServerLedger(output, config, counters, done).for_model(model_id)
    methods = MemoryMethods(config, counters[model_id].count, workspace, ledger)
    model = MiniModel(
        ledger, methods, task, method, budget, output,
        revision_memory=revision, revision_renderer=renderer, revision_budget=memory_budget,
    )
    env = CheckpointEnvironment(
        task, output, execute=execute,
        restore_image=checkpoint["image"] if checkpoint else None,
        guard_state=checkpoint.get("environment_guard_state") if checkpoint else None,
    )
    try:
        write_json(output / "identity.json", {"plan_hash": stable_digest(plan), "task_id": task_id,
            "model_id": model_id, "method": method, "budget": budget,
            "restore_source": str(restore) if restore else None,
            "revision_id": revision.revision_id if revision is not None else None,
            "revision_memory_budget": memory_budget,
            "memory_source_kind": (
                "ledger" if memory_ledger is not None else
                ("revision" if memory_revision is not None else None)
            ),
            "memory_source_sha256": file_sha256(memory_source) if memory_source else None})
        return run_task(task, agent_class, model, env, output, checkpoint=checkpoint)
    finally:
        env.close()
