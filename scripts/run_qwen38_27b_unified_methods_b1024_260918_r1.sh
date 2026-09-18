#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/fangc/tracegraph-server-eval-260917-ama-scorecard-r5
PYTHON=/data/fangc/envs/tracegraph-server-eval-260912-r8/bin/python
DATASET=/data/fangc/tracegraph/data/server_eval_v0_1_handoff_260912
CONFIG="$ROOT/configs/server_eval_qwen38_27b_unified_methods_b1024_260918_r1.json"
PREPARED=/data/fangc/prepared_qwen38_27b_unified_methods_b1024_260918_r1
OUTPUT=/data/fangc/qwen38_27b_unified_methods_b1024_260918_r1
REFERENCE_CONFIG="$ROOT/configs/server_eval_qwen3_14b_unified_methods_b1024_260917_r1.json"
REFERENCE_OUTPUT=/data/fangc/qwen3_14b_unified_methods_b1024_260917_r1
EXPECTED_REFERENCE_CONFIG_SHA=480c3d495c2b0704a5227fe9dd8af33519f635cb360b29ff554f0d1eee2311b1
EXPECTED_CONFIG_SHA=2aaf5792131a72e340567ebc7c904b8b6e58842b0d33d339a3a6a3e238ff8c27
SERVED_MODEL=Qwen3.8-27B-rev-1d4bf0f
LOG=/data/fangc/logs/qwen38_27b_unified_methods_b1024_260918_r1.log

exec > >(tee -a "$LOG") 2>&1

stamp() {
  printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*"
}

cd "$ROOT"
test "$(sha256sum "$REFERENCE_CONFIG" | awk '{print $1}')" = "$EXPECTED_REFERENCE_CONFIG_SHA"
test "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$EXPECTED_CONFIG_SHA"
test -f "$REFERENCE_OUTPUT/report.json"
test -f /home/fangc/tracegraph-qwen38-attribution-260915/artifacts/runtime/qwen38_27b_bf16_tp4_260916_r4.receipt.json
test -d /home/fangc/tracegraph-qwen38-attribution-260915/artifacts/tokenizers/Qwen3.8-27B-tokenizer-1d4bf0f
curl -fsS --max-time 10 http://127.0.0.1:8001/v1/models | "$PYTHON" -c '
import json, sys
models = {item["id"] for item in json.load(sys.stdin)["data"]}
if "Qwen3.8-27B-rev-1d4bf0f" not in models:
    raise SystemExit(f"unexpected served models: {sorted(models)}")
'
stamp "frozen Qwen3.8-27B endpoint and 14B reference verified"

if [[ -d "$PREPARED" ]]; then
  PYTHONPATH=src "$PYTHON" -c '
from pathlib import Path
from tracegraph.benchmark.compression_audit.build import verify_file_manifest
verify_file_manifest(Path("/data/fangc/prepared_qwen38_27b_unified_methods_b1024_260918_r1"))
'
  stamp "existing prepared plan verified"
else
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval prepare \
    --config "$CONFIG" \
    --dataset "$DATASET" \
    --output "$PREPARED"
  stamp "prepared plan created"
fi

"$PYTHON" - <<'PY'
import json
from pathlib import Path

root = Path("/data/fangc/prepared_qwen38_27b_unified_methods_b1024_260918_r1")
manifest = json.loads((root / "manifest.json").read_text())
population = json.loads((root / "population.json").read_text())
assert manifest["episode_count"] == 208, manifest["episode_count"]
assert manifest["construction_jobs"] == 24, manifest["construction_jobs"]
assert manifest["cell_count"] == 1, manifest["cell_count"]
assert len(population["main"]) == 8, len(population["main"])
assert len(population["calibration"]) == 8, len(population["calibration"])
assert len(population["diagnostic"]) == 8, len(population["diagnostic"])
PY
stamp "208-episode six-method plan verified"

if [[ -f "$OUTPUT/report.json" ]]; then
  stamp "completed output already exists; nothing to resume"
  exit 0
fi

resume_args=()
if [[ -d "$OUTPUT" ]]; then
  resume_args+=(--resume)
  stamp "resuming incomplete output"
fi

export TRACEGRAPH_SERVER_API_KEY=local-self-hosted
PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval run \
  --prepared "$PREPARED" \
  --dataset "$DATASET" \
  --output "$OUTPUT" \
  --mode live \
  --execute \
  --assume-gates-passed \
  "${resume_args[@]}"

test -f "$OUTPUT/report.json"
stamp "Qwen3.8-27B unified development matrix complete"
