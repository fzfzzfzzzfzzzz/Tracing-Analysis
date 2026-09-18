"""Download only the hash-pinned tokenizer for the v0.2 pilot; no model calls."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

from tracegraph.benchmark.compression_audit.development_experiment import load_pilot_config
from tracegraph.plain_cli import PlainArgumentParser, run_cli


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_pilot_config(args.config)
    specification = config["tokenizer"]
    if not specification:
        raise ValueError("configuration has no verified tokenizer source")
    path = Path(specification["path"])
    if path.exists():
        payload = path.read_bytes()
    else:
        source = specification["source"]
        if not source.startswith("https://huggingface.co/Qwen/Qwen3.8-Flash-Next/resolve/"):
            raise ValueError("expected the pinned official model source")
        with urllib.request.urlopen(source, timeout=30) as response:
            payload = response.read(30_000_001)
        if len(payload) > 30_000_000:
            raise ValueError("unexpected tokenizer download size")
    if hashlib.sha256(payload).hexdigest() != specification["sha256"]:
        raise ValueError("tokenizer content does not match the frozen SHA-256")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(payload)
    print(f"verified tokenizer: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
