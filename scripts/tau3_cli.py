"""Register the TraceGraph agent and delegate to the upstream τ³ CLI.

The wrapper also normalizes two Windows-specific integration details before
importing τ³: UTF-8 console streams for Rich output, and a process-local Git
``safe.directory`` entry so the upstream provenance probe can read the current
commit without changing the user's global Git configuration.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _prepare_windows_process() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")

    workspace = Path(__file__).resolve().parents[1].as_posix()
    try:
        config_count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        config_count = 0
    os.environ[f"GIT_CONFIG_KEY_{config_count}"] = "safe.directory"
    os.environ[f"GIT_CONFIG_VALUE_{config_count}"] = workspace
    os.environ["GIT_CONFIG_COUNT"] = str(config_count + 1)


_prepare_windows_process()


def _configure_nl_evaluator() -> None:
    """Apply an explicit process-local evaluator model without patching vendor code."""

    model = os.environ.get("TRACEGRAPH_TAU_NL_EVALUATOR_MODEL", "").strip()
    if not model:
        return
    evaluator_args = {
        "temperature": 0.0,
        "max_tokens": 512,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    from tracegraph.tau3_offline import configure_tau_nl_evaluator

    configure_tau_nl_evaluator(
        model=model,
        args=evaluator_args,
        json_mode="strict_then_extract",
    )
    os.environ["TRACEGRAPH_TAU_NL_EVALUATOR_ARGS"] = json.dumps(
        evaluator_args,
        ensure_ascii=True,
        sort_keys=True,
    )
    os.environ["TRACEGRAPH_TAU_NL_EVALUATOR_JSON_MODE"] = "strict_then_extract"


_configure_nl_evaluator()

from tracegraph.tau3_offline import install_decoupled_tau3_runner_from_env  # noqa: E402

install_decoupled_tau3_runner_from_env()

from tracegraph.integrations.tau3_agent import register_tau3_agent  # noqa: E402
from tracegraph.integrations.tau3_user import register_tau3_user  # noqa: E402

register_tau3_agent()
register_tau3_user()

from tau2.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
