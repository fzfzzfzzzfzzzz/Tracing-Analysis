"""Review workitems and external dataset provenance; never fabricate gold or reviews."""

import json
from pathlib import Path

from ..compression_audit.artifacts import load_dataset
from ..compression_audit.build import verify_file_manifest, write_file_manifest
from ..compression_audit.development_experiment import write_json, write_rows
from ..compression_audit.development_scoring import make_rubric
from ..compression_audit.io import file_sha256, stable_digest, load_jsonl
from .rubrics import import_rubrics


def load_suite_dataset(dataset, config, workspace):
    rows = load_dataset(dataset, legacy=False)
    spec = config["data"].get("calibration_dataset")
    if not spec:
        return rows
    root = workspace / spec["path"]
    verify_file_manifest(root)
    if file_sha256(root / "manifest.json") != spec["manifest_sha256"]:
        raise ValueError("calibration dataset changed")
    calibration = load_dataset(root, legacy=False)
    ids = {p.prefix_id for p in calibration[0] if p.split == "dev"}
    if ids & {p.prefix_id for p in rows[0]}:
        raise ValueError("calibration and evaluation prefix IDs overlap")
    return tuple(list(a) + [r for r in b if r.prefix_id in ids] for a, b in zip(rows, calibration))


def export_mini_trace(run: Path, output: Path):
    from dataclasses import replace
    from .mini import prefix_from_messages
    if output.exists():
        raise ValueError("trace export requires new output")
    identity = json.loads((run / "identity.json").read_text(encoding="utf-8"))
    trajectory = json.loads((run / "trajectory.json").read_text(encoding="utf-8"))
    result = json.loads((run / "task_result.json").read_text(encoding="utf-8"))
    checkpoints = sorted(run.glob("checkpoint-*.json"))
    if not checkpoints:
        raise ValueError("completed real trace requires a filesystem checkpoint")
    checkpoint = json.loads(checkpoints[-1].read_text(encoding="utf-8"))
    prefix = prefix_from_messages(trajectory["messages"], identity["task_id"], identity["budget"])
    if prefix is None:
        raise ValueError("trace contains no public events")
    prefix = replace(prefix, source_ref={"source_task_id": identity["task_id"],
        "trajectory_sha256": file_sha256(run / "trajectory.json"),
        "checkpoint_sha256": file_sha256(checkpoints[-1])},
        environment_snapshot={"image": checkpoint["image"], "checkpoint_hash": checkpoint["checkpoint_hash"]})
    output.mkdir(parents=True)
    write_rows(output / "prefixes.unannotated.jsonl", [prefix.to_dict()])
    write_json(output / "annotation_workitem.json", {"prefix_id": prefix.prefix_id,
        "status": "pending_external_rubric_and_queries", "task_success": result["task_success"],
        "failure_chain": None, "gold": None, "recoverability_label": None,
        "provisional_prefix_recoverability": "R0 is a transport placeholder, not an annotation",
        "human_validated": False, "development_only": True, "independent_validation": False})
    return {"exported_prefixes": 1, "annotated_prefixes": 0, "provider_requests": 0}


def review_export(dataset: Path, output: Path):
    if output.exists():
        raise ValueError("review export requires new output")
    verify_file_manifest(dataset)
    prefixes, queries, gold = load_dataset(dataset, legacy=False)
    gold_map = {g.prefix_id: g for g in gold}
    output.mkdir(parents=True)
    write_rows(output / "rubrics.draft.jsonl", [make_rubric(q, gold_map[q.prefix_id]) for q in queries])
    write_rows(output / "review_workitems.jsonl", [{"prefix_id": p.prefix_id,
        "public_content_hash": stable_digest([e["content"] for e in p.events]),
        "status": "pending_external_annotation", "reviewer": None,
        "review_fields": ["necessary_facts", "contradictory_facts", "alternative_evidence_sets",
                          "causal_paths", "strict_chain", "chain_applicable"],
        "source_ref": dict(p.source_ref)} for p in prefixes])
    return {"workitems": len(prefixes), "human_reviews": 0, "provider_requests": 0}


def import_dataset(source: Path, rubrics: Path, provenance: Path, output: Path, exposure: Path):
    if output.exists() or output.resolve().is_relative_to(source.resolve()):
        raise ValueError("import requires separate new output")
    verify_file_manifest(source)
    prefixes, queries, gold = load_dataset(source, legacy=False)
    declarations = load_jsonl(provenance)
    refs = {r["prefix_id"]: r for r in declarations}
    if len(refs) != len(declarations) or set(refs) != {p.prefix_id for p in prefixes}:
        raise ValueError("one provenance record is required for every prefix")
    exposed = {r["public_content_hash"] for r in load_jsonl(exposure)}
    splits, real_tasks = {}, set()
    for p in prefixes:
        r = refs[p.prefix_id]
        fingerprint = stable_digest([e["content"] for e in p.events])
        if r["prefix_hash"] != p.prefix_hash:
            raise ValueError("provenance prefix hash differs")
        if p.split != "dev" and fingerprint in exposed:
            raise ValueError("held-out content has previous exposure")
        source_task = r["source_task_id"]
        if source_task in splits and splits[source_task] != p.split:
            raise ValueError("source task crosses dataset splits")
        splits[source_task] = p.split
        if r.get("source_kind") == "real_checkpoint":
            cp = provenance.parent / r["checkpoint_path"]
            if file_sha256(cp) != r["checkpoint_sha256"]:
                raise ValueError("real checkpoint source changed")
            checkpoint = json.loads(cp.read_text(encoding="utf-8"))
            if stable_digest({k: v for k, v in checkpoint.items() if k != "checkpoint_hash"}) != checkpoint["checkpoint_hash"]:
                raise ValueError("invalid checkpoint")
            real_tasks.add(source_task)
    imported = import_rubrics(rubrics, queries, {p.prefix_id: p for p in prefixes},
                              {g.prefix_id: g for g in gold})
    if {r["query_id"] for r in load_jsonl(rubrics)} != {q.query_id for q in queries}:
        raise ValueError("external dataset requires complete external rubrics")
    output.mkdir(parents=True)
    (output / "public").mkdir()
    (output / "private").mkdir()
    write_rows(output / "public/prefixes.jsonl", [p.to_dict() for p in prefixes])
    write_rows(output / "public/queries.jsonl", [q.to_dict() for q in queries])
    write_rows(output / "private/all_gold.jsonl", [g.to_dict() for g in gold])
    write_rows(output / "private/rubrics.jsonl", list(imported.values()))
    write_rows(output / "provenance.jsonl", declarations)
    result = {"benchmark_id": "compression_audit_v1", "development_only": True,
        "independent_validation": False, "real_source_tasks": len(real_tasks),
        "real_100_threshold_met": len(real_tasks) >= 100, "human_validation_claim": False,
        "source_manifest_sha256": file_sha256(source / "manifest.json"), "provider_requests": 0}
    write_json(output / "manifest.json", result)
    write_file_manifest(output)
    return result
