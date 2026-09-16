"""Explicit real-environment entry point; no automatic server or container startup."""

import json
from pathlib import Path

from ..compression_audit.development_experiment import write_json
from ..compression_audit.io import file_sha256, stable_digest, load_jsonl
from .config import load_config, live_blockers, LocalTokenizer, EmbeddingTokenizer
from .external import source_status
from .methods import MemoryMethods
from .mini import CheckpointEnvironment, MiniModel, official_agent, run_task, validate_restore_source
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


def execute_mini(prepared: Path, output: Path, workspace: Path, *, task_id, model_id, method,
                 budget, execute=False, restore: Path | None = None, calibration: Path | None = None):
    if not execute:
        raise ValueError("mini execution requires explicit --execute")
    config = load_config(prepared / "config.snapshot.json")
    tasks = json.loads((prepared / "tasks.snapshot.json").read_text(encoding="utf-8"))
    plan = json.loads((prepared / "plan.json").read_text(encoding="utf-8"))
    if plan["config_hash"] != stable_digest(config) or plan["tasks_hash"] != stable_digest(tasks):
        raise ValueError("mini plan changed")
    if output.exists() or method not in config["methods"] or budget not in config["history_budgets"]:
        raise ValueError("new output and a frozen method/budget required")
    blockers = live_blockers(config, workspace) + source_status(config, workspace)
    if blockers:
        raise ValueError("mini live preflight blocked: " + str(blockers))
    if calibration is None:
        raise ValueError("real environment run requires a passed audit calibration directory")
    report = json.loads((calibration / "report.json").read_text(encoding="utf-8"))
    identity = json.loads((calibration / "identity.json").read_text(encoding="utf-8"))
    cell = next((c for c in report["cells"] if c["cell_id"] == f"{model_id}-b{budget}"), None)
    if (report["mode"] != "live" or not report["judge_gate"]["pass"] or not cell
            or not cell["calibration"]["pass"] or identity["config_hash"] != stable_digest(config)):
        raise ValueError("audit calibration identity or gate failed")
    task = next(t for t in tasks if t["id"] == task_id)
    checkpoint = json.loads(restore.read_text(encoding="utf-8")) if restore else None
    if checkpoint:
        validate_restore_source(restore.parent, checkpoint)
        old_identity = json.loads((restore.parent / "identity.json").read_text(encoding="utf-8"))
        if any(old_identity[k] != v for k, v in {"model_id": model_id, "method": method,
                "budget": budget, "plan_hash": stable_digest(plan)}.items()):
            raise ValueError("restore model/memory/config differs")
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
    model = MiniModel(ledger, methods, task, method, budget, output)
    env = CheckpointEnvironment(task, output, execute=execute,
                                 restore_image=checkpoint["image"] if checkpoint else None)
    try:
        write_json(output / "identity.json", {"plan_hash": stable_digest(plan), "task_id": task_id,
            "model_id": model_id, "method": method, "budget": budget,
            "restore_source": str(restore) if restore else None})
        return run_task(task, agent_class, model, env, output, checkpoint=checkpoint)
    finally:
        env.close()
