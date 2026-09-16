"""Freeze explicit deployment receipts; never infer weight or hardware identities."""

import json
from pathlib import Path

from ..compression_audit.io import file_sha256, stable_digest
from ..compression_audit.development_experiment import write_json

REQUIRED = ("served_model", "weights_revision", "server_version", "dtype", "quantization",
            "tool_call_parser", "launch_argv", "hardware", "weight_files", "tokenizer_files")


def validate_receipt(receipt):
    if any(k not in receipt or receipt[k] is None for k in REQUIRED):
        raise ValueError("runtime receipt incomplete; use explicit 'none' for no quantization")
    if not isinstance(receipt["launch_argv"], list) or not receipt["launch_argv"]:
        raise ValueError("freeze actual launch argv as an array")
    if not receipt["weight_files"] or not receipt["tokenizer_files"]:
        raise ValueError("freeze weight and tokenizer file hashes")
    for table in (receipt["weight_files"], receipt["tokenizer_files"]):
        if any(len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in table.values()):
            raise ValueError("invalid file SHA256")
    if any("api-key" in str(a).lower() or "api_key" in str(a).lower() for a in receipt["launch_argv"]):
        raise ValueError("launch receipt must not contain credentials; configure via environment")


def record_runtime(spec: Path, weights: Path, tokenizer: Path, output: Path):
    if output.exists():
        raise ValueError("runtime receipt output must be new")
    value = json.loads(spec.read_text(encoding="utf-8"))
    value["weight_files"] = {p.relative_to(weights).as_posix(): file_sha256(p)
                             for p in weights.rglob("*") if p.is_file()}
    value["tokenizer_files"] = {p.relative_to(tokenizer).as_posix(): file_sha256(p)
                                for p in tokenizer.rglob("*") if p.is_file()}
    validate_receipt(value)
    value.update(development_only=True, independent_validation=False)
    value["receipt_hash"] = stable_digest(value)
    write_json(output, value)
    return {"receipt": str(output), "sha256": file_sha256(output), "provider_requests": 0}


def verify_runtime(model: dict, workspace: Path):
    spec = model.get("runtime_receipt")
    if not spec:
        raise ValueError("missing runtime receipt")
    path = workspace / spec["path"]
    if file_sha256(path) != spec["sha256"]:
        raise ValueError("runtime receipt file changed")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    claimed = receipt.pop("receipt_hash")
    if stable_digest(receipt) != claimed:
        raise ValueError("runtime receipt content changed")
    validate_receipt(receipt)
    for key in ("served_model", "weights_revision", "server_version"):
        if receipt[key] != model[key]:
            raise ValueError("deployment identity differs: " + key)
    if receipt["tokenizer_files"] != model["tokenizer"]["files"]:
        raise ValueError("deployment tokenizer differs")
    return receipt
