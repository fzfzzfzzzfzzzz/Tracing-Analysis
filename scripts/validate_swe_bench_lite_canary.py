"""Validate one frozen canary image against its failing and gold states."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import uuid
from pathlib import Path

import pandas as pd


def run_container(image: str, command: str, timeout: int) -> subprocess.CompletedProcess[str]:
    name = "tracegraph-canary-validate-" + uuid.uuid4().hex
    argv = [
        "docker",
        "run",
        "--pull=never",
        "--rm",
        "--name",
        name,
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=128",
        "--memory=4g",
        "--cpus=2",
        "--user=0:0",
        "--workdir=/testbed",
        "--entrypoint=/bin/bash",
        image,
        "-lc",
        command,
    ]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30, check=False
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--canary", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = json.loads((args.canary / "source_manifest.json").read_text(encoding="utf-8"))
    tasks = {
        item["id"].rsplit(":", 1)[-1]: item
        for item in (
            json.loads(line)
            for line in (args.canary / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    instances = {item["instance_id"]: item for item in manifest["instances"]}
    task = tasks[args.instance]
    instance = instances[args.instance]
    row = pd.read_parquet(args.dataset).set_index("instance_id").loc[args.instance]

    inspect = subprocess.run(
        ["docker", "image", "inspect", task["image"]],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if inspect.returncode:
        raise SystemExit("pinned image is not installed: " + inspect.stderr.strip())
    image_info = json.loads(inspect.stdout)[0]
    if image_info["Id"] != instance["image_manifest_digest"]:
        raise SystemExit(f"local image ID mismatch: {image_info['Id']}")

    commit_guard = f"test \"$(git rev-parse HEAD)\" = {instance['base_commit']}"
    baseline = run_container(
        task["image"], commit_guard + "\n" + task["evaluator_command"], args.timeout
    )

    gold_patch = base64.b64encode(str(row["patch"]).encode("utf-8")).decode("ascii")
    gold_setup = "\n".join(
        (
            commit_guard,
            f"printf '%s' '{gold_patch}' | base64 -d > /tmp/tracegraph_gold.patch",
            "git apply --check /tmp/tracegraph_gold.patch",
            "git apply /tmp/tracegraph_gold.patch",
        )
    )
    gold = run_container(
        task["image"], gold_setup + "\n" + task["evaluator_command"], args.timeout
    )

    result = {
        "instance_id": args.instance,
        "image_id": image_info["Id"],
        "base_commit": instance["base_commit"],
        "baseline_returncode": baseline.returncode,
        "baseline_expected_failure": baseline.returncode != 0,
        "gold_returncode": gold.returncode,
        "gold_expected_pass": gold.returncode == 0,
        "baseline_output_tail": (baseline.stdout + baseline.stderr)[-4000:],
        "gold_output_tail": (gold.stdout + gold.stderr)[-4000:],
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["baseline_expected_failure"] or not result["gold_expected_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
