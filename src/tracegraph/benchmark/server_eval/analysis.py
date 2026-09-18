"""Observed budget frontiers; no invented interpolation or aggregate leaderboard."""

from collections import defaultdict

from ..compression_audit.development_results import episode_run_validity


def budget_frontiers(episodes, costs):
    groups = defaultdict(list)
    for row in episodes:
        if row["phase"] == "main":
            groups[(row["cell_id"], row["method_id"])].append(row)
    points = []
    for (cell, method), rows in sorted(groups.items()):
        validity = [episode_run_validity(row) for row in rows]
        capability_rows = [row for row, state in zip(rows, validity)
                           if state != "integration_invalid"]
        usage = [r for r in costs if r["cell_id"] == cell and r["method_id"] == method]
        cost = sum(r["input_tokens"] + r["output_tokens"] for r in usage)
        graded_scores = [float(r["score"].get("graded_audit_score", 0.0))
                         for r in capability_rows]
        overall_scores = [float(
            r["score"].get("overall_failure_memory_score",
                           r["score"].get("graded_audit_score"))
        ) for r in capability_rows if r["score"].get(
            "overall_failure_memory_score", r["score"].get("graded_audit_score")
        ) is not None]
        points.append({"cell_id": cell, "model_id": cell.rsplit("-b", 1)[0],
            "budget": int(cell.rsplit("-b", 1)[1]), "method_id": method,
            "query_ids": sorted(r["query_id"] for r in rows), "coverage": len(rows),
            "capability_coverage": len(capability_rows),
            "valid_run_rate": (validity.count("valid") / len(capability_rows)
                               if capability_rows else None),
            "integration_invalid": validity.count("integration_invalid"),
            "audit_pass_rate": (sum(r["score"].get("audit_pass") is True
                                    for r in capability_rows) / len(capability_rows)
                                if capability_rows else None),
            "graded_audit_score": (sum(graded_scores) / len(graded_scores)
                                   if graded_scores else None),
            "overall_failure_memory_score": (sum(overall_scores) / len(overall_scores)
                                             if overall_scores else None),
            "quality_score_source": (
                "overall_failure_memory_score"
                if any(r["score"].get("overall_failure_memory_score") is not None
                       for r in capability_rows)
                else "legacy_graded_audit_score"
            ),
            "hard_pass_rate": (sum(r["score"].get("hard_pass") is True
                                   for r in capability_rows) / len(capability_rows)
                               if capability_rows else None),
            "total_deployment_tokens": cost if usage and all(r.get("usage_complete", True) for r in usage) else None,
            "cost_scope": "main_questions_and_shared_build_once_excluding_calibration_controls_judge"})
    comparisons = []
    for point in points:
        if (point["total_deployment_tokens"] is None
                or point["overall_failure_memory_score"] is None):
            continue
        peers = [p for p in points if p["model_id"] == point["model_id"]
                 and p["method_id"] != point["method_id"] and p["query_ids"] == point["query_ids"]
                 and p["total_deployment_tokens"] is not None
                 and p["overall_failure_memory_score"] is not None]
        for method in sorted({p["method_id"] for p in peers}):
            candidates = [p for p in peers if p["method_id"] == method]
            under_cost = [p for p in candidates if p["total_deployment_tokens"] <= point["total_deployment_tokens"]]
            best_under_cost = max(
                under_cost,
                key=lambda p: (p["overall_failure_memory_score"], p["audit_pass_rate"]),
                default=None,
            )
            at_quality = [p for p in candidates if (
                p["overall_failure_memory_score"] is not None
                and (p["overall_failure_memory_score"] > point["overall_failure_memory_score"]
                or (
                    p["overall_failure_memory_score"] == point["overall_failure_memory_score"]
                    and p["audit_pass_rate"] >= point["audit_pass_rate"]
                ))
            )]
            comparisons.append({"cell_id": point["cell_id"], "method_id": point["method_id"],
                "peer_method": method,
                "best_observed_quality_within_cost": (
                    best_under_cost["audit_pass_rate"] if best_under_cost else None
                ),
                "best_observed_graded_score_within_cost": (
                    best_under_cost["graded_audit_score"] if best_under_cost else None
                ),
                "best_observed_failure_memory_score_within_cost": (
                    best_under_cost["overall_failure_memory_score"]
                    if best_under_cost else None
                ),
                "least_observed_cost_at_quality": min((p["total_deployment_tokens"] for p in at_quality), default=None),
                "quality_order": ["overall_failure_memory_score", "audit_pass_rate"],
                "interpolation": False, "matched_question_coverage": point["coverage"]})
    return {"points": points, "comparisons": comparisons, "development_only": True,
            "independent_validation": False, "offline_costs_are_unknown": True}
