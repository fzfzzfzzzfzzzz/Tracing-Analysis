"""Import τ-bench and current τ³-bench result files into TraceGraph.

The adapter has no dependency on the upstream package. It consumes the JSON
contract so offline analysis can run under Python 3.11 while live τ³ runs stay
in the upstream Python 3.12/``uv`` environment.
"""

from __future__ import annotations

# ruff: noqa: F401

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..archive import ArchiveStore
from ..capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens
from ..graph import TraceGraph
from ..lifecycle import LifecycleEngine
from ..schema import (
    EdgeType,
    LifecycleState,
    Node,
    NodeType,
    SemanticOutcome,
    ToolStatus,
)
from ..semantics import (
    infer_semantic_outcome,
    is_argument_completion_retry,
    operation_key,
)


from .tau_import import import_simulation_impl


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _stable_node_id(
    simulation_id: str,
    kind: str,
    ordinal: int,
    index: int = 0,
) -> str:
    """Return a prefix-stable event identifier for deterministic re-imports."""

    payload = f"{simulation_id}\x1f{kind}\x1f{ordinal}\x1f{index}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:24]
    return f"{kind}_{digest}"


class TauTraceImporter:
    """Convert saved upstream simulations into dependency trace graphs."""

    DEFAULT_SIDE_EFFECT_PREFIXES = (
        "book",
        "cancel",
        "change",
        "create",
        "delete",
        "exchange",
        "modify",
        "refund",
        "return",
        "send",
        "transfer",
        "update",
        "write",
    )

    def __init__(
        self,
        archive: ArchiveStore,
        *,
        side_effect_prefixes: tuple[str, ...] | None = None,
    ) -> None:
        self.archive = archive
        self.side_effect_prefixes = side_effect_prefixes or self.DEFAULT_SIDE_EFFECT_PREFIXES

    def _is_side_effect(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered.startswith(self.side_effect_prefixes)

    @staticmethod
    def _messages(simulation: dict[str, Any]) -> list[dict[str, Any]]:
        messages = simulation.get("messages")
        if messages is None:
            messages = simulation.get("trajectory", simulation.get("traj", []))
        if not isinstance(messages, list):
            raise ValueError("simulation messages/trajectory/traj must be a list")
        flattened: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            tool_messages = item.get("tool_messages")
            if isinstance(tool_messages, list):
                flattened.extend(entry for entry in tool_messages if isinstance(entry, dict))
            else:
                flattened.append(item)
        return flattened

    @staticmethod
    def _task_lookup(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
        tasks = container.get("tasks", [])
        return {
            str(task.get("id")): task
            for task in tasks
            if isinstance(task, dict) and task.get("id") is not None
        }

    @staticmethod
    def iter_payloads(path: str | Path) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
        """Yield ``(simulation, task)`` from current and legacy result layouts."""

        source = Path(path)
        if source.is_dir():
            metadata_path = source / "results.json"
            if not metadata_path.is_file():
                raise FileNotFoundError(f"missing {metadata_path}")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            tasks = TauTraceImporter._task_lookup(metadata)
            inline = metadata.get("simulations", [])
            if inline:
                for simulation in inline:
                    if isinstance(simulation, dict):
                        yield simulation, tasks.get(str(simulation.get("task_id")))
                return
            simulations_dir = source / "simulations"
            for simulation_path in sorted(simulations_dir.glob("*.json")):
                simulation = json.loads(simulation_path.read_text(encoding="utf-8"))
                yield simulation, tasks.get(str(simulation.get("task_id")))
            return

        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for simulation in payload:
                if isinstance(simulation, dict):
                    yield simulation, None
            return
        if not isinstance(payload, dict):
            raise ValueError("τ-bench input must be a JSON object, list, or results directory")
        tasks = TauTraceImporter._task_lookup(payload)
        simulations = payload.get("simulations")
        if isinstance(simulations, list):
            for simulation in simulations:
                if isinstance(simulation, dict):
                    yield simulation, tasks.get(str(simulation.get("task_id")))
            return
        if payload.get("task_id") is not None or any(
            key in payload for key in ("messages", "trajectory", "traj")
        ):
            yield payload, tasks.get(str(payload.get("task_id")))
            return
        raise ValueError("no simulations found in τ-bench input")

    def import_path(
        self,
        path: str | Path,
        *,
        policy: str | None = None,
    ) -> list[TraceGraph]:
        return [
            self.import_simulation(simulation, task=task, policy=policy)
            for simulation, task in self.iter_payloads(path)
        ]

    def _archive(
        self,
        payload: Any,
        *,
        kind: str,
        graph: TraceGraph,
        step_id: int,
    ) -> str:
        return self.archive.put(
            payload,
            metadata={"kind": kind, "session_id": graph.session_id, "step_id": step_id},
        )

    def import_simulation(
        self,
        simulation: dict[str, Any],
        *,
        task: dict[str, Any] | None = None,
        policy: str | None = None,
    ) -> TraceGraph:
        return import_simulation_impl(
            self, simulation, task=task, policy=policy
        )
