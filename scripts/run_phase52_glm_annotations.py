#!/usr/bin/env python3
"""Run or resume a capped two-pass Phase 5.2 pseudo-label collection."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import date, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tracegraph.lifecycle_annotation import (  # noqa: E402
    AnnotationBudget,
    cohen_kappa_binary,
    consensus_labels,
    extract_function_arguments,
    file_sha256,
    load_phase52_config,
    prepare_validation_feedback_request,
    remap_labels_to_original,
    validate_machine_labels,
    validate_relation_boolean_labels,
    validate_relation_first_labels,
)
from tracegraph.trajectory_artifacts import sha256_json  # noqa: E402


PROTECTED_ROOT_HASHES = {
    "outputs/gdsc_r0_audit": "15ac8851550f3b3a7f9e4ce6caaf826252bb5a10b679814daadfcb02bb381613",
    "outputs/gdsc_r2_1": "12e443366e814eb3403601952dc88763a90bb24c482862402d9110da40d7f491",
    "outputs/phase4": "85a75eb998b08591f426ff64ce328ccc867407042f93485e254b4bf685b93867",
    "outputs/phase5": "be1871f159124856b78a364d6389fd3fc71355a3245a4076601933efca5cab83",
    "outputs/phase5_1": "052981da5bcc836c0fcf417482bc5f90dbdaae5026f7599b734e8a2e9ccea27d",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSON objects in {path}")
    return rows


def _write_new_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_or_verify_json(path: Path, value: Any) -> None:
    if path.exists():
        if _load_json(path) != value:
            raise RuntimeError(f"immutable JSON artifact drift: {path}")
        return
    _write_new_json(path, value)


def _write_new_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _tree_hash(root: Path) -> str:
    files = [
        {
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return sha256_json(files)


def _verify_protected_roots() -> None:
    for relative, expected in PROTECTED_ROOT_HASHES.items():
        observed = _tree_hash(REPO_ROOT / relative)
        if observed != expected:
            raise RuntimeError(f"protected artifact drift before GLM run: {relative}")


def _verify_preparation(output_root: Path) -> None:
    manifest = _load_json(output_root / "preparation_manifest.json")
    declared = manifest.pop("manifest_sha256")
    if sha256_json(manifest) != declared:
        raise ValueError("preparation manifest embedded hash mismatch")
    for record in manifest["files"]:
        path = output_root / record["path"]
        if path.stat().st_size != record["size_bytes"] or file_sha256(path) != record["sha256"]:
            raise ValueError(f"prepared request artifact drift: {path}")


def _price_preflight(
    snapshot_path: Path, confirmation: str, config: dict[str, Any]
) -> dict[str, Any]:
    snapshot = _load_json(snapshot_path)
    declared = snapshot.pop("snapshot_sha256")
    if sha256_json(snapshot) != declared or confirmation != declared:
        raise RuntimeError("pricing snapshot confirmation hash is missing or incorrect")
    checked = date.fromisoformat(str(snapshot["checked_at"]))
    age = (date.today() - checked).days
    if age < 0 or age > int(snapshot["valid_for_days"]):
        raise RuntimeError("pricing snapshot is stale; recheck official pricing before requests")
    model = config["model"]
    if snapshot.get("api_model") not in {None, model["api_model"]}:
        raise RuntimeError("pricing snapshot model does not match the configured model")
    if snapshot.get("must_stop_if_not_free") is True:
        if any(snapshot[key] != "free" for key in ("input_price", "cached_input_price", "output_price")):
            raise RuntimeError(
                "glm-4.7-flash is not recorded as fully free; paid use is forbidden"
            )
    elif model.get("paid_use_authorized_by_user") is True:
        if snapshot.get("paid_use_authorized_by_user") is not True:
            raise RuntimeError("paid model run lacks an explicit paid pricing snapshot")
        for key in ("input_usd_per_mtok", "output_usd_per_mtok"):
            if float(snapshot[key]) < 0:
                raise RuntimeError(f"invalid paid pricing field: {key}")
    else:
        raise RuntimeError("paid model use is not authorized by this configuration")
    return {**snapshot, "snapshot_sha256": declared, "age_days_at_run": age}


def _load_ignored_credentials(config: dict[str, Any]) -> tuple[str, str]:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        raise RuntimeError("local ignored .env is missing")
    check = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={REPO_ROOT.as_posix()}",
            "check-ignore",
            "-q",
            ".env",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    if check.returncode != 0:
        raise RuntimeError(".env is not ignored by Git; refusing to load credentials")
    local: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        local[key.strip()] = value.strip().strip('"').strip("'")
    provider = str(config["model"].get("provider", "zai"))
    if provider == "dashscope":
        api_key = os.environ.get("DASHSCOPE_API_KEY") or local.get(
            "DASHSCOPE_API_KEY", ""
        )
        api_base = (
            os.environ.get("DASHSCOPE_WORKSPACE_BASE_URL")
            or local.get("DASHSCOPE_WORKSPACE_BASE_URL", "")
            or os.environ.get("DASHSCOPE_BASE_URL")
            or local.get(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        )
        key_name = "DASHSCOPE_API_KEY"
        base_name = "DASHSCOPE_BASE_URL"
    elif provider == "zai":
        api_key = os.environ.get("ZAI_API_KEY") or local.get("ZAI_API_KEY", "")
        api_base = os.environ.get("ZAI_API_BASE") or local.get(
            "ZAI_API_BASE", "https://open.bigmodel.cn/api/paas/v4"
        )
        key_name = "ZAI_API_KEY"
        base_name = "ZAI_API_BASE"
    else:
        raise RuntimeError(f"unsupported Phase 5.2 provider: {provider}")
    if not api_key:
        raise RuntimeError(f"{key_name} is missing")
    if not api_base.lower().startswith("https://"):
        raise RuntimeError(f"{base_name} must use HTTPS")
    return api_key, f"{api_base.rstrip('/')}/chat/completions"


def _endpoint_origin(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"


def _redact(value: Any, secret: str) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact(child, secret) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact(child, secret) for child in value]
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    return value


def _post_json(endpoint: str, api_key: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
            return int(response.status), json.loads(raw)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            parsed: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"error": {"message": raw[:4000]}}
        rate_headers = {
            str(key).lower(): str(value)
            for key, value in error.headers.items()
            if str(key).lower() == "retry-after"
            or str(key).lower().startswith("x-ratelimit")
        }
        if rate_headers:
            parsed["response_rate_limit_headers"] = rate_headers
        return int(error.code), parsed
    except urllib.error.URLError as error:
        return 0, {"error": {"message": f"network_error:{error.reason!s}"[:4000]}}


def _usage(response: dict[str, Any]) -> dict[str, int]:
    raw = response.get("usage") or {}
    return {
        "prompt_tokens": int(raw.get("prompt_tokens") or 0),
        "completion_tokens": int(raw.get("completion_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def _existing_ledger(path: Path) -> list[dict[str, Any]]:
    return _load_jsonl(path) if path.exists() else []


def _parse_one(
    *,
    response: dict[str, Any],
    mapping: dict[str, Any],
    config: dict[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    opaque_spans = {str(item["opaque_span_id"]) for item in mapping["spans"]}
    opaque_events = {str(item["opaque_event_id"]) for item in mapping["events"]}
    protocol = config.get("annotation", {}).get(
        "label_protocol", "direct_disposition_v1"
    )
    if protocol == "relation_first_v1":
        parsed = validate_relation_first_labels(
            extract_function_arguments(
                response, function_name="submit_lifecycle_relations"
            ),
            expected_span_ids=opaque_spans,
            allowed_event_ids=opaque_events,
        )
    elif protocol == "relation_first_boolean_v2":
        parsed = validate_relation_boolean_labels(
            extract_function_arguments(
                response, function_name="submit_lifecycle_relations"
            ),
            expected_span_ids=opaque_spans,
            allowed_event_ids=opaque_events,
        )
    else:
        parsed = validate_machine_labels(
            extract_function_arguments(response),
            expected_span_ids=opaque_spans,
            allowed_event_ids=opaque_events,
        )
    return parsed, remap_labels_to_original(parsed, mapping)


def _finalize(
    output_root: Path,
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    price: dict[str, Any],
) -> dict[str, Any]:
    by_prefix: dict[str, dict[str, list[dict[str, Any]]]] = {}
    label_rows: list[dict[str, Any]] = []
    for row in rows:
        labels_path = output_root / "labels" / f"{row['request_id']}.json"
        if not labels_path.exists():
            raise RuntimeError("cannot finalize an incomplete double-pass collection")
        labels = _load_json(labels_path)["labels_original_ids"]
        by_prefix.setdefault(row["prefix_id"], {})[row["pass_id"]] = labels
        label_rows.extend(
            {
                "prefix_id": row["prefix_id"],
                "pass_id": row["pass_id"],
                "split": row["split"],
                **label,
            }
            for label in labels
        )
    consensus_rows: list[dict[str, Any]] = []
    a_safe: list[bool] = []
    b_safe: list[bool] = []
    complete = 0
    for prefix_id in sorted(by_prefix):
        passes = by_prefix[prefix_id]
        if set(passes) != {"pass_a", "pass_b"}:
            continue
        complete += 1
        a_map = {item["span_id"]: item for item in passes["pass_a"]}
        b_map = {item["span_id"]: item for item in passes["pass_b"]}
        prefix_consensus = consensus_labels(passes["pass_a"], passes["pass_b"])
        split = next(row["split"] for row in rows if row["prefix_id"] == prefix_id)
        for item in prefix_consensus:
            consensus_rows.append({"prefix_id": prefix_id, "split": split, **item})
            a_safe.append(a_map[item["span_id"]]["disposition"] == "safe_to_evict")
            b_safe.append(b_map[item["span_id"]]["disposition"] == "safe_to_evict")
    safe_agreement = sum(a == b for a, b in zip(a_safe, b_safe, strict=True)) / len(a_safe)
    kappa = cohen_kappa_binary(a_safe, b_safe)
    consensus_safe = [item for item in consensus_rows if item["disposition"] == "safe_to_evict"]
    protected_safe = [
        item
        for item in consensus_safe
        if set(item["obligations"]) & {"policy", "confirmation", "receipt", "audit"}
    ]
    ledger = _existing_ledger(output_root / "usage_ledger.jsonl")
    usage = {
        "requests_attempted": len(ledger),
        "requests_valid": sum(int(item["valid"]) for item in ledger),
        "prompt_tokens": sum(int(item["usage"]["prompt_tokens"]) for item in ledger),
        "completion_tokens": sum(int(item["usage"]["completion_tokens"]) for item in ledger),
        "total_tokens": sum(int(item["usage"]["total_tokens"]) for item in ledger),
    }
    metrics = {
        "complete_prefixes": complete,
        "safe_binary_agreement": safe_agreement,
        "cohen_kappa": kappa,
        "consensus_safe_units": len(consensus_safe),
        "protected_consensus_safe_units": len(protected_safe),
        "span_units": len(consensus_rows),
    }
    thresholds = config["gates"]["pseudolabel"]
    checks = {
        "complete_prefixes": complete >= thresholds["complete_prefixes_min"],
        "safe_binary_agreement": safe_agreement >= thresholds["safe_binary_agreement_min"],
        "cohen_kappa": kappa >= thresholds["cohen_kappa_min"],
        "consensus_safe_units": len(consensus_safe) >= thresholds["consensus_safe_units_min"],
        "protected_consensus_safe_units": (
            len(protected_safe) <= thresholds["protected_safe_units_max"]
        ),
    }
    _write_new_jsonl(output_root / "parsed_machine_labels.jsonl", label_rows)
    _write_new_jsonl(output_root / "machine_consensus.jsonl", consensus_rows)
    summary: dict[str, Any] = {
        "schema_version": "phase52_pseudolabel_summary_v1",
        "model": config["model"]["report_identity"],
        "same_model_stability_not_independent_annotators": True,
        "metrics": metrics,
        "usage": usage,
        "pricing_snapshot_sha256": price["snapshot_sha256"],
        "machine_labels_are_human_gold": False,
        "machine_labels_may_generate_hard_dead": False,
        "external_behavior_model_sessions": 0,
    }
    summary["summary_sha256"] = sha256_json(summary)
    _write_new_json(output_root / "pseudolabel_summary.json", summary)
    gate: dict[str, Any] = {
        "schema_version": "phase52_pseudolabel_gate_v1",
        "decision": "pass" if all(checks.values()) else "stop_phase52",
        "checks": checks,
        "metrics": metrics,
        "thresholds": thresholds,
        "summary_sha256": summary["summary_sha256"],
        "failure_action": "no_scheme_b_and_no_external_behavior_experiment",
    }
    gate["gate_report_sha256"] = sha256_json(gate)
    _write_new_json(output_root / "pseudolabel_gate.json", gate)
    _verify_protected_roots()
    files = [
        {
            "path": path.relative_to(output_root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.name not in {"annotation_manifest.json", "manifest.json"}
    ]
    final_manifest: dict[str, Any] = {
        "schema_version": "phase52_pseudolabel_artifact_manifest_v1",
        "status": "complete",
        "files": files,
        "protected_artifact_hashes": dict(PROTECTED_ROOT_HASHES),
        "provider_request_attempts": len(ledger),
        "external_behavior_model_sessions": 0,
    }
    final_manifest["manifest_sha256"] = sha256_json(final_manifest)
    _write_new_json(output_root / "annotation_manifest.json", final_manifest)
    if gate["decision"] != "pass":
        stopped_manifest = {
            **final_manifest,
            "status": "stopped_at_pseudolabel_gate",
        }
        stopped_manifest.pop("manifest_sha256", None)
        stopped_manifest["manifest_sha256"] = sha256_json(stopped_manifest)
        _write_new_json(output_root / "manifest.json", stopped_manifest)
    return gate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs/phase52_lifecycle_modeling.json",
    )
    parser.add_argument("--confirm-pricing-snapshot-sha256", required=True)
    parser.add_argument("--pricing-snapshot", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-new-requests", type=int)
    parser.add_argument(
        "--prioritize-request-id",
        action="append",
        default=[],
        help="Run these frozen request IDs first; may be repeated.",
    )
    args = parser.parse_args()
    config = load_phase52_config(args.config)
    output_root = REPO_ROOT / str(config["output_root"])
    if not output_root.is_dir():
        raise RuntimeError("run request preparation before the GLM runner")
    if (output_root / "annotation_manifest.json").exists():
        raise FileExistsError("completed Phase 5.2 artifact is immutable")
    _verify_preparation(output_root)
    _verify_protected_roots()
    snapshot_path = (
        args.pricing_snapshot.resolve()
        if args.pricing_snapshot is not None
        else output_root / "pricing_snapshot.json"
    )
    allowed_snapshot_root = (output_root / "pricing_snapshots").resolve()
    if snapshot_path != (output_root / "pricing_snapshot.json").resolve() and not str(
        snapshot_path
    ).startswith(str(allowed_snapshot_root) + os.sep):
        raise RuntimeError("pricing snapshot must be the initial or append-only Phase 5.2 artifact")
    price = _price_preflight(snapshot_path, args.confirm_pricing_snapshot_sha256, config)
    rows = _load_jsonl(output_root / "request_index.jsonl")
    limits = config["annotation"]
    estimated = sum(int(row["estimated_input_tokens"]) for row in rows)
    if len(rows) != 370 or estimated > limits["estimated_input_tokens_hard_max"]:
        raise RuntimeError("request population or estimated input budget drift")
    if args.prioritize_request_id:
        requested = list(dict.fromkeys(map(str, args.prioritize_request_id)))
        known = {str(row["request_id"]) for row in rows}
        unknown = sorted(set(requested).difference(known))
        if unknown:
            raise RuntimeError(f"unknown prioritized request IDs: {unknown}")
        priority = {request_id: index for index, request_id in enumerate(requested)}
        rows.sort(
            key=lambda row: (
                0 if row["request_id"] in priority else 1,
                priority.get(row["request_id"], 0),
            )
        )
    if args.dry_run:
        api_key, endpoint = _load_ignored_credentials(config)
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "model": config["model"]["api_model"],
                    "provider": config["model"].get("provider", "zai"),
                    "endpoint_origin": _endpoint_origin(endpoint),
                    "credential_loaded": bool(api_key),
                    "requests_prepared": len(rows),
                    "estimated_input_tokens": estimated,
                    "pricing_snapshot_sha256": price["snapshot_sha256"],
                    "provider_requests": 0,
                },
                sort_keys=True,
            )
        )
        return 0

    api_key, endpoint = _load_ignored_credentials(config)
    ledger_path = output_root / "usage_ledger.jsonl"
    ledger = _existing_ledger(ledger_path)
    new_requests = 0
    for index, row in enumerate(rows, 1):
        labels_path = output_root / "labels" / f"{row['request_id']}.json"
        if labels_path.exists():
            continue
        request_path = output_root / row["request_file"]
        mapping_path = output_root / row["mapping_file"]
        if file_sha256(request_path) != row["request_file_sha256"]:
            raise RuntimeError(f"frozen request file drift: {request_path}")
        request_body = _load_json(request_path)
        if sha256_json(request_body) != row["request_sha256"]:
            raise RuntimeError(f"frozen request content drift: {request_path}")
        mapping = _load_json(mapping_path)
        valid = False
        last_error = ""
        while not valid:
            ledger = _existing_ledger(ledger_path)
            existing_attempts = [
                item for item in ledger if item["request_id"] == row["request_id"]
            ]
            invalid_model_responses = sum(
                int(200 <= int(item["http_status"]) < 300 and not item["valid"])
                for item in existing_attempts
            )
            if invalid_model_responses >= int(limits["retry_per_request_max"]) + 1:
                raise RuntimeError(
                    f"request remained invalid after one validation retry: "
                    f"{row['request_id']}"
                )
            attempt = len(existing_attempts) + 1
            AnnotationBudget.from_ledger(ledger, limits=limits).assert_can_submit()
            if args.max_new_requests is not None and new_requests >= args.max_new_requests:
                print(json.dumps({"status": "paused", "new_requests": new_requests}))
                return 3
            provider_request = request_body
            repair_request_file: str | None = None
            if (
                config.get("annotation", {}).get("repair_retry_mode")
                == "validation_feedback"
            ):
                prior_invalid = [
                    item
                    for item in existing_attempts
                    if 200 <= int(item["http_status"]) < 300 and not item["valid"]
                ]
                if prior_invalid:
                    provider_request = prepare_validation_feedback_request(
                        request_body,
                        validation_error=str(prior_invalid[-1]["validation_error"]),
                    )
                    repair_path = output_root / "repair_requests" / (
                        f"{row['request_id']}.attempt{attempt}.json"
                    )
                    _write_or_verify_json(repair_path, provider_request)
                    repair_request_file = repair_path.relative_to(output_root).as_posix()
            provider_request_sha256 = sha256_json(provider_request)
            status, response = _post_json(endpoint, api_key, provider_request)
            new_requests += 1
            safe_response = _redact(response, api_key)
            raw_path = output_root / "raw_responses" / (
                f"{row['request_id']}.attempt{attempt}.json"
            )
            _write_new_json(raw_path, safe_response)
            usage = _usage(response)
            try:
                if status < 200 or status >= 300:
                    raise ValueError(f"provider HTTP status {status}")
                opaque, original = _parse_one(
                    response=response, mapping=mapping, config=config
                )
                label_protocol = config.get("annotation", {}).get(
                    "label_protocol", "direct_disposition_v1"
                )
                label_artifact: dict[str, Any] = {
                    "schema_version": (
                        "phase52_machine_boolean_relation_labels_v2"
                        if label_protocol == "relation_first_boolean_v2"
                        else (
                            "phase52_machine_relation_labels_v1"
                            if label_protocol == "relation_first_v1"
                            else "phase52_machine_labels_v1"
                        )
                    ),
                    "request_id": row["request_id"],
                    "prefix_id": row["prefix_id"],
                    "pass_id": row["pass_id"],
                    "request_sha256": row["request_sha256"],
                    "provider_request_sha256": provider_request_sha256,
                    "repair_request_file": repair_request_file,
                    "raw_response_file": raw_path.relative_to(output_root).as_posix(),
                    "raw_response_sha256": file_sha256(raw_path),
                    "labels_opaque_ids": list(opaque),
                    "labels_original_ids": list(original),
                    "usage": usage,
                }
                label_artifact["labels_sha256"] = sha256_json(label_artifact)
                _write_new_json(labels_path, label_artifact)
                valid = True
                last_error = ""
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                last_error = f"{error.__class__.__name__}:{str(error)[:300]}"
            _append_jsonl(
                ledger_path,
                {
                    "attempted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "provider": config["model"].get("provider", "zai"),
                    "model": config["model"]["api_model"],
                    "request_id": row["request_id"],
                    "prefix_id": row["prefix_id"],
                    "pass_id": row["pass_id"],
                    "attempt": attempt,
                    "http_status": status,
                    "valid": valid,
                    "validation_error": last_error,
                    "usage": usage,
                    "provider_request_sha256": provider_request_sha256,
                    "repair_request_file": repair_request_file,
                    "raw_response_sha256": file_sha256(raw_path),
                },
            )
            ledger = _existing_ledger(ledger_path)
            if valid:
                break
            if status == 0 or status == 429 or status >= 500:
                print(
                    json.dumps(
                        {
                            "status": "paused_transient_http",
                            "http_status": status,
                            "request_id": row["request_id"],
                            "attempt": attempt,
                            "global_requests_used": len(ledger),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return 4
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"non-retryable provider HTTP status {status}: {row['request_id']}"
                )
        if index % 10 == 0 or index == len(rows):
            print(f"validated {index}/{len(rows)} frozen requests", flush=True)

    gate = _finalize(output_root, rows, config, price)
    print(json.dumps(gate, ensure_ascii=False, sort_keys=True))
    return 0 if gate["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
