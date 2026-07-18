"""Plan or explicitly execute a secret-free GLM τ³ experiment matrix."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tracegraph.matrix import build_matrix_plan, require_execution_budget


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def _powershell_command(project_root: Path, run: dict) -> list[str]:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise RuntimeError("PowerShell is required to execute the GLM matrix")
    command = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(project_root / "scripts" / "run_glm_pilot.ps1"),
        "-Domain",
        run["domain"],
        "-TaskId",
        run["task_id"],
        "-Manager",
        run["manager"],
        "-Budget",
        run["budget"],
        "-AgentModel",
        run["agent_model"],
        "-UserModel",
        run["user_model"],
        "-NumTrials",
        str(run["trials"]),
        "-MaxSteps",
        str(run["max_steps"]),
        "-Seed",
        str(run["base_seed"]),
        "-TimeoutSeconds",
        str(run["timeout_seconds"]),
        "-TokenAccounting",
        run["token_accounting"],
        "-SaveTo",
        run["save_to"],
        "-TraceOutputDir",
        run["trace_output_dir"],
        "-VerboseLogs",
    ]
    if run["normalize_user_stop"]:
        command.append("-NormalizeUserStop")
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--print-commands", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-estimated-cost-usd", type=float)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    plan = build_matrix_plan(config)
    plan["generated_at"] = datetime.now(timezone.utc).isoformat()
    plan["source_config"] = str(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = {
        "matrix_id": plan["matrix_id"],
        "run_count": plan["run_count"],
        "session_count": plan["session_count"],
        "estimated_total_cost_usd": plan["estimated_total_cost_usd"],
        "output": str(args.output),
        "execute": args.execute,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    project_root = Path(__file__).resolve().parents[1]
    commands = [_powershell_command(project_root, run) for run in plan["runs"]]
    if args.print_commands:
        for command in commands:
            print(subprocess.list2cmdline(command))

    if not args.execute:
        return
    if os.name != "nt":
        raise RuntimeError("matrix execution is currently supported only on Windows")
    require_execution_budget(plan, args.max_estimated_cost_usd)

    for index, command in enumerate(commands, start=1):
        print(f"[{index}/{len(commands)}] executing {plan['runs'][index - 1]['run_id']}")
        subprocess.run(command, cwd=project_root, check=True)
        delay = float(plan.get("inter_run_delay_seconds", 0.0))
        if delay > 0 and index < len(commands):
            print(f"cooldown: waiting {delay:g}s before the next run")
            time.sleep(delay)


if __name__ == "__main__":
    main()
