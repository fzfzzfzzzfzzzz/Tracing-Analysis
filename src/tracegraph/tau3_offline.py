"""Opt-in τ³ generation persistence and offline reward evaluation.

All τ³ imports are local so the core package remains dependency-free. The
adapter never changes upstream source files and is inactive unless an explicit
trajectory-store environment variable is set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .trajectory_artifacts import (
    EvaluationConfig,
    EvaluationRecorder,
    TrajectoryArtifactStore,
)


def _as_json(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_json(item) for item in value]
    return value


def _provider_usage(simulation: Mapping[str, Any]) -> dict[str, Any]:
    totals = {
        "agent_input_tokens": 0,
        "agent_output_tokens": 0,
        "agent_calls_with_usage": 0,
        "user_input_tokens": 0,
        "user_output_tokens": 0,
        "user_calls_with_usage": 0,
    }
    for message in simulation.get("messages") or []:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "")
        prefix = "agent" if role in {"assistant", "agent"} else "user" if role == "user" else None
        usage = message.get("usage")
        if prefix is None or not isinstance(usage, Mapping):
            continue
        input_value = next(
            (
                usage[key]
                for key in ("prompt_tokens", "input_tokens", "input_token_count")
                if isinstance(usage.get(key), (int, float))
            ),
            None,
        )
        output_value = next(
            (
                usage[key]
                for key in (
                    "completion_tokens",
                    "output_tokens",
                    "output_token_count",
                )
                if isinstance(usage.get(key), (int, float))
            ),
            None,
        )
        if input_value is not None or output_value is not None:
            totals[f"{prefix}_calls_with_usage"] += 1
        totals[f"{prefix}_input_tokens"] += int(input_value or 0)
        totals[f"{prefix}_output_tokens"] += int(output_value or 0)
    totals["agent_cost_usd"] = simulation.get("agent_cost")
    totals["user_cost_usd"] = simulation.get("user_cost")
    return totals


def configure_tau_nl_evaluator(*, model: str, args: Mapping[str, Any], json_mode: str) -> None:
    """Set process-local τ³ evaluator defaults and fenced-JSON handling."""

    if not model:
        return
    import tau2.config as tau_config
    import tau2.evaluator.evaluator_nl_assertions as nl_evaluator
    from tau2.utils.llm_utils import extract_json_from_llm_response

    evaluator_args = dict(args)
    tau_config.DEFAULT_LLM_NL_ASSERTIONS = model
    tau_config.DEFAULT_LLM_NL_ASSERTIONS_ARGS = evaluator_args
    nl_evaluator.DEFAULT_LLM_NL_ASSERTIONS = model
    nl_evaluator.DEFAULT_LLM_NL_ASSERTIONS_ARGS = evaluator_args

    if json_mode == "strict":
        nl_evaluator.json = json
        return
    if json_mode != "strict_then_extract":
        raise ValueError(f"unsupported evaluator JSON mode: {json_mode}")

    class _EvaluatorJsonAdapter:
        @staticmethod
        def loads(value: str) -> object:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                extracted = extract_json_from_llm_response(value)
                if extracted == value:
                    raise
                return json.loads(extracted)

    nl_evaluator.json = _EvaluatorJsonAdapter


def _runtime_provenance(
    orchestrator: Any, evaluation_type: Any, communication_mode: Any
) -> dict[str, Any]:
    agent = getattr(orchestrator, "agent", None)
    user = getattr(orchestrator, "user", None)
    return {
        "protocol": "tau3_generation_before_evaluation_v1",
        "domain": str(orchestrator.environment.get_domain_name()),
        "mode": str(getattr(communication_mode, "value", communication_mode)),
        "solo_mode": bool(getattr(orchestrator, "solo_mode", False)),
        "evaluation_type_requested": str(getattr(evaluation_type, "value", evaluation_type)),
        "seed": getattr(orchestrator, "seed", None),
        "agent_model": getattr(agent, "llm", None),
        "agent_args": _as_json(getattr(agent, "llm_args", {}) or {}),
        "user_model": getattr(user, "llm", None),
        "user_args": _as_json(getattr(user, "llm_args", {}) or {}),
    }


def install_decoupled_tau3_runner_from_env() -> bool:
    """Install generation-only τ³ runner aliases when explicitly requested."""

    store_root = os.environ.get("TRACEGRAPH_TAU_TRAJECTORY_STORE", "").strip()
    if not store_root:
        return False
    mode_setting = os.environ.get("TRACEGRAPH_TAU_EXECUTION_MODE", "generation_only").strip()
    if mode_setting != "generation_only":
        raise ValueError(
            "TRACEGRAPH_TAU_EXECUTION_MODE must be generation_only when the "
            "decoupled trajectory store is enabled"
        )

    from tau2.orchestrator.full_duplex_orchestrator import FullDuplexOrchestrator
    from tau2.orchestrator.modes import CommunicationMode
    import tau2.runner as runner_package
    import tau2.runner.batch as runner_batch
    import tau2.runner.simulation as runner_simulation

    store = TrajectoryArtifactStore(Path(store_root))

    def run_generation_only(
        orchestrator: Any,
        *,
        evaluation_type: Any,
        env_kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        simulation_id = str(orchestrator.simulation_id)
        is_full_duplex = isinstance(orchestrator, FullDuplexOrchestrator)
        communication_mode = (
            CommunicationMode.FULL_DUPLEX if is_full_duplex else CommunicationMode.HALF_DUPLEX
        )
        provenance = _runtime_provenance(orchestrator, evaluation_type, communication_mode)
        provenance["env_kwargs"] = dict(env_kwargs or {})
        try:
            simulation = orchestrator.run()
            simulation.policy = orchestrator.environment.get_policy()
            simulation_value = _as_json(simulation)
            environment_summary = {
                "domain": orchestrator.environment.get_domain_name(),
                "agent_db_hash": orchestrator.environment.get_db_hash(),
                "user_db_hash": orchestrator.environment.get_user_db_hash(),
                "mode": communication_mode.value,
                "solo_mode": bool(getattr(orchestrator, "solo_mode", False)),
                "env_kwargs": dict(env_kwargs or {}),
            }
            store.persist_generation(
                simulation_id=simulation_id,
                simulation=simulation_value,
                task=_as_json(orchestrator.task),
                environment_summary=environment_summary,
                usage=_provider_usage(simulation_value),
                provenance=provenance,
            )
            return simulation
        except Exception as error:
            store.record_generation_error(simulation_id, error, provenance=provenance)
            raise

    runner_simulation.run_simulation = run_generation_only
    runner_batch.run_simulation = run_generation_only
    runner_package.run_simulation = run_generation_only
    os.environ["TRACEGRAPH_TAU_GENERATION_PERSISTENCE"] = "enabled_v1"
    return True


def evaluate_persisted_tau3(
    store: TrajectoryArtifactStore,
    simulation_id: str,
    config: EvaluationConfig,
) -> dict[str, Any]:
    """Evaluate a frozen τ³ trajectory and merge its reward."""

    configure_tau_nl_evaluator(model=config.model, args=config.args, json_mode=config.json_mode)
    from tau2.data_model.simulation import SimulationRun
    from tau2.data_model.tasks import Task
    from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
    import tau2.evaluator.evaluator_nl_assertions as nl_evaluator
    from tau2.orchestrator.modes import CommunicationMode

    def evaluator(generation: Mapping[str, Any], recorder: EvaluationRecorder) -> Mapping[str, Any]:
        simulation = SimulationRun.model_validate(generation["simulation"])
        task = Task.model_validate(generation["task"])
        environment = generation["environment_summary"]
        provenance = generation["provenance"]
        original_generate = nl_evaluator.generate

        def capture_generate(*args: Any, **kwargs: Any) -> Any:
            response = original_generate(*args, **kwargs)
            recorder.record_raw(str(getattr(response, "content", response)))
            return response

        nl_evaluator.generate = capture_generate
        try:
            reward = evaluate_simulation(
                simulation=simulation,
                task=task,
                evaluation_type=EvaluationType(config.evaluation_type),
                solo_mode=bool(environment.get("solo_mode", False)),
                domain=str(environment["domain"]),
                mode=CommunicationMode(str(environment.get("mode") or provenance["mode"])),
                env_kwargs=dict(environment.get("env_kwargs") or {}),
            )
        finally:
            nl_evaluator.generate = original_generate
        return {
            "reward_info": reward.model_dump(mode="json"),
            "evaluation_type": config.evaluation_type,
            "evaluator_model": config.model,
            "raw_response_count": recorder.raw_response_count,
        }

    return store.run_offline_evaluation(simulation_id, config, evaluator)
