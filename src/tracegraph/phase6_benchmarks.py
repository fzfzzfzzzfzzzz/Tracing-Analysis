"""Prepare public benchmark inputs without calling a model provider."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = "phase6_benchmark_preparation_v1"
SELECTION_SCHEMA_VERSION = "phase6_benchmark_task_splits_v1"
SUPPORTED_DATA_SUFFIXES = {".json", ".jsonl", ".parquet"}


def sha256_file(path: Path) -> str:
    """Return a lowercase SHA-256 digest for one file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_bytes(_canonical_bytes(payload))


def _workspace_path(workspace: Path, configured: str) -> Path:
    candidate = (workspace / configured).resolve()
    root = workspace.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"configured path escapes workspace: {configured}")
    return candidate


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"required text field is invalid: {name}")
    return value


def load_benchmark_config(path: Path) -> dict[str, Any]:
    """Load and validate the local preparation configuration."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("benchmark config must be a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported benchmark config schema_version")
    _require_text(payload.get("run_id"), "run_id")
    _require_text(payload.get("output_root"), "output_root")
    if payload.get("external_provider_calls_allowed") is not False:
        raise ValueError("benchmark preparation must forbid provider calls")
    benchmarks = payload.get("benchmarks")
    if not isinstance(benchmarks, list) or not benchmarks:
        raise ValueError("benchmarks must be a non-empty list")
    seen_ids: set[str] = set()
    for index, item in enumerate(benchmarks):
        if not isinstance(item, dict):
            raise ValueError(f"benchmarks[{index}] must be an object")
        benchmark_id = _require_text(item.get("id"), f"benchmarks[{index}].id")
        if benchmark_id in seen_ids:
            raise ValueError(f"duplicate benchmark id: {benchmark_id}")
        seen_ids.add(benchmark_id)
        availability = item.get("availability")
        if availability not in {"public", "pending"}:
            raise ValueError(f"invalid availability for {benchmark_id}")
        upstream = item.get("upstream")
        if not isinstance(upstream, dict):
            raise ValueError(f"missing upstream source for {benchmark_id}")
        code_commit = _require_text(upstream.get("code_commit"), "code_commit")
        if len(code_commit) != 40 or any(character not in "0123456789abcdef" for character in code_commit):
            raise ValueError(f"invalid code commit for {benchmark_id}")
        data_revision = upstream.get("data_revision")
        if availability == "public":
            if not isinstance(data_revision, str) or len(data_revision) != 40:
                raise ValueError(f"public dataset revision is not frozen for {benchmark_id}")
        elif data_revision is not None:
            raise ValueError(f"pending dataset must not claim a revision for {benchmark_id}")
        fields = item.get("task_id_fields")
        if not isinstance(fields, list) or not fields or not all(
            isinstance(field, str) and field for field in fields
        ):
            raise ValueError(f"task_id_fields are invalid for {benchmark_id}")
    budget = payload.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("budget must be an object")
    if budget.get("default_model") != "qwen3.8-27b":
        raise ValueError("default model must remain qwen3.8-27b")
    prices = budget.get("prices_per_million_tokens")
    if not isinstance(prices, dict) or "qwen3.8-27b" not in prices:
        raise ValueError("missing qwen3.8-27b price snapshot")
    return payload


def _git_head(path: Path) -> str | None:
    if not path.is_dir():
        return None
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    return value if len(value) == 40 else None


def _iter_json_records(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"JSONL row is not an object: {path}:{line_number}")
                yield payload
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = next(
            (
                payload[key]
                for key in ("records", "instances", "tasks", "data")
                if isinstance(payload.get(key), list)
            ),
            [payload],
        )
    else:
        raise ValueError(f"JSON data is not an object or list: {path}")
    for record in records:
        if isinstance(record, dict):
            yield record


def _iter_parquet_records(path: Path) -> Iterator[dict[str, Any]]:
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "parquet support missing; install pyarrow in the isolated benchmark environment"
        ) from error
    table = parquet.read_table(path)
    yield from table.to_pylist()


def _iter_records(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix.lower() in {".json", ".jsonl"}:
        yield from _iter_json_records(path)
    elif path.suffix.lower() == ".parquet":
        yield from _iter_parquet_records(path)


def _record_task_id(record: dict[str, Any], fields: list[str]) -> str | None:
    for field in fields:
        value = record.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _record_split(record: dict[str, Any], fields: list[str], default: str) -> str:
    for field in fields:
        value = record.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _load_selections(
    path: Path,
    benchmark_id: str,
) -> tuple[dict[str, list[str]], list[str]]:
    errors: list[str] = []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}, ["任务划分文件不是 JSON 对象。"]
    if payload.get("schema_version") != SELECTION_SCHEMA_VERSION:
        errors.append("任务划分文件的版本不受支持。")
    if payload.get("benchmark_id") != benchmark_id:
        errors.append("任务划分文件写的公开测试任务名称不一致。")
    raw_splits = payload.get("splits")
    if not isinstance(raw_splits, dict):
        return {}, errors + ["任务划分文件缺少 splits。"]
    selections: dict[str, list[str]] = {}
    seen: dict[str, str] = {}
    for split_name, raw_ids in raw_splits.items():
        if not isinstance(split_name, str) or not isinstance(raw_ids, list):
            errors.append("任务划分名称或编号列表格式不对。")
            continue
        task_ids = [str(value).strip() for value in raw_ids if str(value).strip()]
        if len(task_ids) != len(set(task_ids)):
            errors.append(f"{split_name} 中有重复任务编号。")
        selections[split_name] = task_ids
        for task_id in task_ids:
            previous = seen.get(task_id)
            if previous is not None and previous != split_name:
                errors.append(
                    f"任务编号 {task_id} 同时出现在 {previous} 和 {split_name}。"
                )
            else:
                seen[task_id] = split_name
    if not any(selections.values()):
        errors.append("任务划分文件还没有选入任何任务编号。")
    return selections, errors


@dataclass(frozen=True)
class BudgetInput:
    task_count: int | None = None
    methods_per_task: int | None = None
    trials_per_method: int | None = None
    requests_per_trial: int | None = None
    input_tokens_per_request: int | None = None
    output_tokens_per_request: int | None = None
    maximum_cost_cny: float | None = None
    model: str | None = None


def estimate_budget(config: dict[str, Any], inputs: BudgetInput) -> dict[str, Any]:
    """Calculate an upper-bound cost without sending any request."""

    defaults = config["budget"]
    model = inputs.model or defaults["default_model"]
    prices = defaults["prices_per_million_tokens"]
    if model not in prices:
        raise ValueError(f"unsupported model for dry-run budget: {model}")
    values = {
        "methods_per_task": (
            defaults["methods_per_task"]
            if inputs.methods_per_task is None
            else inputs.methods_per_task
        ),
        "trials_per_method": (
            defaults["trials_per_method"]
            if inputs.trials_per_method is None
            else inputs.trials_per_method
        ),
        "requests_per_trial": (
            defaults.get("requests_per_trial", 1)
            if inputs.requests_per_trial is None
            else inputs.requests_per_trial
        ),
        "input_tokens_per_request": (
            defaults["input_tokens_per_request"]
            if inputs.input_tokens_per_request is None
            else inputs.input_tokens_per_request
        ),
        "output_tokens_per_request": (
            defaults["output_tokens_per_request"]
            if inputs.output_tokens_per_request is None
            else inputs.output_tokens_per_request
        ),
    }
    if any(not isinstance(value, int) or value <= 0 for value in values.values()):
        raise ValueError("budget counts and token estimates must be positive")
    maximum_cost = (
        inputs.maximum_cost_cny
        if inputs.maximum_cost_cny is not None
        else defaults["maximum_cost_cny"]
    )
    if maximum_cost <= 0:
        raise ValueError("maximum cost budget must be positive")
    task_count = inputs.task_count
    if task_count is None:
        return {
            "status": "needs_task_count",
            "model": model,
            "maximum_cost_cny": maximum_cost,
            **values,
            "provider_requests_made": 0,
            "message": "还没有确定任务数量，因此现在只能保存计价输入，不能得出总费用。",
        }
    if task_count <= 0:
        raise ValueError("task count must be positive")
    request_count = (
        task_count
        * values["methods_per_task"]
        * values["trials_per_method"]
        * values["requests_per_trial"]
    )
    total_input = request_count * values["input_tokens_per_request"]
    total_output = request_count * values["output_tokens_per_request"]
    price = prices[model]
    estimated_cost = (
        total_input * float(price["input"]) + total_output * float(price["output"])
    ) / 1_000_000
    return {
        "status": "within_limit" if estimated_cost <= maximum_cost else "over_limit",
        "model": model,
        "task_count": task_count,
        **values,
        "request_count": request_count,
        "estimated_input_tokens": total_input,
        "estimated_output_tokens": total_output,
        "estimated_cost_cny": estimated_cost,
        "maximum_cost_cny": maximum_cost,
        "remaining_cny": maximum_cost - estimated_cost,
        "provider_requests_made": 0,
    }


def _inspect_benchmark(
    item: dict[str, Any],
    workspace: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    benchmark_id = item["id"]
    errors: list[str] = []
    warnings: list[str] = []
    files: list[dict[str, Any]] = []
    tasks: list[dict[str, str]] = []

    upstream = item["upstream"]
    code_root = _workspace_path(workspace, item["local_code_root"])
    code_head = _git_head(code_root)
    if code_head is None:
        errors.append("本地还没有官方程序代码，或该目录不是 Git 仓库。")
    elif code_head != upstream["code_commit"]:
        errors.append("本地官方程序代码的版本与配置中固定的版本不一致。")

    if item["availability"] == "pending":
        errors.append(
            "官方数据目前仍标为尚未公开；不能用旧 CodeQA 或自行生成数据冒充正式数据。"
        )

    data_root = _workspace_path(workspace, item["local_data_root"])
    if not data_root.is_dir():
        errors.append(f"本地还没有数据目录：{item['local_data_root']}")
        data_paths: list[Path] = []
    else:
        data_paths = sorted(
            path
            for path in data_root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_DATA_SUFFIXES
        )
        if not data_paths:
            errors.append("本地数据目录中没有可检查的 JSON、JSONL 或 Parquet 文件。")

    expected_by_path = {
        expected["path"]: expected for expected in item.get("expected_files", [])
    }
    for relative, expected in expected_by_path.items():
        path = data_root / relative
        if not path.is_file():
            errors.append(f"缺少固定的数据文件：{relative}")
            continue
        actual_size = path.stat().st_size
        actual_hash = sha256_file(path)
        if actual_size != expected["bytes"] or actual_hash != expected["sha256"]:
            errors.append(f"数据文件的大小或 SHA-256 文件指纹不一致：{relative}")

    known_task_ids: set[str] = set()
    source_split_by_id: dict[str, str] = {}
    task_fields = item["task_id_fields"]
    split_fields = item.get("source_split_fields", ["split"])
    default_split = item.get("default_source_split", "train")
    for path in data_paths:
        relative = path.relative_to(data_root).as_posix()
        files.append(
            {
                "benchmark_id": benchmark_id,
                "kind": "data",
                "path": f"{item['local_data_root'].rstrip('/')}/{relative}",
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
        try:
            records = _iter_records(path)
            for record in records:
                task_id = _record_task_id(record, task_fields)
                if task_id is None:
                    warnings.append(f"有一条数据没有任务编号：{relative}")
                    continue
                source_split = _record_split(record, split_fields, default_split)
                previous_split = source_split_by_id.get(task_id)
                if previous_split is not None:
                    if previous_split == source_split:
                        errors.append(f"源数据中任务编号重复：{task_id}")
                    else:
                        errors.append(
                            f"源数据任务 {task_id} 同时出现在 {previous_split} 和 {source_split}。"
                        )
                    continue
                source_split_by_id[task_id] = source_split
                known_task_ids.add(task_id)
                tasks.append(
                    {
                        "benchmark_id": benchmark_id,
                        "task_id": task_id,
                        "source_split": source_split,
                        "experiment_split": "",
                        "source_file": relative,
                    }
                )
        except (json.JSONDecodeError, OSError, RuntimeError, ValueError):
            errors.append(f"无法读取数据文件 {relative}；请检查文件格式和读取依赖。")

    expected_rows = item.get("expected_rows")
    if data_paths and expected_rows is not None and len(known_task_ids) != expected_rows:
        errors.append(
            f"读到 {len(known_task_ids)} 个任务，但固定的数量是 {expected_rows}。"
        )

    selection_path = _workspace_path(workspace, item["selection_file"])
    selections: dict[str, list[str]] = {}
    if not selection_path.is_file():
        errors.append(f"还没有固定任务划分文件：{item['selection_file']}")
    else:
        selections, selection_errors = _load_selections(selection_path, benchmark_id)
        errors.extend(selection_errors)
        files.append(
            {
                "benchmark_id": benchmark_id,
                "kind": "task_selection",
                "path": item["selection_file"],
                "bytes": selection_path.stat().st_size,
                "sha256": sha256_file(selection_path),
            }
        )
        selected_ids = {task_id for values in selections.values() for task_id in values}
        if known_task_ids:
            unknown = sorted(selected_ids - known_task_ids)
            if unknown:
                errors.append(
                    f"任务划分中有 {len(unknown)} 个编号不在固定的本地数据里。"
                )
        for split_name, task_ids in sorted(selections.items()):
            tasks.extend(
                {
                    "benchmark_id": benchmark_id,
                    "task_id": task_id,
                    "source_split": source_split_by_id.get(task_id, ""),
                    "experiment_split": split_name,
                    "source_file": item["selection_file"],
                }
                for task_id in task_ids
            )

    return (
        {
            "benchmark_id": benchmark_id,
            "official_name": item["official_name"],
            "availability": item["availability"],
            "code_commit_expected": upstream["code_commit"],
            "code_commit_local": code_head,
            "data_revision_expected": upstream.get("data_revision"),
            "data_file_count": len(data_paths),
            "task_count": len(known_task_ids),
            "selected_task_count": sum(len(values) for values in selections.values()),
            "status": "ready" if not errors else "blocked",
            "errors": errors,
            "warnings": sorted(set(warnings)),
        },
        files,
        tasks,
    )


def prepare_benchmarks(
    config_path: Path,
    *,
    workspace: Path,
    output_root: Path | None = None,
    benchmark_ids: set[str] | None = None,
    budget_input: BudgetInput | None = None,
) -> dict[str, Any]:
    """Inspect local inputs and write an append-only preparation result tree."""

    config = load_benchmark_config(config_path)
    selected = [
        item
        for item in config["benchmarks"]
        if benchmark_ids is None or item["id"] in benchmark_ids
    ]
    if not selected:
        raise ValueError("no configured benchmark matched the requested ids")
    unknown = (benchmark_ids or set()) - {item["id"] for item in config["benchmarks"]}
    if unknown:
        raise ValueError(f"unknown benchmark ids: {sorted(unknown)}")
    destination = (
        output_root.resolve()
        if output_root is not None
        else _workspace_path(workspace, config["output_root"])
    )
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"output directory is non-empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    source_reports: list[dict[str, Any]] = []
    input_files: list[dict[str, Any]] = []
    task_rows: list[dict[str, str]] = []
    for item in selected:
        report, files, tasks = _inspect_benchmark(item, workspace)
        source_reports.append(report)
        input_files.extend(files)
        task_rows.extend(tasks)

    selected_count = sum(report["selected_task_count"] for report in source_reports)
    actual_budget_input = budget_input or BudgetInput()
    if actual_budget_input.task_count is None and selected_count:
        actual_budget_input = BudgetInput(
            task_count=selected_count,
            methods_per_task=actual_budget_input.methods_per_task,
            trials_per_method=actual_budget_input.trials_per_method,
            requests_per_trial=actual_budget_input.requests_per_trial,
            input_tokens_per_request=actual_budget_input.input_tokens_per_request,
            output_tokens_per_request=actual_budget_input.output_tokens_per_request,
            maximum_cost_cny=actual_budget_input.maximum_cost_cny,
            model=actual_budget_input.model,
        )
    budget_report = estimate_budget(config, actual_budget_input)
    blocked = any(report["status"] != "ready" for report in source_reports)
    if budget_report["status"] == "over_limit":
        blocked = True
    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": config["run_id"],
        "prepared_at": config["prepared_at"],
        "status": "blocked" if blocked else "ready",
        "external_provider_calls_allowed": False,
        "provider_requests_made": 0,
        "sources": source_reports,
        "budget": budget_report,
    }

    snapshot = dict(config)
    snapshot["benchmarks"] = selected
    _write_json(destination / "source_config_snapshot.json", snapshot)
    _write_json(destination / "preparation_report.json", report)
    _write_json(destination / "budget_dry_run.json", budget_report)
    with (destination / "input_file_manifest.jsonl").open("w", encoding="utf-8") as handle:
        for row in sorted(input_files, key=lambda value: (value["benchmark_id"], value["path"])):
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with (destination / "task_inventory.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "benchmark_id",
            "task_id",
            "source_split",
            "experiment_split",
            "source_file",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(
            sorted(
                task_rows,
                key=lambda row: (
                    row["benchmark_id"],
                    row["task_id"],
                    row["experiment_split"],
                    row["source_file"],
                ),
            )
        )
    output_names = [
        "budget_dry_run.json",
        "input_file_manifest.jsonl",
        "preparation_report.json",
        "source_config_snapshot.json",
        "task_inventory.csv",
    ]
    output_hashes = {name: sha256_file(destination / name) for name in output_names}
    _write_json(destination / "hashes.json", output_hashes)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": config["run_id"],
        "status": report["status"],
        "files": output_names + ["hashes.json"],
        "hash_algorithm": "SHA-256",
        "provider_requests_made": 0,
    }
    _write_json(destination / "manifest.json", manifest)
    return report
