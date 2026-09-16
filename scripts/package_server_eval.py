"""Create a portable server bundle from an allowlist; never include credentials."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from tracegraph.plain_cli import PlainArgumentParser


def package(workspace: Path, dataset: Path, output: Path,
            config: Path | None = None) -> dict:
    from tracegraph.benchmark.compression_audit.build import verify_file_manifest
    verify_file_manifest(dataset)
    if output.exists():
        raise ValueError("bundle output must be new")
    selected = {}
    for directory in ("src/tracegraph", "tests"):
        for file in (workspace / directory).rglob("*.py"):
            selected[file.relative_to(workspace).as_posix()] = file
    for directory, glob in (("configs", "*.json"), ("scripts", "*.py"), ("scripts", "*.sh"),
                            ("docs", "*服务器*.md")):
        for file in (workspace / directory).glob(glob):
            selected[file.relative_to(workspace).as_posix()] = file
    for relative in ("pyproject.toml", "uv.lock", "README.md", "LICENSE"):
        if (workspace / relative).exists():
            selected[relative] = workspace / relative
    if config is not None:
        config = config.resolve()
        if not config.is_file() or not config.is_relative_to(workspace.resolve()):
            raise ValueError("explicit config must be a workspace file")
        selected[config.relative_to(workspace.resolve()).as_posix()] = config
    for file in dataset.rglob("*"):
        if file.is_file():
            selected["data/server_eval/" + file.relative_to(dataset).as_posix()] = file
    sources = {}
    for filename in ("server_eval_qwen38.json", "server_eval_qwen38_extended.json"):
        path = workspace / "configs" / filename
        if path.exists():
            sources.update(json.loads(path.read_text(encoding="utf-8"))["sources"])
    for source in sources.values():
        for relative, expected in source["files"].items():
            file = workspace / source["path"] / relative
            if file.is_file():
                if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                    raise ValueError("official source differs from frozen manifest")
                selected[source["path"] + "/" + relative] = file
    hashes = {}
    for relative, file in selected.items():
        if file.is_symlink() or not file.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("bundle source must be a regular workspace file")
        if any(part.startswith(".env") or part == ".git" for part in Path(relative).parts):
            raise ValueError("credential or Git metadata is forbidden in bundle")
        hashes[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest = {"format": "server_eval_bundle_v1", "files": hashes,
                "model_weights_included": False, "credentials_included": False,
                "dataset_path": "data/server_eval", "provider_requests": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for relative, file in sorted(selected.items()):
            archive.writestr(relative, file.read_bytes())
        archive.writestr("server_bundle_manifest.json", json.dumps(manifest, indent=2))
    return {"file": str(output), "files": len(hashes), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = PlainArgumentParser(description="打包服务器评测代码、数据和固定源码，不包含凭证或模型权重。")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(Path.cwd(), args.dataset, args.output, args.config), indent=2))
