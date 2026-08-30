"""在费用硬上限内运行第六阶段真实模型小规模试跑。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from tracegraph.decision_state import stable_digest
from tracegraph.phase6_live import (
    file_sha256,
    load_jsonl,
    parse_submit_answer,
    prepare_live_trials,
    provider_request,
    score_live_answer,
    smoke_tool_contract_valid,
    summarize_live_results,
    theoretical_maximum_cost_cny,
    validate_input_hashes,
    validate_live_config,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _jsonl_text(rows: list[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for row in rows
    )


def _write_or_verify(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise RuntimeError(f"existing append-only artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    with path.open("ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _load_ignored_dashscope_credentials() -> tuple[str, str]:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        raise RuntimeError("local ignored .env is missing")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    if ignored.returncode != 0:
        raise RuntimeError(".env is not ignored by Git; refusing to load credentials")
    local: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        local[key.strip()] = value.strip().strip('"').strip("'")
    api_key = os.environ.get("DASHSCOPE_API_KEY") or local.get("DASHSCOPE_API_KEY", "")
    api_base = os.environ.get("DASHSCOPE_WORKSPACE_BASE_URL") or local.get(
        "DASHSCOPE_WORKSPACE_BASE_URL", ""
    )
    api_base = api_base or os.environ.get("DASHSCOPE_BASE_URL") or local.get(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is missing")
    if not api_base.lower().startswith("https://"):
        raise RuntimeError("DASHSCOPE_BASE_URL must use HTTPS")
    return api_key, f"{api_base.rstrip('/')}/chat/completions"


def _endpoint_origin(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"


def _post_json(
    endpoint: str,
    api_key: str,
    body: Mapping[str, Any],
    *,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return int(response.status), json.loads(raw)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"error": {"message": raw[:4000]}}
        return int(error.code), parsed
    except (TimeoutError, urllib.error.URLError) as error:
        return 0, {"error": {"message": f"{error.__class__.__name__}: {error.reason}"}}


def _usage(response: Mapping[str, Any]) -> tuple[int, int]:
    usage = response.get("usage", {})
    if not isinstance(usage, Mapping):
        return 0, 0
    input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    output_tokens = int(
        usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    )
    return input_tokens, output_tokens


def _cost_cny(
    model: str,
    input_tokens: int,
    output_tokens: int,
    config: Mapping[str, Any],
) -> float:
    prices = config["pricing_snapshot"]["prices_per_million_tokens"][model]
    return (
        input_tokens * float(prices["input"])
        + output_tokens * float(prices["output"])
    ) / 1_000_000


def _ledger_totals(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "requests": len(rows),
        "input_tokens": sum(int(row.get("input_tokens", 0)) for row in rows),
        "output_tokens": sum(int(row.get("output_tokens", 0)) for row in rows),
        "cost_cny": sum(float(row.get("cost_cny", 0.0)) for row in rows),
    }


def _assert_budget_before_request(
    ledger: list[Mapping[str, Any]],
    model: str,
    config: Mapping[str, Any],
) -> None:
    limits = config["limits"]
    totals = _ledger_totals(ledger)
    if totals["requests"] >= int(limits["request_count_hard_max"]):
        raise RuntimeError("provider request count hard limit reached")
    if totals["input_tokens"] >= int(limits["actual_input_tokens_hard_max"]):
        raise RuntimeError("provider input token hard limit reached")
    if totals["output_tokens"] >= int(limits["actual_output_tokens_hard_max"]):
        raise RuntimeError("provider output token hard limit reached")
    worst_next = _cost_cny(
        model,
        int(limits["request_input_tokens_hard_max"]),
        int(config["model"]["max_output_tokens"]),
        config,
    )
    if totals["cost_cny"] + worst_next > float(limits["maximum_cost_cny"]):
        raise RuntimeError("100 CNY provider cost hard limit would be exceeded")


def _assert_budget_after_request(
    ledger: list[Mapping[str, Any]], config: Mapping[str, Any]
) -> None:
    totals = _ledger_totals(ledger)
    limits = config["limits"]
    if totals["cost_cny"] > float(limits["maximum_cost_cny"]):
        raise RuntimeError("100 CNY provider cost hard limit was exceeded")
    if totals["input_tokens"] > int(limits["actual_input_tokens_hard_max"]):
        raise RuntimeError("actual provider input token hard limit was exceeded")
    if totals["output_tokens"] > int(limits["actual_output_tokens_hard_max"]):
        raise RuntimeError("actual provider output token hard limit was exceeded")


def _raw_response_path(output_root: Path, request_id: str) -> Path:
    return output_root / "raw_responses" / f"{request_id}.json"


def _save_provider_attempt(
    *,
    output_root: Path,
    request_id: str,
    request_kind: str,
    model: str,
    body: Mapping[str, Any],
    status: int,
    response: Mapping[str, Any],
    valid: bool,
    validation_error: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    raw_path = _raw_response_path(output_root, request_id)
    _write_or_verify(raw_path, _json_text(response))
    input_tokens, output_tokens = _usage(response)
    row = {
        "attempted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "request_id": request_id,
        "request_kind": request_kind,
        "model": model,
        "http_status": status,
        "valid": valid,
        "validation_error": validation_error,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_cny": _cost_cny(model, input_tokens, output_tokens, config),
        "provider_request_sha256": stable_digest(body),
        "raw_response_file": raw_path.relative_to(output_root).as_posix(),
        "raw_response_sha256": file_sha256(raw_path),
    }
    _append_jsonl(output_root / "ledger.jsonl", row)
    return row


def _smoke_template() -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "system",
                "content": "Call submit_answer exactly once using only the supplied record.",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "records": [
                            {
                                "record_id": "R001",
                                "kind": "observation",
                                "content": "current:connectivity-ok",
                            }
                        ],
                        "request": "Report the current connectivity value.",
                    },
                    separators=(",", ":"),
                ),
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "submit_answer",
                    "description": "Submit the answer and supporting record IDs.",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "answer": {"type": "string"},
                            "evidence_record_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "fact_scope": {
                                "type": "string",
                                "enum": ["current", "historical"],
                            },
                            "would_repeat_side_effect": {"type": "boolean"},
                        },
                        "required": [
                            "answer",
                            "evidence_record_ids",
                            "fact_scope",
                            "would_repeat_side_effect",
                        ],
                    },
                },
            }
        ],
        "tool_choice": {
            "type": "function",
            "function": {"name": "submit_answer"},
        },
        "stream": False,
    }


def _model_unavailable(status: int, response: Mapping[str, Any], valid: bool) -> bool:
    if status == 200 and not valid:
        return True
    if status not in {400, 403, 404}:
        return False
    text = json.dumps(response, ensure_ascii=False).casefold()
    return any(
        marker in text
        for marker in (
            "model",
            "not exist",
            "not found",
            "not support",
            "permission",
            "access denied",
            "模型",
            "无权",
        )
    )


def _select_model(
    *,
    output_root: Path,
    config: Mapping[str, Any],
    endpoint: str,
    api_key: str,
) -> dict[str, Any]:
    selection_path = output_root / "model_selection.json"
    if selection_path.is_file():
        return json.loads(selection_path.read_text(encoding="utf-8"))
    ledger_path = output_root / "ledger.jsonl"
    ledger = load_jsonl(ledger_path) if ledger_path.is_file() else []
    models = [str(config["model"]["primary"]), str(config["model"]["fallback"])]
    fallback_reason = ""
    for index, model in enumerate(models):
        _assert_budget_before_request(ledger, model, config)
        body = provider_request(_smoke_template(), model, config["model"])
        request_id = f"smoke_{index + 1}_{model.replace('.', '_').replace('-', '_')}"
        status, response = _post_json(
            endpoint,
            api_key,
            body,
            timeout=int(config["limits"]["timeout_seconds"]),
        )
        valid = False
        error = ""
        try:
            parsed = parse_submit_answer(response) if 200 <= status < 300 else None
            valid = bool(parsed and smoke_tool_contract_valid(parsed))
            if not valid:
                error = "smoke answer did not satisfy the tool-call contract"
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            error = f"{exc.__class__.__name__}:{str(exc)[:300]}"
        attempt = _save_provider_attempt(
            output_root=output_root,
            request_id=request_id,
            request_kind="smoke",
            model=model,
            body=body,
            status=status,
            response=response,
            valid=valid,
            validation_error=error,
            config=config,
        )
        ledger.append(attempt)
        _assert_budget_after_request(ledger, config)
        if valid:
            selection = {
                "schema_version": "phase6_live_model_selection_v1",
                "selected_model": model,
                "primary_model": config["model"]["primary"],
                "fallback_model": config["model"]["fallback"],
                "fallback_used": index == 1,
                "fallback_reason": fallback_reason or None,
                "smoke_request_id": request_id,
                "endpoint_origin": _endpoint_origin(endpoint),
            }
            _write_or_verify(selection_path, _json_text(selection))
            return selection
        if index == 0 and _model_unavailable(status, response, valid):
            fallback_reason = f"primary smoke unavailable: HTTP {status}; {error}"
            continue
        raise RuntimeError(f"model smoke failed without an allowed fallback: HTTP {status}")
    raise RuntimeError("both the requested model and qwen-plus failed the smoke test")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _finalize(
    output_root: Path,
    config: Mapping[str, Any],
    model_selection: Mapping[str, Any],
) -> dict[str, Any]:
    results = load_jsonl(output_root / "results.jsonl")
    ledger = load_jsonl(output_root / "ledger.jsonl")
    metrics, gates = summarize_live_results(results, config)
    totals = _ledger_totals(ledger)
    metrics["provider_requests"] = totals["requests"]
    metrics["provider_total_input_tokens"] = totals["input_tokens"]
    metrics["provider_total_output_tokens"] = totals["output_tokens"]
    metrics["provider_total_cost_cny"] = totals["cost_cny"]
    metrics["selected_model"] = model_selection["selected_model"]
    gates["criteria"]["cost_within_100_cny"] = totals["cost_cny"] <= 100.0
    gates["decision"] = "pass" if all(gates["criteria"].values()) else "stop"
    _write_or_verify(output_root / "metrics.json", _json_text(metrics))
    _write_or_verify(output_root / "gate_report.json", _json_text(gates))
    artifact_hashes = {
        path.relative_to(output_root).as_posix(): file_sha256(path)
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.name not in {"hashes.json", "manifest.json"}
    }
    _write_or_verify(output_root / "hashes.json", _json_text(artifact_hashes))
    manifest = {
        "schema_version": "phase6_live_manifest_v1",
        "run_id": config["run_id"],
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "selected_model": model_selection["selected_model"],
        "fallback_used": model_selection["fallback_used"],
        "controlled_synthetic": True,
        "real_provider_used": True,
        "trial_count": len(results),
        "provider_requests": totals["requests"],
        "provider_total_cost_cny": totals["cost_cny"],
        "gate_decision": gates["decision"],
        "hashes_sha256": file_sha256(output_root / "hashes.json"),
    }
    _write_or_verify(output_root / "manifest.json", _json_text(manifest))
    return {"manifest": manifest, "metrics": metrics, "gates": gates}


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/phase6_live_qwen38_27b_v1.json"),
        help="已经固定的试跑设置文件",
    )
    parser.add_argument("--confirm-config-sha256", required=True, help="确认设置文件指纹")
    parser.add_argument(
        "--confirm-primary-model",
        required=True,
        help="确认首先尝试的模型编号",
    )
    parser.add_argument(
        "--confirm-maximum-cost-cny",
        type=float,
        required=True,
        help="确认人民币费用硬上限",
    )
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="只做一次工具调用连通检查，不运行 216 次试验",
    )
    parser.add_argument(
        "--max-new-trials",
        type=int,
        help="本次最多新增多少次正式试验，留空表示跑完",
    )
    args = parser.parse_args()
    config_path = (REPO_ROOT / args.config).resolve()
    if file_sha256(config_path) != args.confirm_config_sha256.lower():
        raise ValueError("confirmed config SHA-256 does not match")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_live_config(config)
    if args.confirm_primary_model != config["model"]["primary"]:
        raise ValueError("confirmed primary model does not match")
    if args.confirm_maximum_cost_cny != float(config["limits"]["maximum_cost_cny"]):
        raise ValueError("confirmed maximum cost does not match")
    theoretical = theoretical_maximum_cost_cny(config)
    if theoretical > float(config["limits"]["maximum_cost_cny"]):
        raise RuntimeError("the fixed request limits could exceed the authorized cost")
    input_root = (REPO_ROOT / str(config["input_root"])).resolve()
    validate_input_hashes(input_root, config["input_hashes"])
    trials = prepare_live_trials(config, input_root)
    output_root = (REPO_ROOT / str(config["output_root"])).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    _write_or_verify(output_root / "config.snapshot.json", _json_text(config))
    _write_or_verify(output_root / "request_templates.jsonl", _jsonl_text(trials))
    sample = {
        "schema_version": "phase6_live_sample_v1",
        "prefix_ids": list(config["sample_prefix_ids"]),
        "methods": list(config["methods"]),
        "fork_types": list(config["fork_types"]),
        "trial_count": len(trials),
        "theoretical_maximum_cost_cny": theoretical,
    }
    _write_or_verify(output_root / "sample.json", _json_text(sample))
    api_key, endpoint = _load_ignored_dashscope_credentials()
    selection = _select_model(
        output_root=output_root,
        config=config,
        endpoint=endpoint,
        api_key=api_key,
    )
    if args.smoke_only:
        print(_json_text({"status": "smoke_pass", **selection}).strip())
        return 0
    fork_by_id = {
        str(item["fork_id"]): item for item in load_jsonl(input_root / "forks.jsonl")
    }
    results_path = output_root / "results.jsonl"
    existing_results = load_jsonl(results_path) if results_path.is_file() else []
    completed = {str(item["trial_id"]) for item in existing_results}
    ledger_path = output_root / "ledger.jsonl"
    ledger = load_jsonl(ledger_path) if ledger_path.is_file() else []
    model = str(selection["selected_model"])
    new_trials = 0
    for index, trial in enumerate(trials, 1):
        if trial["trial_id"] in completed:
            continue
        if args.max_new_trials is not None and new_trials >= args.max_new_trials:
            print(_json_text({"status": "paused", "new_trials": new_trials}).strip())
            return 3
        _assert_budget_before_request(ledger, model, config)
        body = provider_request(trial["request_template"], model, config["model"])
        request_id = f"trial_{stable_digest(trial['trial_id'])[:20]}"
        status, response = _post_json(
            endpoint,
            api_key,
            body,
            timeout=int(config["limits"]["timeout_seconds"]),
        )
        valid = False
        validation_error = ""
        answer: dict[str, Any] | None = None
        try:
            if status < 200 or status >= 300:
                raise ValueError(f"provider HTTP status {status}")
            answer = parse_submit_answer(response)
            visible = set(trial["opaque_event_ids"])
            if set(answer["evidence_record_ids"]).difference(visible):
                raise ValueError("model cited record IDs that were not visible")
            valid = True
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            validation_error = f"{error.__class__.__name__}:{str(error)[:300]}"
        attempt = _save_provider_attempt(
            output_root=output_root,
            request_id=request_id,
            request_kind="trial",
            model=model,
            body=body,
            status=status,
            response=response,
            valid=valid,
            validation_error=validation_error,
            config=config,
        )
        ledger.append(attempt)
        _assert_budget_after_request(ledger, config)
        score = (
            score_live_answer(answer, trial, fork_by_id[str(trial["fork_id"])])
            if valid and answer is not None
            else {
                "answer_success": False,
                "answer_fact_match": False,
                "fact_scope_match": False,
                "side_effect_safe": False,
                "unknown_evidence_record_ids": [],
                "evidence_event_ids": [],
                "required_evidence_recall": 0.0,
                "required_anchor_cited": False,
                "old_fact_used_as_current": False,
            }
        )
        result = {
            "schema_version": "phase6_live_result_v1",
            "trial_id": trial["trial_id"],
            "prefix_id": trial["prefix_id"],
            "fork_id": trial["fork_id"],
            "fork_type": trial["fork_type"],
            "scenario_family": trial["scenario_family"],
            "variant_id": trial["variant_id"],
            "method_id": trial["method_id"],
            "model": model,
            "valid_response": valid,
            "validation_error": validation_error,
            "answer": answer,
            **score,
            "input_tokens": attempt["input_tokens"],
            "output_tokens": attempt["output_tokens"],
            "cost_cny": attempt["cost_cny"],
            "provider_request_sha256": attempt["provider_request_sha256"],
            "request_hash_valid": (
                stable_digest(trial["request_template"])
                == trial["request_template_sha256"]
            ),
            "raw_response_file": attempt["raw_response_file"],
            "raw_response_sha256": attempt["raw_response_sha256"],
        }
        _append_jsonl(results_path, result)
        completed.add(str(trial["trial_id"]))
        new_trials += 1
        if index % 10 == 0 or len(completed) == len(trials):
            totals = _ledger_totals(ledger)
            print(
                _json_text(
                    {
                        "completed_trials": len(completed),
                        "total_trials": len(trials),
                        "provider_requests": totals["requests"],
                        "cost_cny": totals["cost_cny"],
                    }
                ).strip(),
                flush=True,
            )
        if status == 0 or status == 429 or status >= 500:
            print(_json_text({"status": "paused_provider", "http_status": status}).strip())
            return 4
        if status < 200 or status >= 300:
            raise RuntimeError(f"provider request failed with HTTP {status}")
    report = _finalize(output_root, config, selection)
    print(_json_text(report).strip())
    return 0 if report["gates"]["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
