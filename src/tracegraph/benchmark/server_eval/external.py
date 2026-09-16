"""Verified official algorithm loading, with explicit host adaptations in provenance."""

from __future__ import annotations

import importlib
import importlib.util
import builtins
import subprocess
import sys
import types
import uuid
from pathlib import Path

from ..compression_audit.io import file_sha256


def verify_source(spec: dict, workspace: Path) -> Path:
    root = (workspace / spec["path"]).resolve()
    for relative, digest in spec["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or file_sha256(path) != digest:
            raise ValueError("official source missing or changed: " + relative)
    if len(spec["revision"]) != 40:
        raise ValueError("pin an official source commit")
    return root


def load_package(name: str, root: Path) -> None:
    module = types.ModuleType(name)
    module.__path__ = [str(root)]
    sys.modules[name] = module


def acon_optimizers(spec: dict, workspace: Path, llm):
    root = verify_source(spec, workspace)
    namespace = "_tracegraph_acon_" + uuid.uuid4().hex
    load_package(namespace, root / "src/productive_agents/ctxopt")
    history = importlib.import_module(namespace + ".history_optimizer")
    observation = importlib.import_module(namespace + ".obs_optimizer")
    base = {"model": llm.model, "temperature": 0, "compressor_type": "full",
            "history_prompt_dir": str(root / spec["prompt_dir"]),
            "obs_prompt_dir": str(root / spec["prompt_dir"]),
            "history_summarization_threshold": -1,
            "prompts": {"prompt_system": "system_prompt", "prompt_user": "prompt_user",
                        "prompt_history_user": "prompt_history_v2"}}
    return (history.HistoryOptimizer(dict(base), debug_mode=False, llm=llm),
            observation.ObservationOptimizer(dict(base), debug_mode=False, llm=llm))


def docker_search(script: Path, image: str, timeout: float):
    """Only the generated script (containing public trajectory data) is mounted.

    No credentials, dataset gold, source workspace, host network, or writeable host
    directory is exposed. The image is preinstalled and digest-pinned; never pulled.
    """
    if "@sha256:" not in image or len(image.rsplit("@sha256:", 1)[1]) != 64:
        raise ValueError("AMA code search requires a local digest-pinned Docker image")
    name = "tracegraph-search-" + uuid.uuid4().hex
    cmd = ["docker", "run", "--pull=never", "--rm", "--name", name,
           "--network=none", "--read-only", "--cap-drop=ALL",
           "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=256m",
           "--cpus=1", "--user=65534:65534", "--tmpfs=/tmp:rw,noexec,size=16m",
           "--mount", f"type=bind,source={script.resolve()},target=/script.py,readonly",
           "--entrypoint=python", image, "-I", "/script.py"]
    # Bound captured output with a small supervisor inside the container.
    wrapper = ("import subprocess; p=subprocess.Popen(['python','-I','/script.py'],"
               "stdout=subprocess.PIPE,stderr=subprocess.STDOUT); "
               "out=p.stdout.read(1048577); "
               "p.kill() if len(out)>1048576 else None; "
               "print(out[:1048576].decode(errors='replace')); "
               "raise SystemExit(1 if len(out)>1048576 else p.wait())")
    cmd[-2:] = ["-I", "-c", wrapper]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=min(timeout, 60), check=False)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False)


def ama_modules(spec: dict, workspace: Path, *, read_hook, sandbox_hook):
    root = verify_source(spec, workspace)
    namespace = "_tracegraph_ama_" + uuid.uuid4().hex
    load_package(namespace, root / "src/method/ama_agent_core")
    # The pinned utils file imports Ray for unrelated distributed worker helpers.
    # Our synchronous official build/retrieve + Docker-search path never uses those
    # helpers. Defer this one import inside this module only; do not fake any Ray
    # operation, alter algorithm source, or patch process-wide imports.
    class LazyRay:
        def __getattr__(self, name):
            return getattr(importlib.import_module("ray"), name)
    lazy_ray = LazyRay()
    def local_import(name, *args, **kwargs):
        return lazy_ray if name == "ray" else builtins.__import__(name, *args, **kwargs)
    module_spec = importlib.util.spec_from_file_location(
        namespace + ".utils", root / "src/method/ama_agent_core/utils.py")
    utils = importlib.util.module_from_spec(module_spec)
    utils.__dict__["__builtins__"] = {**vars(builtins), "__import__": local_import}
    sys.modules[module_spec.name] = utils
    module_spec.loader.exec_module(utils)
    construct = importlib.import_module(namespace + ".construct")
    retrieve = importlib.import_module(namespace + ".retrieve")
    utils = importlib.import_module(namespace + ".utils")

    # Observe upstream retrieval without supplying gold or replacing ranking logic.
    original_extract = utils._extract_chunks
    def extract(trajectory, indices):
        read_hook(trajectory, list(indices))
        return original_extract(trajectory, indices)
    utils._extract_chunks = retrieve._extract_chunks = extract

    original_search = utils._run_keyword_search
    def search(**kwargs):
        trajectory = kwargs["trajectory_data"]["trajectory"]
        read_hook(trajectory, [t["turn_idx"] for t in trajectory])
        return original_search(**kwargs)
    retrieve._run_keyword_search = search

    def run(args, **kwargs):
        script = Path(args[1]).resolve()
        if len(args) != 2 or script.name != "script.py" or not script.is_file():
            raise ValueError("unexpected upstream code execution request")
        return sandbox_hook(script, kwargs["timeout"])
    utils.subprocess = types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)
    return construct, retrieve


def source_status(config: dict, workspace: Path) -> list[str]:
    blockers = []
    for method in config["methods"]:
        key = {"acon_official": "acon", "ama_official_bm25": "ama", "ama_official_embedding": "ama"}.get(method)
        if key:
            try:
                verify_source(config["sources"][key], workspace)
            except (ValueError, OSError, KeyError):
                blockers.append(f"{method}:official_source_not_ready")
    if "acon_official" in config["methods"]:
        for package in ("jinja2", "requests"):
            if importlib.util.find_spec(package) is None:
                blockers.append(f"acon_official:dependency_missing:{package}")
    return blockers
