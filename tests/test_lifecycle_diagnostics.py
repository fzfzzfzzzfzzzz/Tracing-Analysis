import json
import tempfile
import unittest
from pathlib import Path

from tracegraph.graph import TraceGraph
from tracegraph.lifecycle_diagnostics import (
    analyze_lifecycle_disagreements,
    write_lifecycle_diagnostics,
)
from tracegraph.schema import EdgeType, LifecycleState, NodeType


def _write_trace(
    root: Path,
    name: str,
    *,
    lifecycle: LifecycleState,
    with_error: bool = False,
    with_retry: bool = False,
    write_context_view: bool = False,
) -> str:
    graph = TraceGraph(session_id=name)
    first = graph.create_node(
        NodeType.TOOL_CALL,
        {"tool": "lookup"},
        1,
        lifecycle=lifecycle,
    )
    graph.create_node(
        NodeType.OBSERVATION,
        {"ok": True},
        1,
        lifecycle=LifecycleState.CONSUMED,
    )
    if with_error:
        error = graph.create_node(
            NodeType.ERROR,
            {"error": "temporary"},
            2,
            lifecycle=LifecycleState.UNRESOLVED_FAILURE,
        )
        graph.connect(first.node_id, error.node_id, EdgeType.FAILED_WITH)
        if with_retry:
            retry = graph.create_node(
                NodeType.TOOL_CALL,
                {"tool": "lookup"},
                3,
                lifecycle=LifecycleState.ACTIVE,
            )
            graph.connect(retry.node_id, first.node_id, EdgeType.RETRIES)
    path = root / "traces" / name / "trace.json"
    graph.save(path)
    if write_context_view:
        error_items = [
            {
                "node_id": node.node_id,
                "node_type": "error",
                "reason": "unresolved_failure",
            }
            for node in graph.find_nodes(node_types={NodeType.ERROR})
        ]
        (path.parent / "context_views.jsonl").write_text(
            json.dumps(
                {
                    "selected_tokens": 3000,
                    "budget": 2048,
                    "items": error_items,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return path.relative_to(root).as_posix()


class LifecycleDiagnosticsTests(unittest.TestCase):
    def test_ranks_disagreements_and_failure_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            cases = (
                ("retail", "0", 0, True, False, True, True),
                ("airline", "1", 0, False, True, False, False),
                ("retail", "2", 0, True, True, False, False),
            )
            for domain, task_id, trial, reference_success, comparator_success, error, retry in cases:
                for manager, success in (
                    ("ours_without_lifecycle_states", reference_success),
                    ("full_ours", comparator_success),
                ):
                    trace_file = _write_trace(
                        root,
                        f"{manager}-{domain}-{task_id}-{trial}",
                        lifecycle=(
                            LifecycleState.CONSUMED
                            if manager == "full_ours"
                            else LifecycleState.ACTIVE
                        ),
                        with_error=error and manager == "full_ours",
                        with_retry=retry and manager == "full_ours",
                        write_context_view=error and manager == "full_ours",
                    )
                    rows.append(
                        {
                            "manager": manager,
                            "domain": domain,
                            "task_id": task_id,
                            "trial": trial,
                            "task_success": success,
                            "termination_reason": "user_stop",
                            "trace_file": trace_file,
                            "total_selected_context_tokens": (
                                1000 if manager == "full_ours" else 1500
                            ),
                        }
                    )
            report = analyze_lifecycle_disagreements(
                {"matrix_id": "diagnostic-test", "sessions": rows},
                project_root=root,
            )
            self.assertEqual(report["counts"]["matched_pairs"], 3)
            self.assertEqual(report["counts"]["success_disagreements"], 2)
            self.assertEqual(report["counts"]["pairs_with_failure_signal"], 1)
            self.assertEqual(report["counts"]["pairs_with_retry_edges"], 1)
            self.assertEqual(
                report["counts"]["pairs_with_selected_error_items"],
                1,
            )
            self.assertEqual(
                report["counts"]["comparator_unresolved_failure_reason_items"],
                1,
            )
            self.assertEqual(report["counts"]["selected_comparator_traces"], 2)
            self.assertEqual(
                report["pairs"][0]["priority_tier"],
                "disagreement_with_failure",
            )
            self.assertEqual(
                report["pairs"][0]["selected_context_token_delta"],
                -500.0,
            )
            self.assertEqual(
                report["task_metrics"][0]["success_delta"],
                1.0,
            )

            output = root / "diagnostics"
            write_lifecycle_diagnostics(report, output)
            self.assertTrue((output / "lifecycle_diagnostics.json").exists())
            self.assertEqual(
                len(
                    (output / "lifecycle_pairs.csv")
                    .read_text(encoding="utf-8-sig")
                    .splitlines()
                ),
                4,
            )
            loaded = json.loads(
                (output / "lifecycle_diagnostics.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(loaded["matrix_id"], "diagnostic-test")

    def test_allows_missing_context_token_measurements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for manager in ("ours_without_lifecycle_states", "full_ours"):
                rows.append(
                    {
                        "manager": manager,
                        "domain": "retail",
                        "task_id": "0",
                        "trial": 0,
                        "task_success": True,
                        "trace_file": _write_trace(
                            root,
                            f"{manager}-retail-0-0",
                            lifecycle=LifecycleState.ACTIVE,
                        ),
                    }
                )

            report = analyze_lifecycle_disagreements(
                {"matrix_id": "missing-token-test", "sessions": rows},
                project_root=root,
            )

            self.assertIsNone(
                report["task_metrics"][0]["mean_selected_context_token_delta"]
            )


if __name__ == "__main__":
    unittest.main()
