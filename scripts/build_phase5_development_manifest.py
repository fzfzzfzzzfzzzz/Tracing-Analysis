#!/usr/bin/env python3
"""Freeze the outcome-blind Phase 5 development-prefix manifest."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tracegraph.phase5_offline import (
    build_development_manifest,
    tool_schema_artifact,
)


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _domain_tool_schemas(domain: str) -> tuple[dict[str, Any], ...]:
    if domain == "retail":
        from tau2.domains.retail.environment import get_environment
    elif domain == "airline":
        from tau2.domains.airline.environment import get_environment
    else:
        raise ValueError(f"unsupported tau3 domain: {domain}")
    environment = get_environment()
    return tuple(
        dict(tool.openai_schema)
        for tool in sorted(environment.get_tools(), key=lambda item: item.name)
    )


def _domains(dataset: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(source["domain"])
                for source in dataset.get("sources", ())
                if isinstance(source, Mapping)
            }
        )
    )


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")


def main() -> int:
    _configure_utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            "outputs/gdsc_r0_audit/decision_points/"
            "decision_point_dataset.json"
        ),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    dataset_path = args.dataset.resolve()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise FileExistsError(
            f"refusing to overwrite existing Phase 5 output: {output_root}"
        )
    dataset = _load_json(dataset_path)
    schemas = tool_schema_artifact(
        {domain: _domain_tool_schemas(domain) for domain in _domains(dataset)}
    )
    manifest = build_development_manifest(
        dataset,
        dataset_path=dataset_path,
        schemas_artifact=schemas,
    )

    output_root.mkdir(parents=True, exist_ok=False)
    _write_new_json(output_root / "tool_schemas.json", schemas)
    _write_new_json(
        output_root / "development_prefix_manifest.json",
        manifest,
    )
    print(
        json.dumps(
            {
                "output_root": output_root.as_posix(),
                "tool_schema_artifact_sha256": schemas["artifact_sha256"],
                "manifest_sha256": manifest["manifest_sha256"],
                "counts": manifest["counts"],
                "external_provider_generations": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
