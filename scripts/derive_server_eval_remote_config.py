#!/usr/bin/env python3
"""Derive a remote-local server config without mutating the frozen base config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tokenizer-path", required=True)
    parser.add_argument("--runtime-receipt-path", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    config = json.loads(args.base.read_text(encoding="utf-8"))
    if len(config.get("models", ())) != 1:
        raise ValueError("remote derivation expects exactly one frozen model")
    model = config["models"][0]
    model["base_url"] = args.base_url
    model["tokenizer"]["path"] = args.tokenizer_path
    model["runtime_receipt"]["path"] = args.runtime_receipt_path
    config["remote_execution"] = {
        "derived_from": str(args.base),
        "transport": "server_localhost_no_ssh_tunnel",
        "model_endpoint_mutation_only": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
