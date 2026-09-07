"""Definitions moved from ``tracegraph.compression_audit``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from ...capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens

from .constants import (
    FAILURE_ROLES as FAILURE_ROLES,
    RECOVERABILITY_LEVELS as RECOVERABILITY_LEVELS,
    SPLITS as SPLITS,
)



def validate_real_annotations(
    annotation_root: Path,
    *,
    expected_revisions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    paths = {
        name: annotation_root / f"{name}.jsonl"
        for name in ("candidates", "annotator_a", "annotator_b", "adjudicated")
    }
    if not all(path.is_file() for path in paths.values()):
        return {
            "ready": False,
            "reason": "annotation_files_missing",
            "adjudicated_count": 0,
        }
    rows = {name: load_jsonl(path) for name, path in paths.items()}
    keyed = {
        name: {str(item.get("candidate_id")): item for item in values}
        for name, values in rows.items()
    }
    duplicate_ids = {
        name: len(values) != len(keyed[name]) for name, values in rows.items()
    }
    annotation_ids = set(keyed["annotator_a"])
    shared = sorted(annotation_ids.intersection(keyed["annotator_b"]))
    selected_ids = set(keyed["adjudicated"])
    candidate_provenance_complete = bool(selected_ids) and selected_ids.issubset(
        set(keyed["candidates"])
    )
    category_fields = ("failure_family", "recoverability")
    category_kappas = {
        field_name: _cohen_kappa(
            [
                str(
                    keyed["annotator_a"][key]
                    .get("failure_chain", {})
                    .get(field_name, "")
                )
                for key in shared
            ],
            [
                str(
                    keyed["annotator_b"][key]
                    .get("failure_chain", {})
                    .get(field_name, "")
                )
                for key in shared
            ],
        )
        for field_name in category_fields
    }

    def annotation_evidence(item: Mapping[str, Any]) -> tuple[str, ...]:
        chain = item.get("failure_chain", {})
        values = list(chain.get("ordered_source_event_ids", ()))
        explicit = chain.get("evidence_source_event_ids_by_field", {})
        if isinstance(explicit, dict):
            for event_ids in explicit.values():
                values.extend(event_ids or ())
        return _tuple_strings(values)

    evidence_f1 = [
        _event_f1(
            annotation_evidence(keyed["annotator_a"][key]),
            annotation_evidence(keyed["annotator_b"][key]),
        )
        for key in shared
    ]
    adjudicated = list(keyed["adjudicated"].values())
    swe = sum(item.get("source") == "swe_gym" for item in adjudicated)
    ama = sum(item.get("source") == "ama_bench" for item in adjudicated)
    replayable = sum(
        item.get("source") == "swe_gym"
        and bool(item.get("replay", {}).get("docker_replayable"))
        for item in adjudicated
    )

    def replay_verified(item: Mapping[str, Any]) -> bool:
        replay = item.get("replay", {})
        if item.get("source") != "swe_gym" or not replay.get("docker_replayable"):
            return False
        receipt = replay.get("verification", {})
        if not isinstance(receipt, dict):
            return False
        log_ref = str(receipt.get("log_path") or "")
        log_path = (annotation_root / log_ref).resolve()
        if (
            not log_ref
            or not log_path.is_relative_to(annotation_root.resolve())
            or not log_path.is_file()
        ):
            return False
        return bool(
            receipt.get("status") == "passed"
            and receipt.get("exit_code") == 0
            and "@sha256:" in str(receipt.get("image_ref") or "")
            and isinstance(receipt.get("command"), list)
            and receipt["command"]
            and receipt.get("log_sha256") == file_sha256(log_path)
            and str(replay.get("snapshot_ref") or "").strip()
        )

    replay_verified_count = sum(replay_verified(item) for item in adjudicated)
    minimum_kappa = (
        min(float(value) for value in category_kappas.values() if value is not None)
        if category_kappas and all(value is not None for value in category_kappas.values())
        else None
    )
    mean_f1 = sum(evidence_f1) / len(evidence_f1) if evidence_f1 else None

    def adjudicated_complete(item: Mapping[str, Any]) -> bool:
        chain = item.get("failure_chain", {})
        prefix = item.get("prefix", {})
        events = prefix.get("events", ()) if isinstance(prefix, dict) else ()
        event_ids = {
            str(event.get("source_event_id") or event.get("event_id") or "")
            for event in events
            if isinstance(event, dict)
        }
        event_order = {
            str(event.get("source_event_id") or event.get("event_id") or ""): index
            for index, event in enumerate(events)
            if isinstance(event, dict)
        }
        ordered = _tuple_strings(chain.get("ordered_source_event_ids", ()))
        required_roles = set(FAILURE_ROLES)
        observed_roles = {
            str(event.get("causal_role"))
            for event in events
            if isinstance(event, dict) and event.get("causal_role")
        }
        explicit = chain.get("evidence_source_event_ids_by_field", {})
        explicit_ids = {
            str(event_id)
            for values in explicit.values()
            for event_id in (values or ())
        } if isinstance(explicit, dict) else set()
        required_evidence_fields = {
            "failed_action",
            "failure_cause",
            "diagnostic_evidence",
            "switch_decision",
            "replacement_action",
            "resolution_evidence",
        }
        explicit_complete = bool(
            isinstance(explicit, dict)
            and required_evidence_fields.issubset(
                {str(key) for key, values in explicit.items() if values}
            )
        )
        return bool(
            item.get("annotation_status") == "adjudicated"
            and str(item.get("adjudicator") or "").strip()
            and str(item.get("repository") or "").strip()
            and str(item.get("task_id") or "").strip()
            and len(str(item.get("trajectory_revision") or "")) >= 40
            and all(
                str(chain.get(field, "")).strip()
                for field in (
                    "failure_family",
                    "failed_action",
                    "error_signature",
                    "diagnostic_evidence",
                    "switch_decision",
                    "replacement_action",
                    "resolution_evidence",
                    "recoverability",
                )
            )
            and chain.get("recoverability") in RECOVERABILITY_LEVELS
            and len(ordered) >= 5
            and len(set(ordered)) == len(ordered)
            and set(ordered).issubset(event_ids)
            and [event_order.get(event_id, -1) for event_id in ordered]
            == sorted(event_order.get(event_id, -1) for event_id in ordered)
            and isinstance(chain.get("failed_arguments"), dict)
            and isinstance(chain.get("replacement_arguments"), dict)
            and explicit_ids.issubset(event_ids)
            and (required_roles.issubset(observed_roles) or explicit_complete)
        )

    complete = bool(adjudicated) and all(adjudicated_complete(item) for item in adjudicated)
    independent_annotators = bool(shared) and all(
        str(keyed["annotator_a"][key].get("annotator") or "").strip()
        and str(keyed["annotator_b"][key].get("annotator") or "").strip()
        and str(keyed["annotator_a"][key].get("annotator"))
        != str(keyed["annotator_b"][key].get("annotator"))
        and keyed["annotator_a"][key].get("annotation_status") == "annotated"
        and keyed["annotator_b"][key].get("annotation_status") == "annotated"
        for key in shared
    )
    annotation_hashes_linked = bool(selected_ids) and all(
        str(keyed["adjudicated"][key].get("annotator_a_sha256") or "")
        == stable_digest(keyed["annotator_a"][key])
        and str(keyed["adjudicated"][key].get("annotator_b_sha256") or "")
        == stable_digest(keyed["annotator_b"][key])
        for key in selected_ids.intersection(shared)
    )
    metadata_aligned = bool(selected_ids) and all(
        all(
            keyed[name][key].get(field_name)
            == keyed["adjudicated"][key].get(field_name)
            for name in ("annotator_a", "annotator_b")
            for field_name in (
                "source",
                "repository",
                "task_id",
                "trajectory_revision",
            )
        )
        for key in selected_ids.intersection(shared)
    )
    split_counts = {
        split: sum(item.get("split") == split for item in adjudicated) for split in SPLITS
    }
    repositories_by_split = {
        split: {
            str(item.get("repository"))
            for item in adjudicated
            if item.get("split") == split
        }
        for split in SPLITS
    }
    repository_leakage = sorted(
        {
            repository
            for index, left in enumerate(SPLITS)
            for right in SPLITS[index + 1 :]
            for repository in repositories_by_split[left].intersection(
                repositories_by_split[right]
            )
        }
    )
    tasks_by_split = {
        split: {
            (str(item.get("repository")), str(item.get("task_id")))
            for item in adjudicated
            if item.get("split") == split
        }
        for split in SPLITS
    }
    task_leakage = sorted(
        {
            task
            for index, left in enumerate(SPLITS)
            for right in SPLITS[index + 1 :]
            for task in tasks_by_split[left].intersection(tasks_by_split[right])
        }
    )
    exact_annotation_sets = bool(
        len(rows["annotator_a"]) == 100
        and len(rows["annotator_b"]) == 100
        and len(adjudicated) == 100
        and annotation_ids == set(keyed["annotator_b"]) == selected_ids
    )
    pinned_revisions_match = all(
        not expected_revisions
        or str(item.get("trajectory_revision") or "")
        == str(expected_revisions.get(str(item.get("source")), ""))
        for item in adjudicated
    )
    return {
        "ready": bool(
            exact_annotation_sets
            and not any(duplicate_ids.values())
            and swe == 60
            and ama == 40
            and replayable == 60
            and replay_verified_count == 60
            and split_counts == {"dev": 20, "validation": 20, "test": 60}
            and not repository_leakage
            and not task_leakage
            and minimum_kappa is not None
            and minimum_kappa >= 0.80
            and mean_f1 is not None
            and mean_f1 >= 0.90
            and complete
            and independent_annotators
            and annotation_hashes_linked
            and metadata_aligned
            and candidate_provenance_complete
            and pinned_revisions_match
        ),
        "shared_double_annotations": len(shared),
        "adjudicated_count": len(adjudicated),
        "swe_gym_count": swe,
        "ama_bench_count": ama,
        "swe_gym_replayable_count": replayable,
        "swe_gym_verified_replay_count": replay_verified_count,
        "split_counts": split_counts,
        "repository_leakage": repository_leakage,
        "task_leakage": task_leakage,
        "categorical_cohen_kappa_by_field": category_kappas,
        "minimum_categorical_cohen_kappa": minimum_kappa,
        "recoverability_cohen_kappa": category_kappas["recoverability"],
        "mean_evidence_event_f1": mean_f1,
        "adjudicated_fields_complete": complete,
        "independent_annotators": independent_annotators,
        "annotation_hashes_linked": annotation_hashes_linked,
        "metadata_aligned": metadata_aligned,
        "candidate_provenance_complete": candidate_provenance_complete,
        "exact_annotation_sets": exact_annotation_sets,
        "pinned_revisions_match": pinned_revisions_match,
        "duplicate_candidate_ids": duplicate_ids,
    }


# Imported after definitions so mutually-referential helpers initialize safely.
from .io import (
    _tuple_strings as _tuple_strings,
    file_sha256 as file_sha256,
    load_jsonl as load_jsonl,
    stable_digest as stable_digest,
)

from .validation_helpers import (
    _cohen_kappa as _cohen_kappa,
    _event_f1 as _event_f1,
)
