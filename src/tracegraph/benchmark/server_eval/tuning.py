"""Predeclared ACON development guidance sweep, using official runtime optimizers."""

import copy
import json
from pathlib import Path

from ..compression_audit.development_experiment import write_json
from ..compression_audit.development_results import episode_run_validity
from ..compression_audit.io import stable_digest
from .config import load_config
from .prepare import prepare

GUIDANCE = {
    "original": "",
    "evidence": "Preserve resolvable source pointers next to each factual claim. Remove duplicated prose first.",
    "causal": "Keep explicit failure, diagnostic evidence, decision and resolution links with source pointers. "
              "Do not infer a resolution merely from a later success. Preserve current-state overrides.",
}


def prepare_tuning(config_path: Path, dataset: Path, output: Path, workspace: Path):
    config = load_config(config_path)
    if config["data"]["split"] != "dev" or output.exists():
        raise ValueError("guidance tuning requires dev split and new output")
    output.mkdir(parents=True)
    summaries = []
    for name, text in GUIDANCE.items():
        candidate = copy.deepcopy(config)
        candidate["methods"] = ["full_history", "acon_official"]
        candidate["interactive"] = False
        candidate["acon_guidance"] = {"candidate": name, "text": text, "development_only": True}
        path = output / (name + ".json")
        write_json(path, candidate)
        summaries.append({"candidate": name, "config_hash": stable_digest(candidate),
                          **prepare(path, dataset, output / name, workspace)})
    result = {"candidates": summaries,
              "selection_rule": "maximize_overall_failure_memory_score_then_audit_pass_then_minimize_deployment_tokens",
              "total_request_upper_bound": sum(r["request_upper_bound"] for r in summaries),
              "official_guidance_training_reproduction": False, "development_only": True,
              "independent_validation": False, "provider_requests": 0}
    write_json(output / "tuning_plan.json", result)
    return result


def select_guidance(plan: Path, runs: Path, output: Path):
    frozen = json.loads((plan / "tuning_plan.json").read_text(encoding="utf-8"))
    scores = []
    for candidate in frozen["candidates"]:
        name = candidate["candidate"]
        report = json.loads((runs / name / "report.json").read_text(encoding="utf-8"))
        identity = json.loads((runs / name / "identity.json").read_text(encoding="utf-8"))
        if identity["config_hash"] != candidate["config_hash"] or report["mode"] != "live":
            raise ValueError("guidance selection requires matching real development runs")
        if report["completed_episodes"] != report["planned_episodes"] or not all(
                c["calibration"]["pass"] for c in report["cells"]):
            raise ValueError("all candidates must finish with calibration gates passed")
        from ..compression_audit.io import load_jsonl
        rows = [r for r in load_jsonl(runs / name / "episodes.jsonl")
                if r["phase"] == "main" and r["method_id"] == "acon_official"]
        if any(episode_run_validity(row) == "integration_invalid" for row in rows):
            raise ValueError("guidance selection cannot use integration-invalid episodes")
        tokens = sum(r["input_tokens"] + r["output_tokens"] for r in report["deployment_usage_by_method"]
                     if r["method_id"] == "acon_official")
        overall = [r["score"].get(
            "overall_failure_memory_score",
            r["score"].get("graded_audit_score", 100.0 if r["score"].get("hard_pass") else 0.0),
        ) for r in rows]
        scores.append({"candidate": name, "hard_pass": sum(r["score"]["hard_pass"] for r in rows),
                       "audit_pass": sum(r["score"].get("audit_pass", r["score"]["hard_pass"])
                                         for r in rows),
                       "overall_failure_memory_score": sum(map(float, overall)) / len(overall),
                       "graded_audit_score": (sum(float(r["score"].get("graded_audit_score", 0.0))
                                                  for r in rows) / len(rows) if rows else 0.0),
                       "questions": len(rows), "deployment_tokens": tokens})
    if len({r["questions"] for r in scores}) != 1 or not scores[0]["questions"]:
        raise ValueError("candidate coverage differs")
    chosen = sorted(scores, key=lambda r: (-r["overall_failure_memory_score"],
                                           -r["audit_pass"],
                                           r["deployment_tokens"], r["candidate"]))[0]
    if output.exists():
        raise ValueError("selection receipt must be new")
    result = {"selected": chosen["candidate"], "scores": scores, "development_only": True,
              "independent_validation": False, "guidance": GUIDANCE[chosen["candidate"]]}
    write_json(output, result)
    return result
