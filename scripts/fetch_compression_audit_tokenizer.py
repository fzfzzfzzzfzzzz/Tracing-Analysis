"""Fetch only the hash-pinned public tokenizer; never download model weights."""

from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path

from tracegraph.benchmark.compression_audit.dataset import file_sha256, load_config
from tracegraph.plain_cli import PlainArgumentParser


def main() -> int:
    parser = PlainArgumentParser(description="下载并校验 benchmark 固定的官方 tokenizer，不调用模型。")
    parser.add_argument("--config", type=Path, required=True, help="含 tokenizer SHA-256 的配置文件")
    args = parser.parse_args()
    specification = load_config(args.config)["v0_live"]["context_tokenizer"]
    destination = (Path.cwd() / specification["path"]).resolve()
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(specification["download_url"], timeout=30) as response:
            with destination.open("xb") as handle:
                shutil.copyfileobj(response, handle)
    actual_hash = file_sha256(destination)
    if actual_hash != specification["sha256"]:
        raise ValueError("tokenizer hash mismatch; existing data is preserved and will not be overwritten")
    print(json.dumps({"path": str(destination), "sha256": actual_hash, "provider_requests": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
