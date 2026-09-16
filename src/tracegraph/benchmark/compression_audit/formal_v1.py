"""Freeze the real-trajectory annotation population for compression_audit_v1.

This module deliberately stops before creating gold.  It mines candidate
windows from pinned source files, selects one trajectory per source task, fixes
repository-disjoint splits, and emits two identical but separate blank review
packets for independent annotation.
"""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ...compression_audit_real import (
    load_source_rows,
    mine_ama_bench_candidates,
    mine_swe_gym_candidates,
)
from .build import load_config
from .io import canonical_json, file_sha256, stable_digest


SOURCE_SPLIT_QUOTAS = {
    "swe_gym": {"dev": 12, "validation": 12, "test": 36},
    "ama_bench": {"dev": 8, "validation": 8, "test": 24},
}


def _write_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def _deduplicate_source_tasks(rows: Sequence[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Keep one label-free, deterministic trajectory per source task."""

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["source"]), str(row["repository"]), str(row["task_id"]))].append(row)
    selected = []
    for task_key, candidates in grouped.items():
        selected.append(
            min(
                candidates,
                key=lambda item: stable_digest(
                    {"seed": seed, "task": task_key, "candidate_id": item["candidate_id"]}
                ),
            )
        )
    return selected


def _repository_assignment(
    rows: Sequence[dict[str, Any]], quotas: Mapping[str, int], seed: int
) -> dict[str, str]:
    counts = Counter(str(row["repository"]) for row in rows)
    if len(counts) < len(quotas):
        raise ValueError("formal split requires at least one repository per split")
    repositories = sorted(
        counts,
        key=lambda repository: (
            -counts[repository],
            stable_digest({"seed": seed, "repository": repository}),
        ),
    )
    split_order = ("test", "dev", "validation")
    assignment: dict[str, str] = {}
    capacities = {split: 0 for split in quotas}
    for index, repository in enumerate(repositories):
        if index < len(split_order):
            split = split_order[index]
        else:
            split = min(
                quotas,
                key=lambda value: (
                    capacities[value] / quotas[value],
                    stable_digest({"seed": seed, "repository": repository, "split": value}),
                ),
            )
        assignment[repository] = split
        capacities[split] += counts[repository]
    insufficient = {
        split: {"available": capacities[split], "required": quota}
        for split, quota in quotas.items()
        if capacities[split] < quota
    }
    if insufficient:
        raise ValueError(f"repository-disjoint split lacks candidates: {insufficient}")
    return assignment


def _sample_split(
    rows: Sequence[dict[str, Any]], quota: int, *, source: str, split: str, seed: int
) -> list[dict[str, Any]]:
    by_repository: dict[str, deque[dict[str, Any]]] = {}
    for repository in sorted(
        {str(row["repository"]) for row in rows},
        key=lambda value: stable_digest(
            {"seed": seed, "source": source, "split": split, "repository": value}
        ),
    ):
        ranked = sorted(
            (row for row in rows if str(row["repository"]) == repository),
            key=lambda row: stable_digest(
                {"seed": seed, "source": source, "split": split,
                 "candidate_id": row["candidate_id"]}
            ),
        )
        by_repository[repository] = deque(ranked)
    selected: list[dict[str, Any]] = []
    repositories = list(by_repository)
    while len(selected) < quota:
        progressed = False
        for repository in repositories:
            if by_repository[repository] and len(selected) < quota:
                selected.append(by_repository[repository].popleft())
                progressed = True
        if not progressed:
            raise ValueError(f"not enough {source}/{split} candidates for quota {quota}")
    return selected


def freeze_population(rows: Sequence[dict[str, Any]], *, seed: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select exactly 100 tasks with fixed source quotas and repository isolation."""

    unique = _deduplicate_source_tasks(rows, seed)
    selected: list[dict[str, Any]] = []
    assignments: dict[str, dict[str, str]] = {}
    for source, quotas in SOURCE_SPLIT_QUOTAS.items():
        source_rows = [row for row in unique if row.get("source") == source]
        assignment = _repository_assignment(source_rows, quotas, seed)
        assignments[source] = assignment
        for split, quota in quotas.items():
            eligible = [
                row for row in source_rows
                if assignment[str(row["repository"])] == split
            ]
            sampled = _sample_split(
                eligible, quota, source=source, split=split, seed=seed
            )
            for rank, original in enumerate(sampled, 1):
                row = copy.deepcopy(original)
                row["split"] = split
                row["selection"] = {
                    "seed": seed,
                    "source_split_quota": quota,
                    "within_source_split_rank": rank,
                    "used_annotation_labels": False,
                    "used_benchmark_method_scores": False,
                    "candidate_mining_used_source_trajectory_outcome": True,
                }
                selected.append(row)
    selected.sort(key=lambda row: (str(row["split"]), str(row["source"]), str(row["candidate_id"])))
    repositories_by_split = {
        split: sorted({str(row["repository"]) for row in selected if row["split"] == split})
        for split in ("dev", "validation", "test")
    }
    leakage = set(repositories_by_split["dev"]) & set(repositories_by_split["validation"])
    leakage |= set(repositories_by_split["dev"]) & set(repositories_by_split["test"])
    leakage |= set(repositories_by_split["validation"]) & set(repositories_by_split["test"])
    if (leakage or len(selected) != 100
            or len({(row["source"], row["task_id"]) for row in selected}) != 100):
        raise AssertionError("formal population invariants failed")
    report = {
        "selection_seed": seed,
        "selected_count": len(selected),
        "source_counts": dict(Counter(str(row["source"]) for row in selected)),
        "split_counts": dict(Counter(str(row["split"]) for row in selected)),
        "source_split_counts": {
            source: dict(Counter(str(row["split"]) for row in selected if row["source"] == source))
            for source in SOURCE_SPLIT_QUOTAS
        },
        "unique_source_tasks": len({(row["source"], row["task_id"]) for row in selected}),
        "repositories_by_split": repositories_by_split,
        "repository_leakage": sorted(leakage),
        "repository_assignment": assignments,
        "selection_observed_annotations": False,
        "selection_observed_benchmark_method_results": False,
        "candidate_mining_used_source_trajectory_outcome": True,
    }
    return selected, report


def _review_case(row: Mapping[str, Any]) -> dict[str, Any]:
    case = copy.deepcopy(dict(row))
    case.pop("candidate_hints", None)
    case["failure_chain"] = {
        "failure_family": "",
        "failed_action": "",
        "failed_arguments": {},
        "error_signature": "",
        "diagnostic_evidence": "",
        "switch_decision": "",
        "replacement_action": "",
        "replacement_arguments": {},
        "resolution_evidence": "",
        "ordered_source_event_ids": [],
        "evidence_source_event_ids_by_field": {},
        "recoverability": "",
    }
    case["annotator"] = ""
    case["adjudicator"] = ""
    case["annotation_status"] = "blank"
    return case


def _review_template(row: Mapping[str, Any]) -> dict[str, Any]:
    case = _review_case(row)
    return {
        key: case[key]
        for key in (
            "candidate_id", "source", "repository", "task_id", "split",
            "trajectory_revision", "failure_chain", "annotator",
            "annotation_status",
        )
    }


def prepare_annotation_packets(
    *, config_path: Path, swe_sources: Sequence[Path], ama_source: Path,
    output_root: Path, seed: int
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"annotation output already exists: {output_root}")
    config = load_config(config_path)
    revisions = {
        str(item["id"]): str(item["trajectory_revision"])
        for item in config["real"]["sources"]
    }
    source_files: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for source in sorted(path.resolve() for path in swe_sources):
        digest = file_sha256(source)
        rows = load_source_rows(source)
        candidates.extend(
            mine_swe_gym_candidates(
                rows,
                revision=revisions["swe_gym_openhands"],
                source_file_sha256=digest,
            )
        )
        source_files.append({"source": "swe_gym", "path": str(source),
                             "bytes": source.stat().st_size, "sha256": digest,
                             "row_count": len(rows)})
    ama_digest = file_sha256(ama_source)
    ama_rows = load_source_rows(ama_source)
    candidates.extend(
        mine_ama_bench_candidates(
            ama_rows,
            revision=revisions["ama_bench"],
            source_file_sha256=ama_digest,
        )
    )
    source_files.append({"source": "ama_bench", "path": str(ama_source.resolve()),
                         "bytes": ama_source.stat().st_size, "sha256": ama_digest,
                         "row_count": len(ama_rows)})
    selected, selection = freeze_population(candidates, seed=seed)
    output_root.mkdir(parents=True)
    _write_rows(output_root / "candidates.jsonl", selected)
    for name in ("annotator_a.jsonl", "annotator_b.jsonl", "adjudicated.jsonl"):
        (output_root / name).write_text("", encoding="utf-8")
    cases = [_review_case(row) for row in selected]
    readme = (
        "# compression_audit_v1 独立轨迹标注包\n\n"
        "两位标注者必须分别工作，不交换答案。将 `reviews.template.jsonl` 复制为"
        " `reviews.completed.jsonl`，逐条填写 failure_chain、annotator，并把"
        " annotation_status 改为 `annotated`。不得使用 candidates.jsonl 中的启发式提示；"
        " reviewer packet 已移除这些提示。测试 split 标签是预先冻结的分组，不代表答案。\n\n"
        "每个 evidence ID 必须来自该条 prefix.events 的 source_event_id。完整链至少包含："
        "失败动作、失败结果、诊断证据、切换决定、替代动作、成功证据。"
        "failed_action 与 replacement_action 必须逐字等于各自所选 tool_call 事件的 "
        "tool_name；failed_arguments 与 replacement_arguments 必须逐字段等于该事件 "
        "content.arguments，不得写解释性短语、增加 tool/wrapped_tool/_verb 等字段，"
        "也不得把数组改写成字符串。每个动作字段的 evidence 列表中，第一个 tool_call "
        "就是该精确值的来源。"
        "如果轨迹不能支持完整链，不要猜测；将 annotation_status 设为 `rejected` 并说明原因。\n"
        "SWE-Gym 轨迹数据卡未声明数据许可；此包仅供受控研究标注，不得公开转发。\n"
    )
    for reviewer in ("reviewer_a", "reviewer_b"):
        packet = output_root / "_packet" / reviewer
        packet.mkdir(parents=True)
        _write_rows(packet / "cases.jsonl", cases)
        _write_rows(packet / "reviews.template.jsonl", (_review_template(row) for row in selected))
        (packet / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": "compression_audit_v1_annotation_freeze_v1",
        "benchmark_id": "compression_audit_v1",
        "formal_release_target": "v1.0",
        "gold_created": False,
        "double_human_annotation_complete": False,
        "adjudication_complete": False,
        "split_assignment_frozen_before_annotation": True,
        "formal_test_membership_frozen": False,
        "config_path": str(config_path.resolve()),
        "config_sha256": file_sha256(config_path),
        "source_files": source_files,
        "candidate_pool_count": len(candidates),
        "selection": selection,
        "candidates_sha256": file_sha256(output_root / "candidates.jsonl"),
    }
    (output_root / "selection_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
