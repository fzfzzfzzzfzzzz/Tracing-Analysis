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
    import tau2.config as tau_config

    evaluator_args = {
        "temperature": 0.0,
        "max_tokens": 512,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    tau_config.DEFAULT_LLM_NL_ASSERTIONS = model
    tau_config.DEFAULT_LLM_NL_ASSERTIONS_ARGS = evaluator_args

    # Importing ``tau2.config`` first executes ``tau2.__init__``.  The package
    # initializer eagerly imports the NL evaluator, whose module copies these
    # defaults with ``from tau2.config import ...``.  Update those copied
    # module globals too; changing only ``tau2.config`` leaves the evaluator on
    # the upstream OpenAI default despite the apparently-correct config value.
    import tau2.evaluator.evaluator_nl_assertions as nl_evaluator
    from tau2.utils.llm_utils import extract_json_from_llm_response

    nl_evaluator.DEFAULT_LLM_NL_ASSERTIONS = model
    nl_evaluator.DEFAULT_LLM_NL_ASSERTIONS_ARGS = evaluator_args

    class _EvaluatorJsonAdapter:
        """Keep strict JSON first, then accept a fenced LLM JSON object."""

        @staticmethod
        def loads(value: str) -> object:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                extracted = extract_json_from_llm_response(value)
                if extracted == value:
                    raise
                return json.loads(extracted)

    # The upstream evaluator binds the stdlib ``json`` module as a module
    # global and calls only ``json.loads``.  Rebinding that one evaluator
    # global keeps the compatibility change local; it does not monkeypatch
    # Python's shared json module or any vendor source file.
    nl_evaluator.json = _EvaluatorJsonAdapter
    os.environ["TRACEGRAPH_TAU_NL_EVALUATOR_ARGS"] = json.dumps(
        evaluator_args,
        ensure_ascii=True,
        sort_keys=True,
    )
    os.environ["TRACEGRAPH_TAU_NL_EVALUATOR_JSON_MODE"] = "strict_then_extract"


_configure_nl_evaluator()

from tracegraph.integrations.tau3_agent import register_tau3_agent  # noqa: E402
from tracegraph.integrations.tau3_user import register_tau3_user  # noqa: E402

register_tau3_agent()
register_tau3_user()

from tau2.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
