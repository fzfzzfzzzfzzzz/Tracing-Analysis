"""运行仓库的纯本地验证，不允许访问真实模型服务。"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Sequence

from tracegraph.plain_cli import PlainArgumentParser, run_cli


ROOT = Path(__file__).resolve().parents[1]
FROZEN_HASHES = {
    "outputs/compression_audit/v0_1_handoff/manifest.json": (
        "d33c12ce2b2c5dacefa6ce353fb46df80d2a766510aa398ae1a07961d38a249b"
    ),
    "outputs/compression_audit/v0_1_qwen_live_reconciled/run_summary.json": (
        "dbdae96e99d54fede54452778522dd317bdd192276707ca19769f4c0ff6fdca4"
    ),
    "outputs/compression_audit/v0_1_qwen_live_score_reconciled/gate_report.json": (
        "4eb0bd86f94e891a692cae8a4d76befb49380e3c5da35d36bfeff294ddd1d7d9"
    ),
    "outputs/compression_audit/v0_1_qwen_live_score_reconciled/report.json": (
        "109efc483d285726a882514bab52cf2e1eb2ebe2d280a0964fcc84a36d4a654f"
    ),
}


def run(command: Sequence[str], *, env: dict[str, str]) -> None:
    print("+", subprocess.list2cmdline(list(command)), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def validate_json_schemas() -> None:
    import jsonschema

    checked = 0
    for schema_path in sorted((ROOT / "configs").glob("*.schema.json")):
        document_path = schema_path.with_name(schema_path.name.removesuffix(".schema.json") + ".json")
        if not document_path.is_file():
            continue
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        document = json.loads(document_path.read_text(encoding="utf-8"))
        validator = jsonschema.validators.validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(document)
        checked += 1
    if checked == 0:
        raise RuntimeError("no config/schema pairs were validated")
    print(f"validated {checked} config/schema pairs")


def verify_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = project["project"]["version"]
    installed = importlib.metadata.version("tracegraph")
    if installed != expected:
        raise RuntimeError(f"installed tracegraph {installed} != project {expected}")


def verify_frozen_hashes() -> None:
    for relative, expected in FROZEN_HASHES.items():
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(f"frozen artifact is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"frozen artifact changed: {relative}")


def deterministic_smoke(env: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="tracegraph-verify-") as directory:
        root = Path(directory)
        dataset = root / "dataset"
        run_root = root / "run"
        score = root / "score"
        config = "configs/compression_audit_v1.json"
        run([sys.executable, "-m", "tracegraph", "benchmark-build", "--config", config,
             "--output", str(dataset)], env=env)
        run([sys.executable, "-m", "tracegraph", "benchmark-validate", "--dataset",
             str(dataset)], env=env)
        run([sys.executable, "-m", "tracegraph", "benchmark-run", "--config", config,
             "--dataset", str(dataset), "--output", str(run_root), "--mode",
             "deterministic", "--method", "M5_lifecycle_causal_reactivation",
             "--query-type", "distractor_current"], env=env)
        run([sys.executable, "-m", "tracegraph", "benchmark-score", "--dataset",
             str(dataset), "--run", str(run_root), "--output", str(score),
             "--bootstrap-samples", "100"], env=env)
        manifest = json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("provider_requests") != 0:
            raise RuntimeError("deterministic verification made provider requests")
        if manifest.get("episode_count") != 240:
            raise RuntimeError("deterministic verification did not exercise 240 prefixes")


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("core", "full", "release"),
        default="core",
        help="选择核心、完整或发布验证范围",
    )
    args = parser.parse_args()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env["TRACEGRAPH_DISABLE_LIVE"] = "1"

    run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"], env=env)
    run([sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"], env=env)
    run([sys.executable, "-m", "pytest", "-q"], env=env)
    run([sys.executable, "-m", "tracegraph", "--help"], env=env)
    verify_version()

    if args.profile in {"full", "release"}:
        validate_json_schemas()
        deterministic_smoke(env)

    if args.profile == "release":
        run(["uv", "lock", "--check"], env=env)
        verify_frozen_hashes()
        run([sys.executable, "-m", "tracegraph", "benchmark-validate", "--dataset",
             "outputs/compression_audit/v0_1_handoff"], env=env)
        run([sys.executable, "-m", "pytest", "--cov=tracegraph", "--cov-branch",
             "--cov-fail-under=85", "-q"], env=env)
        run(["git", "diff", "--check"], env=env)
        status = subprocess.run(
            ["git", "status", "--porcelain=v1"], cwd=ROOT, env=env, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        if status:
            raise RuntimeError("release verification requires a clean tracked worktree")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
