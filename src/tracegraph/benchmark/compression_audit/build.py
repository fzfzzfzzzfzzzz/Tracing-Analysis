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
    BENCHMARK_ID as BENCHMARK_ID,
    CONFIG_SCHEMA_VERSION as CONFIG_SCHEMA_VERSION,
    FAILURE_ROLES as FAILURE_ROLES,
    FAILURE_TEMPLATES as FAILURE_TEMPLATES,
    MANIFEST_SCHEMA_VERSION as MANIFEST_SCHEMA_VERSION,
    SCHEMA_VERSION as SCHEMA_VERSION,
    SPLITS as SPLITS,
)



def load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported compression audit config")
    controlled = value.get("controlled", {})
    if int(controlled.get("prefix_count", 0)) != 240:
        raise ValueError("controlled prefix_count must remain 240")
    if int(controlled.get("queries_per_prefix", 0)) != 6:
        raise ValueError("controlled queries_per_prefix must remain 6")
    real = value.get("real", {})
    if int(real.get("swe_gym_quota", 0)) != 60 or int(real.get("ama_bench_quota", 0)) != 40:
        raise ValueError("real source quotas must remain 60 SWE-Gym and 40 AMA-Bench")
    limits = value.get("v0_live", {}).get("limits", {})
    if int(limits.get("request_count_hard_max", 0)) != 368:
        raise ValueError("v0 request_count_hard_max must remain 368")
    if float(limits.get("maximum_cost_cny", 0)) != 100.0:
        raise ValueError("v0 maximum_cost_cny must remain 100")
    return value


def _expected_real_revisions(config: Mapping[str, Any]) -> dict[str, str]:
    by_id = {
        str(item.get("id")): str(item.get("trajectory_revision") or "")
        for item in config.get("real", {}).get("sources", ())
    }
    return {
        "swe_gym": by_id.get("swe_gym_openhands", ""),
        "ama_bench": by_id.get("ama_bench", ""),
    }


def _copy_legacy(legacy_root: Path, destination: Path) -> dict[str, Any]:
    names = (
        "prefixes.jsonl",
        "forks.jsonl",
        "lifecycle_gold.jsonl",
        "manager_outputs.jsonl",
        "manifest.json",
    )
    destination.mkdir(parents=True, exist_ok=True)
    source_hashes: dict[str, str] = {}
    for name in names:
        source = legacy_root / name
        if not source.is_file():
            raise FileNotFoundError(source)
        source_hashes[name] = file_sha256(source)
        shutil.copyfile(source, destination / name)
        if file_sha256(destination / name) != source_hashes[name]:
            raise ValueError(f"legacy copy hash mismatch: {name}")
    wrapper = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "role": "legacy_diagnostic_only",
        "ranked": False,
        "source_root": str(legacy_root),
        "source_hashes": source_hashes,
        "preserves_original_ids": True,
        "preserves_original_results": True,
    }
    _write_json(destination / "compatibility_manifest.json", wrapper)
    return wrapper


def _annotation_template(source: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "candidate_id": "",
        "repository": "",
        "task_id": "",
        "split": "",
        "prefix": {},
        "failure_chain": {
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
        },
        "replay": {
            "docker_replayable": False,
            "snapshot_ref": "",
            "verification_command": "",
        },
        "annotator": "",
        "adjudicator": "",
        "annotator_a_sha256": "",
        "annotator_b_sha256": "",
        "annotation_status": "blank",
    }


def _write_real_annotation_scaffold(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "swe_gym.template.json", _annotation_template("swe_gym"))
    _write_json(destination / "ama_bench.template.json", _annotation_template("ama_bench"))
    for name in (
        "candidates.jsonl",
        "annotator_a.jsonl",
        "annotator_b.jsonl",
        "adjudicated.jsonl",
    ):
        _write_jsonl(destination / name, ())


def _copy_real_annotation_inputs(source: Path, destination: Path) -> bool:
    names = ("candidates.jsonl", "annotator_a.jsonl", "annotator_b.jsonl", "adjudicated.jsonl")
    if not source.is_dir() or not (source / "adjudicated.jsonl").is_file():
        return False
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = source / name
        if not path.is_file():
            raise FileNotFoundError(path)
        shutil.copyfile(path, destination / name)
    for name in ("source_manifest.json", "manifest.json", "file_manifest.jsonl"):
        path = source / name
        if path.is_file():
            shutil.copyfile(path, destination / name)
    for row in load_jsonl(source / "adjudicated.jsonl"):
        receipt = row.get("replay", {}).get("verification", {})
        log_ref = str(receipt.get("log_path") or "") if isinstance(receipt, dict) else ""
        if not log_ref:
            continue
        source_log = (source / log_ref).resolve()
        if not source_log.is_relative_to(source.resolve()) or not source_log.is_file():
            raise ValueError("replay verification log must exist inside the annotation bundle")
        target_log = destination / source_log.relative_to(source.resolve())
        if target_log.exists():
            if file_sha256(target_log) != file_sha256(source_log):
                raise ValueError("replay log collides with another annotation artifact")
        else:
            target_log.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_log, target_log)
    for source_name in ("swe_gym", "ama_bench"):
        template = source / f"{source_name}.template.json"
        if template.is_file():
            shutil.copyfile(template, destination / template.name)
        else:
            _write_json(destination / template.name, _annotation_template(source_name))
    return True


def import_adjudicated_real(
    annotation_root: Path,
) -> tuple[list[PrefixRecord], list[FailureChainGold], list[QueryRecord]]:
    """Convert 100 adjudicated normalized trajectories into opaque benchmark records."""

    rows = load_jsonl(annotation_root / "adjudicated.jsonl")
    prefixes: list[PrefixRecord] = []
    gold_rows: list[FailureChainGold] = []
    queries: list[QueryRecord] = []
    for row in rows:
        if row.get("annotation_status") != "adjudicated":
            raise ValueError("real import accepts adjudicated rows only")
        source = _nonempty(row.get("source"), "real source")
        if source not in {"swe_gym", "ama_bench"}:
            raise ValueError(f"unsupported real source: {source}")
        candidate_id = _nonempty(row.get("candidate_id"), "candidate_id")
        repository = _nonempty(row.get("repository"), "repository")
        task_id = _nonempty(row.get("task_id"), "task_id")
        split = _nonempty(row.get("split"), "real split")
        if split not in SPLITS:
            raise ValueError(f"invalid real split: {split}")
        raw_prefix = row.get("prefix")
        if not isinstance(raw_prefix, dict):
            raise ValueError(f"real prefix must be an object: {candidate_id}")
        raw_events = raw_prefix.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            raise ValueError(f"real prefix requires normalized events: {candidate_id}")
        prefix_id = f"real:{source}:{stable_digest(candidate_id)[:16]}"
        source_ids = []
        for index, event in enumerate(raw_events, 1):
            if not isinstance(event, dict):
                raise ValueError(f"real event must be an object: {candidate_id}/{index}")
            source_ids.append(
                _nonempty(
                    event.get("source_event_id") or event.get("event_id"),
                    "source_event_id",
                )
            )
        if len(set(source_ids)) != len(source_ids):
            raise ValueError(f"duplicate real source event ID: {candidate_id}")
        id_map = {
            source_id: f"{prefix_id}:E{index:03d}"
            for index, source_id in enumerate(source_ids, 1)
        }
        events = []
        for index, (source_id, raw_event) in enumerate(
            zip(source_ids, raw_events, strict=True), 1
        ):
            events.append(
                {
                    "event_id": id_map[source_id],
                    "step_id": index,
                    "kind": _nonempty(raw_event.get("kind"), "real event kind"),
                    "content": raw_event.get("content"),
                    "causal_role": str(raw_event.get("causal_role") or "unclassified"),
                    "token_count": int(
                        raw_event.get("token_count") or estimate_tokens(raw_event.get("content"))
                    ),
                    "side_effect": bool(raw_event.get("side_effect")),
                    **(
                        {"source_message_ordinal": int(raw_event["source_message_ordinal"])}
                        if raw_event.get("source_message_ordinal")
                        else {}
                    ),
                    **(
                        {"call_id": str(raw_event["call_id"])}
                        if raw_event.get("call_id")
                        else {}
                    ),
                    **(
                        {"tool_name": str(raw_event["tool_name"])}
                        if raw_event.get("tool_name")
                        else {}
                    ),
                }
            )
        chain = row.get("failure_chain")
        if not isinstance(chain, dict):
            raise ValueError(f"missing adjudicated failure chain: {candidate_id}")
        ordered_source = _tuple_strings(chain.get("ordered_source_event_ids", ()))
        if any(item not in id_map for item in ordered_source):
            raise ValueError(f"real gold references an unknown source event: {candidate_id}")
        ordered = tuple(id_map[item] for item in ordered_source)
        recoverability = _nonempty(chain.get("recoverability"), "recoverability")
        current_source = _tuple_strings(raw_prefix.get("current_source_event_ids", ()))
        if any(item not in id_map for item in current_source):
            raise ValueError(f"real current fact references an unknown event: {candidate_id}")
        current_ids = tuple(id_map[item] for item in current_source)
        snapshot = dict(raw_prefix.get("environment_snapshot") or {})
        snapshot.update(
            {
                "source": source,
                "docker_replayable": bool(row.get("replay", {}).get("docker_replayable")),
                "snapshot_ref": str(row.get("replay", {}).get("snapshot_ref") or ""),
                "side_effects_sandboxed": True,
                "history_reconstructable": recoverability != "R0",
                "unsafe_to_repeat_failed_action": recoverability == "R3",
            }
        )
        prefix = PrefixRecord(
            prefix_id=prefix_id,
            source_kind="real_trajectory",
            source_ref={
                "source": source,
                "candidate_id": candidate_id,
                "repository": repository,
                "task_id": task_id,
                "trajectory_revision": str(row.get("trajectory_revision") or ""),
            },
            split=split,
            failure_family=_nonempty(chain.get("failure_family"), "failure_family"),
            task_domain=str(raw_prefix.get("task_domain") or "software"),
            recoverability=recoverability,
            context_length=str(raw_prefix.get("context_length") or "natural"),
            budget_tokens=int(raw_prefix.get("budget_tokens") or 2048),
            events=tuple(events),
            messages=tuple(dict(item) for item in raw_prefix.get("messages", ())),
            tool_schemas=tuple(dict(item) for item in raw_prefix.get("tool_schemas", ())),
            environment_snapshot=snapshot,
        )

        role_ids: dict[str, tuple[str, ...]] = {}
        for role in FAILURE_ROLES:
            role_ids[role] = tuple(
                str(item["event_id"])
                for item in events
                if item.get("causal_role") == role
            )
        evidence: dict[str, tuple[str, ...]] = {
            "failed_action": role_ids["failed_action"],
            "failed_arguments": role_ids["failed_action"],
            "failure_cause": role_ids["failure_result"] + role_ids["diagnostic_evidence"],
            "diagnostic_evidence": (
                role_ids["failure_result"] + role_ids["diagnostic_evidence"]
            ),
            "switch_decision": role_ids["switch_decision"],
            "replacement_action": role_ids["replacement_action"],
            "replacement_arguments": role_ids["replacement_action"],
            "resolution_evidence": role_ids["resolution_evidence"],
            "ordered_event_ids": ordered,
            "current_fact": current_ids,
        }
        explicit_evidence = chain.get("evidence_source_event_ids_by_field") or {}
        if not isinstance(explicit_evidence, dict):
            raise ValueError(
                f"real evidence_source_event_ids_by_field must be an object: {candidate_id}"
            )
        for field_name, raw_ids in explicit_evidence.items():
            source_evidence_ids = _tuple_strings(raw_ids or ())
            if any(item not in id_map for item in source_evidence_ids):
                raise ValueError(
                    f"real field evidence references an unknown source event: "
                    f"{candidate_id}/{field_name}"
                )
            evidence[str(field_name)] = tuple(id_map[item] for item in source_evidence_ids)
        for arguments_field, action_field in (
            ("failed_arguments", "failed_action"),
            ("replacement_arguments", "replacement_action"),
        ):
            if arguments_field not in explicit_evidence:
                evidence[arguments_field] = evidence[action_field]
        gold = FailureChainGold(
            prefix_id=prefix_id,
            failed_action=_nonempty(chain.get("failed_action"), "failed_action"),
            failed_arguments=dict(chain.get("failed_arguments") or {}),
            error_signature=_nonempty(chain.get("error_signature"), "error_signature"),
            diagnostic_evidence=_nonempty(
                chain.get("diagnostic_evidence"), "diagnostic_evidence"
            ),
            switch_decision=_nonempty(chain.get("switch_decision"), "switch_decision"),
            replacement_action=_nonempty(
                chain.get("replacement_action"), "replacement_action"
            ),
            replacement_arguments=dict(chain.get("replacement_arguments") or {}),
            resolution_evidence=_nonempty(
                chain.get("resolution_evidence"), "resolution_evidence"
            ),
            ordered_event_ids=ordered,
            evidence_by_field=evidence,
            recoverability=recoverability,
            current_fact=str(raw_prefix.get("current_fact") or ""),
            current_event_ids=current_ids,
            source_event_ids={id_map[key]: key for key in id_map},
            annotation={
                "gold_source": "double_human_adjudication",
                "human_annotated": True,
                "annotator_a_sha256": str(
                    row.get("annotator_a_sha256") or row.get("annotator_a_hash") or ""
                ),
                "annotator_b_sha256": str(
                    row.get("annotator_b_sha256") or row.get("annotator_b_hash") or ""
                ),
                "adjudicator": str(row.get("adjudicator") or ""),
            },
        )
        item_queries = list(build_queries(prefix))
        if source == "ama_bench":
            item_queries = [item for item in item_queries if item.track == "audit_qa"]
        prefixes.append(prefix)
        gold_rows.append(gold)
        queries.extend(item_queries)
    return prefixes, gold_rows, queries


def artifact_manifest(root: Path) -> list[dict[str, Any]]:
    """Hash every run artifact except the two self-referential manifest files."""

    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in {"manifest.json", "file_manifest.jsonl"}:
            continue
        rows.append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": file_sha256(path)}
        )
    return rows


def write_file_manifest(root: Path) -> list[dict[str, Any]]:
    rows = artifact_manifest(root)
    _write_jsonl(root / "file_manifest.jsonl", rows)
    return rows


def verify_file_manifest(root: Path) -> None:
    claimed = load_jsonl(root / "file_manifest.jsonl")
    actual = artifact_manifest(root)
    if stable_digest(sorted(claimed, key=lambda row: str(row.get("path")))) != stable_digest(actual):
        raise ValueError(f"artifact SHA-256 manifest does not match files: {root}")


def build_benchmark(
    config_path: Path,
    output_root: Path,
    *,
    workspace: Path | None = None,
    source_workspace: Path | None = None,
) -> dict[str, Any]:
    """Build v0.1 diagnostic and the controlled portion of v1 without provider calls."""

    config = load_config(config_path)
    root = (workspace or Path.cwd()).resolve()
    source_root = (source_workspace or root).resolve()
    destination = output_root.resolve()
    legacy_root = (source_root / str(config["legacy"]["input_root"])).resolve()
    expected_legacy_hashes = dict(config["legacy"].get("expected_hashes") or {})
    for name in (
        "prefixes.jsonl",
        "forks.jsonl",
        "lifecycle_gold.jsonl",
        "manager_outputs.jsonl",
        "manifest.json",
    ):
        source = legacy_root / name
        if not source.is_file():
            raise FileNotFoundError(source)
        expected = expected_legacy_hashes.get(name)
        if expected is not None and file_sha256(source) != str(expected):
            raise ValueError(f"legacy source hash mismatch: {name}")
    _assert_output_available(destination)
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "config.snapshot.json", config)

    legacy_wrapper = _copy_legacy(legacy_root, destination / "legacy_diagnostic" / "original")
    legacy_prefixes, legacy_gold, legacy_queries = convert_legacy_diagnostic(legacy_root)
    _write_jsonl(
        destination / "legacy_diagnostic" / "audit_prefixes.jsonl",
        (item.to_dict() for item in legacy_prefixes),
    )
    _write_jsonl(
        destination / "legacy_diagnostic" / "audit_queries.jsonl",
        (item.to_dict() for item in legacy_queries),
    )
    _write_jsonl(
        destination / "legacy_diagnostic" / "audit_gold.jsonl",
        (item.to_dict() for item in legacy_gold),
    )

    controlled_prefixes, controlled_gold, controlled_queries = generate_controlled_dataset(
        base_seed=int(config["controlled"]["base_seed"])
    )
    public = destination / "public"
    private = destination / "private"
    _write_jsonl(public / "prefixes.jsonl", (item.to_dict() for item in controlled_prefixes))
    _write_jsonl(public / "queries.jsonl", (item.to_dict() for item in controlled_queries))
    _write_jsonl(
        public / "dev_validation_gold.jsonl",
        (item.to_dict() for item in controlled_gold if item.prefix_id.split(":")[1] in {
            template["id"] for template in FAILURE_TEMPLATES[:4]
        }),
    )
    _write_jsonl(
        private / "test_gold.jsonl",
        (item.to_dict() for item in controlled_gold if item.prefix_id.split(":")[1] in {
            template["id"] for template in FAILURE_TEMPLATES[4:]
        }),
    )
    _write_jsonl(private / "all_gold.jsonl", (item.to_dict() for item in controlled_gold))
    annotation_destination = destination / "annotations" / "real"
    annotation_source = (
        source_root / str(config["real"].get("normalized_annotation_root", ""))
    ).resolve()
    annotations_copied = _copy_real_annotation_inputs(
        annotation_source, annotation_destination
    ) if config["real"].get("normalized_annotation_root") else False
    if not annotations_copied:
        _write_real_annotation_scaffold(annotation_destination)
    real_validation = validate_real_annotations(
        annotation_destination,
        expected_revisions=_expected_real_revisions(config),
    )
    real_prefixes: list[PrefixRecord] = []
    real_gold: list[FailureChainGold] = []
    real_queries: list[QueryRecord] = []
    if real_validation["ready"]:
        real_prefixes, real_gold, real_queries = import_adjudicated_real(
            annotation_destination
        )
        _write_jsonl(public / "real_prefixes.jsonl", (item.to_dict() for item in real_prefixes))
        _write_jsonl(public / "real_queries.jsonl", (item.to_dict() for item in real_queries))
        _write_jsonl(
            public / "real_dev_validation_gold.jsonl",
            (item.to_dict() for item in real_gold if next(
                prefix.split for prefix in real_prefixes if prefix.prefix_id == item.prefix_id
            ) != "test"),
        )
        _write_jsonl(
            private / "real_test_gold.jsonl",
            (item.to_dict() for item in real_gold if next(
                prefix.split for prefix in real_prefixes if prefix.prefix_id == item.prefix_id
            ) == "test"),
        )
        _write_jsonl(private / "real_all_gold.jsonl", (item.to_dict() for item in real_gold))

    split_counts = {
        split: sum(item.split == split for item in controlled_prefixes) for split in SPLITS
    }
    file_rows = write_file_manifest(destination)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "release": "v0.1-diagnostic",
        "development_protocol": "v0.2-development",
        "development_only": True,
        "independent_validation": False,
        "config_path": str(config_path),
        "config_sha256": file_sha256(config_path),
        "repository": git_provenance(root),
        "implementation": implementation_provenance(root),
        "data_revision": stable_digest(
            {
                "legacy_source_hashes": legacy_wrapper["source_hashes"],
                "controlled_base_seed": config["controlled"]["base_seed"],
                "controlled_prefix_hashes": [item.prefix_hash for item in controlled_prefixes],
                "real_annotation_gate": real_validation,
            }
        ),
        "external_provider_calls": 0,
        "legacy": {
            "prefix_count": len(legacy_prefixes),
            "original_fork_count": 72,
            "ranked": False,
            "source_hashes": legacy_wrapper["source_hashes"],
        },
        "controlled": {
            "prefix_count": len(controlled_prefixes),
            "query_count": len(controlled_queries),
            "episode_count": len(controlled_queries),
            "split_counts": split_counts,
            "synthetic": True,
        },
        "real": {
            "adjudicated_count": real_validation["adjudicated_count"],
            "swe_gym_count": real_validation.get("swe_gym_count", 0),
            "ama_bench_count": real_validation.get("ama_bench_count", 0),
            "imported_prefix_count": len(real_prefixes),
            "imported_query_count": len(real_queries),
            "status": (
                "ready" if real_validation["ready"] else "awaiting_double_human_annotation"
            ),
            "annotation_gate": real_validation,
        },
        "v1_ready": bool(real_validation["ready"] and len(real_prefixes) == 100),
        "interpretation": (
            "The v0.1 data have been seen during development. New results are development_only, "
            "not independent validation evidence, and cannot support a formal benchmark claim. "
            "The 72 legacy forks remain unranked compatibility diagnostics."
        ),
        "artifacts": file_rows,
    }
    _write_json(destination / "manifest.json", manifest)
    return manifest
# Imported late so mutually-referential helpers initialize safely.
from .controlled import (
    build_queries as build_queries,
    generate_controlled_dataset as generate_controlled_dataset,
)
from .io import (
    _assert_output_available as _assert_output_available,
    _nonempty as _nonempty,
    _tuple_strings as _tuple_strings,
    _write_json as _write_json,
    _write_jsonl as _write_jsonl,
    file_sha256 as file_sha256,
    git_provenance as git_provenance,
    implementation_provenance as implementation_provenance,
    load_jsonl as load_jsonl,
    stable_digest as stable_digest,
)

from .legacy_data import (
    convert_legacy_diagnostic as convert_legacy_diagnostic,
)

from .models import (
    FailureChainGold as FailureChainGold,
    PrefixRecord as PrefixRecord,
    QueryRecord as QueryRecord,
)

from .real_validation import (
    validate_real_annotations as validate_real_annotations,
)
