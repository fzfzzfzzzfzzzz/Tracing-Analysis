"""Independent offline scoring of a saved v0.2 pilot, without new judge calls."""

from __future__ import annotations

import json
from pathlib import Path

from .build import verify_file_manifest, write_file_manifest
from .development_experiment import load_pilot_config, write_json
from .development_results import judge_gate, write_results
from .development_scoring import score_submission
from .io import file_sha256, load_jsonl, stable_digest


def rescore_pilot(dataset: Path, run: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("rescore output already exists")
    if any(output.resolve().is_relative_to(root.resolve()) for root in (dataset, run)):
        raise ValueError("rescore must not change immutable inputs")
    verify_file_manifest(dataset)
    verify_file_manifest(run)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if file_sha256(dataset / "manifest.json") != manifest["dataset_manifest_sha256"]:
        raise ValueError("scoring dataset differs from run")
    config = load_pilot_config(run / "config.snapshot.json")
    rubrics = json.loads((run / "rubrics.snapshot.json").read_text(encoding="utf-8"))
    jobs = load_jsonl(run / "completed_jobs.jsonl")
    if len({j["job_id"] for j in jobs}) != len(jobs) or any(
            stable_digest(j["result"]) != j["result_hash"] for j in jobs):
        raise ValueError("completed job identity invalid")
    examples = [j["result"] for j in jobs if j["kind"] == "example"]
    originals = {e["example_id"]: e for e in json.loads(
        (run / "rule_examples.snapshot.json").read_text(encoding="utf-8"))}
    for example in examples:
        original = originals[example["example_id"]]
        score = score_submission(original["answer"], original["rubric"],
            [r["record_id"] for r in original["records"]], judge=example["judge"],
            judge_calibrated=True)
        example["predicted_pass"] = score["audit_pass"]
        example["score"] = score
    calibrated = judge_gate(examples, config["gates"])["pass"]
    episodes = load_jsonl(run / "episodes.jsonl")
    for episode in episodes:
        rubric = rubrics[episode["query_id"]]
        if stable_digest({k: v for k, v in rubric.items() if k != "rubric_hash"}) != rubric["rubric_hash"]:
            raise ValueError("scoring rubric hash differs")
        episode["score"] = score_submission(episode["answer"], rubric, episode["final_visible_ids"],
            judge=episode["judge"], judge_calibrated=calibrated, status=episode["status"],
            executed_side_effects=sum(bool(t.get("executed_side_effect")) for t in episode["tool_calls"]),
            unsafe_attempts=sum(bool(t.get("unsafe_side_effect_attempt")) for t in episode["tool_calls"]))
        if episode.get("incomplete"):
            episode["score"]["failure_labels"].append("provider_or_budget_interruption")
    ledger = load_jsonl(run / "provider_ledger.jsonl") if (run / "provider_ledger.jsonl").exists() else []
    prior = json.loads((run / "report.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True)
    report = write_results(output, episodes, examples, ledger, config=config,
                           mode=manifest["mode"], stop_reason=prior["stop_reason"])
    write_json(output / "manifest.json", {"protocol": "v0.2-development",
        "run_manifest_sha256": file_sha256(run / "manifest.json"),
        "dataset_manifest_sha256": file_sha256(dataset / "manifest.json"),
        "development_only": True, "independent_validation": False,
        "artifacts": write_file_manifest(output)})
    return report
