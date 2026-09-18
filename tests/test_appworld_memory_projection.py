from __future__ import annotations

import json
from pathlib import Path

from tracegraph.integrations.appworld_memory import (
    build_appworld_graph,
    close_appworld_protocol,
    project_appworld_history,
)
from tracegraph.schema import EdgeType, NodeType


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "Complete the task safely."},
        {
            "role": "assistant",
            "content": "print(apis.shop.create_order(item_id=1))",
        },
        {
            "role": "user",
            "content": "Execution failed. Traceback: invalid item_id",
        },
        {
            "role": "assistant",
            "content": "print(apis.shop.create_order(item_id=2))",
        },
        {"role": "user", "content": '{"status": "created", "id": 7}'},
    ]


def test_graph_records_failure_retry_and_resolution() -> None:
    graph = build_appworld_graph(_messages(), session_id="fixture")
    assert graph.validate() == []
    assert len(graph.find_nodes(node_types={NodeType.ERROR})) == 1
    assert len([edge for edge in graph.edges.values() if edge.edge_type == EdgeType.RETRIED_BY]) == 1
    assert len([edge for edge in graph.edges.values() if edge.edge_type == EdgeType.RESOLVED_BY]) == 1


def test_api_documentation_failure_schema_is_not_an_execution_error() -> None:
    messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "print(apis.api_docs.show_api_doc())"},
        {
            "role": "user",
            "content": json.dumps(
                {"response_schemas": {"failure": {"message": "string"}}}
            ),
        },
    ]
    graph = build_appworld_graph(messages, session_id="api_docs")
    assert not graph.find_nodes(node_types={NodeType.ERROR})
    assert len(graph.find_nodes(node_types={NodeType.OBSERVATION})) == 1


def test_repeated_successful_reads_are_not_inferred_as_supersession() -> None:
    messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "print(apis.docs.show_api_doc())"},
        {"role": "user", "content": '{"page": 1}'},
        {"role": "assistant", "content": "print(apis.docs.show_api_doc())"},
        {"role": "user", "content": '{"page": 2}'},
    ]
    graph = build_appworld_graph(messages, session_id="repeated_reads")
    assert not [
        edge
        for edge in graph.edges.values()
        if edge.edge_type == EdgeType.SUPERSEDED_BY
    ]
    projection = project_appworld_history(
        messages, budget=8192, session_id="repeated_reads"
    )
    assert projection.fragments == []


def test_appworld_protocol_closes_action_observation_pairs() -> None:
    selected, additions = close_appworld_protocol(_messages(), {1, 3, 5})
    assert selected == {1, 2, 3, 4, 5}
    assert {item.reason for item in additions} == {
        "action_for_selected_observation"
    }


def test_projection_is_deterministic_and_keeps_latest_pair() -> None:
    first = project_appworld_history(_messages(), budget=24, session_id="same")
    second = project_appworld_history(_messages(), budget=24, session_id="same")
    assert first.projected_sha256 == second.projected_sha256
    assert first.context_view.to_dict() == second.context_view.to_dict()
    assert 1 in first.selected_ordinals
    assert {4, 5}.issubset(first.selected_ordinals)
    assert first.messages[-1] == _messages()[-1]
    assert [message["role"] for message in first.messages] == [
        "user",
        "assistant",
        "user",
    ]


def test_final_protocol_projection_honors_budget_after_pair_closure() -> None:
    messages = [{"role": "user", "content": "task " * 20}]
    for index in range(8):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": f"print(apis.docs.show(page={index})) " * 5,
                },
                {"role": "user", "content": (f"page {index} result ") * 20},
            ]
        )
    projection = project_appworld_history(
        messages, budget=220, session_id="budget_closure"
    )
    assert projection.budget_pruned_ordinals
    assert not projection.projection_budget_infeasible
    assert projection.to_record()["projected_estimated_tokens"] <= 220
    assert projection.messages[0]["role"] == "user"
    assert projection.messages[-2:] == messages[-2:]


def test_saved_appworld_history_replays_without_protocol_or_graph_errors() -> None:
    path = Path(
        "outputs/appworld_external_canary_260918/full_history_qwen38_v3/"
        "task_383cbac_1/llm_history.json"
    )
    if not path.exists():
        return
    sessions = json.loads(path.read_text(encoding="utf-8"))
    source = sessions[0]
    if source and source[0].get("role") == "system":
        source = source[1:]
    for end in range(1, len(source) + 1, 2):
        prefix = source[:end]
        projection = project_appworld_history(
            prefix,
            budget=8192,
            session_id=f"saved_{end}",
        )
        assert projection.graph.validate() == []
        assert projection.messages[0]["role"] == "user"
        assert projection.messages[-1]["role"] == "user"
        assert all(
            left["role"] != right["role"]
            for left, right in zip(projection.messages, projection.messages[1:])
        )
