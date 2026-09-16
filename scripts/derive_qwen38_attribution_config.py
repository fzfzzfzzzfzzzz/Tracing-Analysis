"""Derive the frozen Qwen3.8-27B pytest-5221 attribution config.

The already exposed task and its agent protocol remain unchanged.  This helper
only replaces the model/runtime identity and Qwen3.8's documented thinking
sampling parameters, while refusing an unexpected source configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SOURCE_SHA256 = "1fe232c27bf6d5043271d8ad285979a0724339a2f3aa5812548c313814d00bed"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--weights-revision", required=True)
    parser.add_argument("--served-model", required=True)
    parser.add_argument("--server-version", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--seed", type=int, default=20260972)
    parser.add_argument("--calibration-exclude-prefix-id", action="append", default=[])
    parser.add_argument("--calibration-prefix-id", action="append", default=[])
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(args.output)
    if sha256(args.source) != SOURCE_SHA256:
        raise ValueError("unexpected source attribution config")
    if not args.tokenizer_dir.is_dir() or not args.runtime_receipt.is_file():
        raise ValueError("tokenizer directory and runtime receipt must exist")

    config = json.loads(args.source.read_text(encoding="utf-8"))
    if len(config["models"]) != 1 or config["models"][0]["id"] != "qwen3_14b":
        raise ValueError("source model slot differs")

    exclusions = list(config["data"].get("calibration_exclude_prefix_ids", []))
    for prefix_id in args.calibration_exclude_prefix_id:
        if prefix_id not in exclusions:
            exclusions.append(prefix_id)
    if len(exclusions) != len(set(exclusions)):
        raise ValueError("duplicate calibration exclusion")
    if args.calibration_prefix_id:
        if len(args.calibration_prefix_id) != 8 or len(set(args.calibration_prefix_id)) != 8:
            raise ValueError("fresh calibration requires eight distinct prefix IDs")
        if set(args.calibration_prefix_id) & set(exclusions):
            raise ValueError("fresh calibration overlaps an exposed prefix")
        config["data"]["calibration_prefix_ids"] = args.calibration_prefix_id
    config["data"]["calibration_exclude_prefix_ids"] = exclusions

    tokenizer_files = {
        path.relative_to(args.tokenizer_dir).as_posix(): sha256(path)
        for path in args.tokenizer_dir.rglob("*")
        if path.is_file()
    }
    if not {"tokenizer.json", "tokenizer_config.json"} <= tokenizer_files.keys():
        raise ValueError("incomplete tokenizer snapshot")

    model = config["models"][0]
    model.update(
        id="qwen38_27b",
        display_name="Qwen/Qwen3.8-27B BF16 TP4 pytest-5221 attribution r1",
        base_url=args.base_url,
        served_model=args.served_model,
        returned_model_allowlist=[args.served_model],
        weights_revision=args.weights_revision,
        server_version=args.server_version,
        tokenizer={"path": str(args.tokenizer_dir), "files": tokenizer_files},
        runtime_receipt={
            "path": str(args.runtime_receipt),
            "sha256": sha256(args.runtime_receipt),
        },
        thinking_sampling={
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
        },
    )
    config["judge_model_id"] = "qwen38_27b"
    config["seed"] = args.seed
    config["model_upgrade"] = {
        "source_model": "Qwen/Qwen3-14B",
        "target_model": "Qwen/Qwen3.8-27B",
        "changed_fields": [
            "model weights and tokenizer",
            "model serving runtime",
            "tensor parallel size 2 to 4",
            "thinking sampling temperature 0.6 to 1.0",
        ],
        "held_constant": [
            "exposed task",
            "full-history method",
            "32768-token context window",
            "2048-token per-turn output limit",
            "40-call agent ceiling",
            "native bash tool transport",
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
