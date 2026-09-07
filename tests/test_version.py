from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

import tracegraph


def test_runtime_and_project_versions_match() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    expected = project["project"]["version"]

    assert expected == "0.4.0"
    assert importlib.metadata.version("tracegraph") == expected
    assert tracegraph.__version__ == expected
