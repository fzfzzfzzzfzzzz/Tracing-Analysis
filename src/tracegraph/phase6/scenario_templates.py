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





def _filler(label: str, target_tokens: int) -> str:
    prefix = f"{label}:"
    return prefix + ("x" * max(0, target_tokens * 4 - len(prefix)))


def _tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Deterministic controlled tool {name}",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "entity": {"type": "string"},
                },
                "required": ["operation"],
            },
        },
    }


def _forks(
    *,
    prefix_id: str,
    family: str,
    current_goal_id: str,
    old_goal_id: str,
    anchor_ids: tuple[str, ...],
    required_ids: tuple[str, ...],
    forbidden_ids: tuple[str, ...],
    answer_fact: str,
    entity: str,
    error_signature: str | None,
    reacquisition_mode: str,
) -> tuple[ScenarioFork, ...]:
    if family == "F3_goal_resume":
        reactivate_request = {
            "text": f"Resume the earlier goal {old_goal_id} for {entity}.",
            "current_goal_id": current_goal_id,
            "resume_goal_id": old_goal_id,
            "referenced_entities": [entity],
        }
    elif error_signature:
        reactivate_request = {
            "text": f"The previous {error_signature} happened again for {entity}; explain why.",
            "current_goal_id": current_goal_id,
            "error_signature": error_signature,
            "referenced_entities": [entity],
            "historical_intent": True,
        }
    else:
        reactivate_request = {
            "text": f"Audit why the previous operation for {entity} was performed.",
            "current_goal_id": current_goal_id,
            "historical_intent": True,
            "historical_reference": True,
            "referenced_entities": [entity],
            "referenced_event_ids": list(anchor_ids[:1]),
        }
    calls = 1 if reacquisition_mode == "cheap" else 3
    observation_tokens = 128 if reacquisition_mode == "cheap" else 512
    rows = (
        ScenarioFork(
            fork_id=f"{prefix_id}:continue",
            prefix_id=prefix_id,
            fork_type="CONTINUE",
            request={
                "text": f"Continue the current task for {entity} using the current state.",
                "current_goal_id": current_goal_id,
                "referenced_entities": [entity],
            },
            required_anchor_ids=(),
            required_subgraph_event_ids=(),
            forbidden_current_fact_ids=forbidden_ids,
            expected_answer_facts=(f"current:{entity}",),
            expected_final_state={"goal_id": current_goal_id, "status": "continued"},
            reacquisition_calls=0,
            reacquisition_observation_tokens=0,
        ),
        ScenarioFork(
            fork_id=f"{prefix_id}:reactivate",
            prefix_id=prefix_id,
            fork_type="REACTIVATE",
            request=reactivate_request,
            required_anchor_ids=anchor_ids,
            required_subgraph_event_ids=required_ids,
            forbidden_current_fact_ids=forbidden_ids,
            expected_answer_facts=(answer_fact,),
            expected_final_state={"goal_id": old_goal_id, "status": "historically_explained"},
            reacquisition_calls=calls,
            reacquisition_observation_tokens=observation_tokens,
        ),
        ScenarioFork(
            fork_id=f"{prefix_id}:distractor",
            prefix_id=prefix_id,
            fork_type="DISTRACTOR",
            request={
                "text": f"Use {entity} for a new current operation; do not revisit history.",
                "current_goal_id": current_goal_id,
                "referenced_entities": [entity],
            },
            required_anchor_ids=(),
            required_subgraph_event_ids=(),
            forbidden_current_fact_ids=forbidden_ids,
            expected_answer_facts=(f"current:{entity}",),
            expected_final_state={"goal_id": current_goal_id, "status": "new_operation"},
            reacquisition_calls=0,
            reacquisition_observation_tokens=0,
        ),
    )
    return rows


# Imported after definitions so mutually-referential helpers initialize safely.
from .scenario_models import (
    ScenarioFork as ScenarioFork,
)
