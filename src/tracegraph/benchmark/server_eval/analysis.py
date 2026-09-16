"""Observed budget frontiers; no invented interpolation or aggregate leaderboard."""

from collections import defaultdict


def budget_frontiers(episodes, costs):
    groups = defaultdict(list)
    for row in episodes:
        if row["phase"] == "main":
            groups[(row["cell_id"], row["method_id"])].append(row)
    points = []
    for (cell, method), rows in sorted(groups.items()):
        usage = [r for r in costs if r["cell_id"] == cell and r["method_id"] == method]
        cost = sum(r["input_tokens"] + r["output_tokens"] for r in usage)
        points.append({"cell_id": cell, "model_id": cell.rsplit("-b", 1)[0],
            "budget": int(cell.rsplit("-b", 1)[1]), "method_id": method,
            "query_ids": sorted(r["query_id"] for r in rows), "coverage": len(rows),
            "audit_pass_rate": sum(r["score"].get("audit_pass") is True for r in rows) / len(rows),
            "hard_pass_rate": sum(r["score"].get("hard_pass") is True for r in rows) / len(rows),
            "total_deployment_tokens": cost if usage and all(r.get("usage_complete", True) for r in usage) else None,
            "cost_scope": "main_questions_and_shared_build_once_excluding_calibration_controls_judge"})
    comparisons = []
    for point in points:
        if point["total_deployment_tokens"] is None:
            continue
        peers = [p for p in points if p["model_id"] == point["model_id"]
                 and p["method_id"] != point["method_id"] and p["query_ids"] == point["query_ids"]
                 and p["total_deployment_tokens"] is not None]
        for method in sorted({p["method_id"] for p in peers}):
            candidates = [p for p in peers if p["method_id"] == method]
            under_cost = [p for p in candidates if p["total_deployment_tokens"] <= point["total_deployment_tokens"]]
            at_quality = [p for p in candidates if p["audit_pass_rate"] >= point["audit_pass_rate"]]
            comparisons.append({"cell_id": point["cell_id"], "method_id": point["method_id"],
                "peer_method": method,
                "best_observed_quality_within_cost": max((p["audit_pass_rate"] for p in under_cost), default=None),
                "least_observed_cost_at_quality": min((p["total_deployment_tokens"] for p in at_quality), default=None),
                "interpolation": False, "matched_question_coverage": point["coverage"]})
    return {"points": points, "comparisons": comparisons, "development_only": True,
            "independent_validation": False, "offline_costs_are_unknown": True}
