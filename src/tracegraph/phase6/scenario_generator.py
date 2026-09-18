"""Definitions moved from ``tracegraph.phase6_scenarios``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from ..archive import ArchiveStore
from ..capture import estimate_tokens
from ..decision_state import stable_digest
from ..goal_lifecycle import GoalContext, GoalLifecycleState
from ..graph import TraceGraph
from ..schema import Edge, EdgeType, Node, NodeType

from .scenario_constants import (
    DEFAULT_BASE_SEED as DEFAULT_BASE_SEED,
    SCENARIO_FAMILIES as SCENARIO_FAMILIES,
    VARIANTS as VARIANTS,
)



def _build_prefix(
    family: str,
    variant_id: str,
    payload_tokens: int,
    reacquisition_mode: str,
    *,
    archive: ArchiveStore,
    base_seed: int,
) -> ScenarioPrefix:
    prefix_id = f"{family}:{variant_id}"
    old_goal_id = f"{prefix_id}:goal-old"
    current_goal_id = f"{prefix_id}:goal-current"
    chain_id = f"{prefix_id}:causal-history"
    entity = {
        "F1_shell_switch": "shell-script.ps1",
        "F2_failed_approach": "module-alpha",
        "F3_goal_resume": "goal-a-worktree",
        "F4_explainable_process": "calculation-42",
        "F5_state_supersession": "service-config",
        "F6_side_effect_audit": "order-9001",
    }[family]
    a = _ScenarioAssembly(prefix_id=prefix_id, archive=archive)
    old_goal = a.goal(
        "old-goal",
        f"Investigate historical work for {entity}",
        GoalLifecycleState.DORMANT,
        old_goal_id,
    )
    pinned = a.node(
        "constraint",
        NodeType.CONSTRAINT,
        f"Never repeat irreversible operations for {entity} without confirmation.",
        GoalLifecycleState.PINNED,
        goal_id=current_goal_id,
        metadata={"retention_obligation": "policy", "pinned": True},
    )
    del pinned

    error_signature: str | None = None
    forbidden_ids: tuple[str, ...] = ()
    if family in {"F1_shell_switch", "F2_failed_approach"}:
        if family == "F1_shell_switch":
            first_tool, second_tool = "powershell", "bash"
            error_signature = "shell_syntax_error"
            answer_fact = "PowerShell syntax failed; Bash-compatible syntax resolved it."
        else:
            first_tool, second_tool = "approach_a", "approach_b"
            error_signature = "approach_a_failure"
            answer_fact = "Approach A failed on diagnostic evidence; Approach B succeeded."
        failed_call, failed_result = a.exchange(
            "failed-attempt",
            tool_name=first_tool,
            arguments={"operation": "attempt", "entity": entity},
            result={
                "error": error_signature,
                "detail": _filler(error_signature, payload_tokens),
            },
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            failed=True,
            metadata={
                "error_signature": error_signature,
                "retrieval_terms": [entity, error_signature, first_tool, "previous"],
                "guard_text": f"Avoid {first_tool}: {error_signature}",
                "reactivation_token_count": 96,
                "reactivation_value": True,
            },
        )
        decision = a.decision(
            "switch-decision",
            f"Switch from {first_tool} to {second_tool} because {error_signature}.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, first_tool, second_tool],
                "reactivation_value": True,
            },
        )
        retry_call, retry_result = a.exchange(
            "successful-alternative",
            tool_name=second_tool,
            arguments={"operation": "retry", "entity": entity},
            result={"status": "success", "entity": entity, "method": second_tool},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, second_tool, "resolved"],
                "reactivation_value": True,
                "reactivation_token_count": 64,
            },
        )
        a.edge(failed_result, decision, EdgeType.BLOCKS)
        a.edge(decision, retry_call, EdgeType.LEADS_TO)
        a.edge(failed_call, retry_call, EdgeType.RETRIED_BY)
        a.edge(failed_result, retry_result, EdgeType.RESOLVED_BY)
        anchors = (failed_result.node_id,)
        required = tuple(
            item.node_id
            for item in (failed_call, failed_result, decision, retry_call, retry_result)
        )
    elif family == "F3_goal_resume":
        answer_fact = "Goal A resumes from its saved partial progress and next step."
        progress_call, progress_result = a.exchange(
            "goal-a-progress",
            tool_name="inspect_goal",
            arguments={"operation": "inspect", "entity": entity},
            result={"progress": "60%", "next_step": "apply patch", "detail": _filler("goal-a", payload_tokens)},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, old_goal_id, "resume"],
                "reactivation_value": True,
                "reactivation_token_count": 96,
            },
        )
        pause = a.decision(
            "goal-a-paused",
            "Goal A was paused after partial progress; preserve the next step.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={"retrieval_terms": [entity, old_goal_id, "paused", "resume"]},
        )
        a.edge(progress_result, pause, EdgeType.SUPPORTS)
        anchors = (progress_result.node_id, pause.node_id)
        required = (old_goal.node_id, progress_call.node_id, progress_result.node_id, pause.node_id)
    elif family == "F4_explainable_process":
        answer_fact = "The archived intermediate calculation deterministically produced result 42."
        process_call, process_result = a.exchange(
            "calculation-process",
            tool_name="calculator",
            arguments={"operation": "calculate", "entity": entity},
            result={"intermediate": _filler("calculation trace", payload_tokens), "result": 42},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, "calculation", "42", "intermediate"],
                "reactivation_value": True,
                "reactivation_token_count": 128,
            },
        )
        explanation = a.decision(
            "calculation-conclusion",
            "Intermediate steps support the final result 42.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={"retrieval_terms": [entity, "result", "42"]},
        )
        a.edge(process_result, explanation, EdgeType.SUPPORTS)
        anchors = (process_result.node_id,)
        required = (process_call.node_id, process_result.node_id, explanation.node_id)
    elif family == "F5_state_supersession":
        answer_fact = "The old config value was v1; it was intentionally superseded by v2."
        old_call, old_result = a.exchange(
            "read-old-config",
            tool_name="read_config",
            arguments={"operation": "read", "entity": entity},
            result={"version": "v1", "detail": _filler("old config", payload_tokens)},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, "old", "v1", "previous"],
                "reactivation_value": True,
                "reactivation_token_count": 96,
            },
        )
        reason = a.decision(
            "update-reason",
            "Update was required to replace v1 with v2.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={"retrieval_terms": [entity, "v1", "v2", "why"]},
        )
        new_call, new_result = a.exchange(
            "read-new-config",
            tool_name="read_config",
            arguments={"operation": "read", "entity": entity},
            result={"version": "v2", "status": "current"},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, "new", "v2"],
                "reactivation_value": True,
                "reactivation_token_count": 64,
            },
        )
        a.edge(old_result, reason, EdgeType.SUPPORTS)
        a.edge(old_result, new_result, EdgeType.SUPERSEDED_BY)
        # The oracle records only the old result as epistemically superseded;
        # the protocol call remains dormant and is evicted as one full span.
        a.gold[old_result.node_id] = GoalLifecycleState.SUPERSEDED
        anchors = (old_result.node_id,)
        required = (old_call.node_id, old_result.node_id, reason.node_id, new_call.node_id, new_result.node_id)
        forbidden_ids = (old_result.node_id,)
    else:
        answer_fact = "The archived authorization rationale and pinned receipt prove the side effect was approved once."
        auth_call, auth_result = a.exchange(
            "authorization-check",
            tool_name="check_authorization",
            arguments={"operation": "authorize", "entity": entity},
            result={"authorized": True, "reason": _filler("user confirmation", payload_tokens)},
            state=GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={
                "retrieval_terms": [entity, "authorization", "confirmation", "audit"],
                "reactivation_value": True,
                "reactivation_token_count": 96,
            },
        )
        rationale = a.decision(
            "authorization-rationale",
            "User confirmation authorized exactly one side effect.",
            GoalLifecycleState.DORMANT,
            goal_id=old_goal_id,
            chain_id=chain_id,
            metadata={"retrieval_terms": [entity, "why", "authorization", "audit"]},
        )
        a.edge(auth_result, rationale, EdgeType.SUPPORTS)
        anchors = (auth_result.node_id,)
        required = (auth_call.node_id, auth_result.node_id, rationale.node_id)

    # A separate deterministically recomputable span exercises EPHEMERAL
    # without becoming part of the historical causal chain.
    a.exchange(
        "ephemeral-check",
        tool_name="checksum",
        arguments={"operation": "checksum", "entity": entity},
        result={"checksum": "deterministic"},
        state=GoalLifecycleState.EPHEMERAL,
        goal_id=old_goal_id,
        chain_id=f"{prefix_id}:ephemeral",
        metadata={
            "ephemeral_eligible": True,
            "deterministically_recomputable": True,
            "reactivation_value": False,
            "reactivation_token_count": 16,
        },
    )

    current_goal = a.goal(
        "current-goal",
        f"Complete the current task for {entity}",
        GoalLifecycleState.ACTIVE,
        current_goal_id,
    )
    current_goal.metadata["structured_current"] = True
    current_payload_tokens = max(96, payload_tokens // 3)
    current_call, current_result = a.exchange(
        "current-state",
        tool_name="current_state",
        arguments={"operation": "read_current", "entity": entity},
        result={"entity": entity, "status": "current", "detail": _filler("current", current_payload_tokens)},
        state=GoalLifecycleState.ACTIVE,
        goal_id=current_goal_id,
        chain_id=f"{prefix_id}:current",
        metadata={
            "current_fact": True,
            "current_root": True,
            "retrieval_terms": [entity, "current"],
        },
    )
    del current_goal, current_call, current_result

    if family == "F6_side_effect_audit":
        effect_call, effect_result = a.exchange(
            "side-effect-receipt",
            tool_name="execute_once",
            arguments={"operation": "execute", "entity": entity},
            result={"receipt_id": "receipt-9001", "executed": True},
            state=GoalLifecycleState.PINNED,
            goal_id=current_goal_id,
            chain_id=f"{prefix_id}:receipt",
            side_effect=True,
            metadata={
                "retention_obligation": "receipt",
                "pinned": True,
                "retrieval_terms": [entity, "receipt", "audit"],
            },
        )
        del effect_call, effect_result

    if family == "F4_explainable_process":
        a.node(
            "uncertain-note",
            NodeType.DECISION,
            "An unrelated partial diagnostic remains uncertain.",
            GoalLifecycleState.UNCERTAIN,
            goal_id=current_goal_id,
            metadata={"uncertain": True},
        )

    if a.graph.validate():
        raise ValueError(f"generated invalid graph {prefix_id}: {a.graph.validate()}")
    goal_context = GoalContext(
        current_goal_id=current_goal_id,
        paused_or_cancelled_goal_ids=(old_goal_id,),
        referenced_entities=(entity,),
    )
    forks = _forks(
        prefix_id=prefix_id,
        family=family,
        current_goal_id=current_goal_id,
        old_goal_id=old_goal_id,
        anchor_ids=anchors,
        required_ids=required,
        forbidden_ids=forbidden_ids,
        answer_fact=answer_fact,
        entity=entity,
        error_signature=error_signature,
        reacquisition_mode=reacquisition_mode,
    )
    removable_states = {
        GoalLifecycleState.DORMANT,
        GoalLifecycleState.SUPERSEDED,
        GoalLifecycleState.EPHEMERAL,
    }
    removable_span_ids = sorted(
        {
            (
                f"tool:{a.graph.nodes[event_id].metadata['call_id']}"
                if a.graph.nodes[event_id].metadata.get("call_id")
                else f"event:{event_id}"
            )
            for event_id, state in a.gold.items()
            if state in removable_states
        }
    )
    schemas = tuple(
        _tool_schema(name)
        for name in sorted(
            {
                str(node.metadata["tool_name"])
                for node in a.graph.nodes.values()
                if node.metadata.get("tool_name")
            }
        )
    )
    return ScenarioPrefix(
        prefix_id=prefix_id,
        scenario_family=family,
        variant_id=variant_id,
        payload_target_tokens=payload_tokens,
        reacquisition_mode=reacquisition_mode,
        graph=a.graph,
        messages=tuple(a.messages),
        tool_schemas=schemas,
        goal_context=goal_context,
        lifecycle_gold_by_event=dict(a.gold),
        oracle_removable_span_ids=tuple(removable_span_ids),
        forks=forks,
        base_seed=base_seed,
    )


def generate_trace_lifecycle_suite(
    archive_root: str | Path,
    *,
    base_seed: int = DEFAULT_BASE_SEED,
) -> tuple[ScenarioPrefix, ...]:
    """Generate the fixed 24-prefix/72-fork population in stable order."""

    archive = ArchiveStore(archive_root)
    return tuple(
        _build_prefix(
            family,
            variant_id,
            payload_tokens,
            reacquisition_mode,
            archive=archive,
            base_seed=base_seed,
        )
        for family in SCENARIO_FAMILIES
        for variant_id, payload_tokens, reacquisition_mode in VARIANTS
    )


# Imported after definitions so mutually-referential helpers initialize safely.
from .scenario_models import (
    ScenarioPrefix as ScenarioPrefix,
    _ScenarioAssembly as _ScenarioAssembly,
)

from .scenario_templates import (
    _filler as _filler,
    _forks as _forks,
    _tool_schema as _tool_schema,
)
