"""Freeze the development-only SWE-bench Lite trajectory canary.

This is a collection-pipeline check, not an independent benchmark result.  It
uses one repository so that the whole repository can be excluded from the
later formal validation set without discarding many repositories.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shlex
from pathlib import Path

import pandas as pd


DATASET_REVISION = "1056b1963e8af34a3010add7c73a413a1ffe317b"
DATASET_SHA256 = "7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4"
DATASET_BYTES = 1_119_540
SELECTION_SEED = "tracegraph-real-canary-260914-r1"
REPOSITORY = "pytest-dev/pytest"
SELECTED = (
    "pytest-dev__pytest-5413",
    "pytest-dev__pytest-7220",
    "pytest-dev__pytest-7490",
    "pytest-dev__pytest-5227",
    "pytest-dev__pytest-7168",
    "pytest-dev__pytest-5495",
    "pytest-dev__pytest-5221",
    "pytest-dev__pytest-5103",
)
IMAGE_DIGESTS = {
    "pytest-dev__pytest-5413": "8a97420ed474c427571c6f9ebffc7b6ca8b66b96d59b36c850b64cb50e8fa686",
    "pytest-dev__pytest-7220": "eaa0b259f3518bb572d3796c41d56f90f510de0d034f549d8dfc9280e88d1537",
    "pytest-dev__pytest-7490": "c45a2ab9017bc1659864ed9e82e9aff772fb596c1790d18280f04cb313bbeb49",
    "pytest-dev__pytest-5227": "50368ff0f6c8b591e4f78cae3f244a14e6de59e1798d09028ccad446d379ffa4",
    "pytest-dev__pytest-7168": "5ffc8edc6b8f44db44c9a9f88c23745795f878edb13d641f2f9cef8c50228f15",
    "pytest-dev__pytest-5495": "813c99cc34228450df683c8a3693d88266355f5b780569dcdd92562cb4ce4dd5",
    "pytest-dev__pytest-5221": "798111aafcff014e884ce2dbef8661b850ec0f69abeda32bdf6f5d8e74d6ef10",
    "pytest-dev__pytest-5103": "e4704aa1479bc1aa9e2491e59dbc1a5c3eadbdf4eb600279e50ba39abacbf610",
}
IMAGE_PREFIX = "ghcr.io/epoch-research/swe-bench.eval.x86_64."
STATE_CONTRACT = "filesystem_and_explicit_env_no_background_services"
SYSTEM_PROMPT = """You are a software engineer fixing one SWE-bench issue in the repository already checked out at /testbed.
You must implement the requested change in non-test source files; do not merely explain or advise.
The repository, Git metadata, and dependencies are already prepared. Do not clone or initialize a repository, configure or commit Git, change revisions or branches, reset the worktree, install packages, use the network, or start background services.

Work interactively and step by step:
1. Inspect the current repository and relevant source files.
2. Reproduce or locate the faulty behavior.
3. Edit only the source files needed for a general fix. Do not edit tests or project configuration.
4. Run focused tests and inspect the final diff. Resolve failures before submitting. A submission with an empty `git diff` is invalid.

Every response must use the required structured action with exactly two nonempty fields: `thought` for concise planning and `command` for one bash action. The harness will execute `command` and return its observation.
Each block runs in a fresh shell with /testbed as its working directory. Make dependent commands fail fast with `set -e` or `&&` so an early failure cannot be hidden by a later successful command.
Do not submit until you have made the source change and verified it. In a separate final response, set `command` to only:
printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n%s\\n' 'your short implementation and test summary'
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_list(value: object, field: str, instance_id: str, *, require_nonempty: bool) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value)
    if (
        not isinstance(value, list)
        or (require_nonempty and not value)
        or not all(isinstance(item, str) and item for item in value)
    ):
        requirement = "a nonempty" if require_nonempty else "a"
        raise ValueError(f"{instance_id}: {field} must be {requirement} string list")
    return value


def evaluator_command(test_patch: str, fail_to_pass: list[str]) -> str:
    encoded = base64.b64encode(test_patch.encode("utf-8")).decode("ascii")
    tests = " ".join(shlex.quote(test_id) for test_id in fail_to_pass)
    return "\n".join(
        (
            "set -euo pipefail",
            f"printf '%s' {shlex.quote(encoded)} | base64 -d > /tmp/tracegraph_test.patch",
            "git apply --check /tmp/tracegraph_test.patch",
            "git apply /tmp/tracegraph_test.patch",
            f"python -m pytest -q {tests}",
        )
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite frozen output: {output}")
    if dataset.stat().st_size != DATASET_BYTES or sha256(dataset) != DATASET_SHA256:
        raise ValueError("dataset identity does not match the pinned ModelScope revision")

    frame = pd.read_parquet(dataset)
    rows = frame.set_index("instance_id", drop=False)
    candidates = frame.loc[frame["repo"] == REPOSITORY, "instance_id"].tolist()
    expected_order = sorted(
        candidates,
        key=lambda instance_id: hashlib.sha256(
            f"{SELECTION_SEED}|{instance_id}".encode("utf-8")
        ).hexdigest(),
    )[: len(SELECTED)]
    if tuple(expected_order) != SELECTED:
        raise ValueError("selection no longer reproduces from the pinned seed")

    tasks: list[dict[str, object]] = []
    instances: list[dict[str, object]] = []
    for instance_id in SELECTED:
        row = rows.loc[instance_id]
        if row["repo"] != REPOSITORY:
            raise ValueError(f"unexpected repository for {instance_id}")
        fail_to_pass = parse_list(
            row["FAIL_TO_PASS"], "FAIL_TO_PASS", instance_id, require_nonempty=True
        )
        pass_to_pass = parse_list(
            row["PASS_TO_PASS"], "PASS_TO_PASS", instance_id, require_nonempty=False
        )
        digest = IMAGE_DIGESTS[instance_id]
        image = f"{IMAGE_PREFIX}{instance_id}@sha256:{digest}"
        tasks.append(
            {
                "id": f"realcanary:swebench_lite:{instance_id}",
                "image": image,
                "cwd": "/testbed",
                "task": str(row["problem_statement"]),
                "system_prompt": SYSTEM_PROMPT,
                "evaluator_command": evaluator_command(str(row["test_patch"]), fail_to_pass),
                "max_model_calls": 12,
                "wall_seconds": 1800,
                "tool_timeout": 60,
                "memory": "4g",
                "user": "0:0",
                "state_contract": STATE_CONTRACT,
            }
        )
        instances.append(
            {
                "instance_id": instance_id,
                "repo": str(row["repo"]),
                "base_commit": str(row["base_commit"]),
                "version": str(row["version"]),
                "image": image,
                "image_manifest_digest": f"sha256:{digest}",
                "problem_statement_sha256": hashlib.sha256(
                    str(row["problem_statement"]).encode("utf-8")
                ).hexdigest(),
                "test_patch_sha256": hashlib.sha256(str(row["test_patch"]).encode("utf-8")).hexdigest(),
                "gold_patch_sha256": hashlib.sha256(str(row["patch"]).encode("utf-8")).hexdigest(),
                "fail_to_pass": fail_to_pass,
                "pass_to_pass_count": len(pass_to_pass),
            }
        )

    output.mkdir(parents=True)
    tasks_path = output / "tasks.jsonl"
    tasks_path.write_text(
        "".join(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n" for task in tasks),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "tracegraph_real_canary_v1",
        "frozen_at": "2026-09-14",
        "development_only": True,
        "independent_validation": False,
        "purpose": "real-trajectory collection and evaluation-pipeline canary",
        "benchmark": "princeton-nlp/SWE-bench_Lite",
        "mirror": "ModelScope",
        "dataset_revision": DATASET_REVISION,
        "dataset_file": "data/test-00000-of-00001.parquet",
        "dataset_bytes": DATASET_BYTES,
        "dataset_sha256": DATASET_SHA256,
        "selection_seed": SELECTION_SEED,
        "selection_algorithm": "ascending sha256(seed + '|' + instance_id), first 8 within repository",
        "selected_repository": REPOSITORY,
        "selected_count": len(tasks),
        "excluded_from_formal_validation": True,
        "formal_validation_excluded_repository": REPOSITORY,
        "evaluator_scope": "apply hidden test_patch, run FAIL_TO_PASS only",
        "evaluator_limitation": "pipeline canary only; not the official full SWE-bench score",
        "agent_prompt_provenance": {
            "upstream": "SWE-agent/mini-swe-agent",
            "revision": "04d809ceab9df28f9adaed044884180159172930",
            "source": "src/minisweagent/config/benchmarks/swebench.yaml",
            "adaptation": "preserves working-directory, source-only, iterative, test, and submission boundaries while using this harness's strict thought/command JSON transport"
        },
        "images": "Epoch Research GHCR SWE-bench evaluation images, pinned by manifest digest",
        "instances": instances,
        "tasks_jsonl_sha256": sha256(tasks_path),
    }
    write_json(output / "source_manifest.json", manifest)
    print(json.dumps({"output": str(output), "tasks": len(tasks), "tasks_sha256": manifest["tasks_jsonl_sha256"]}))


if __name__ == "__main__":
    main()
