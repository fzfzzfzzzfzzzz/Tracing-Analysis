#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/fangc/tracegraph-server-eval-260917-ama-scorecard-r5
PYTHON=/data/fangc/envs/tracegraph-server-eval-260912-r8/bin/python
DATASET=/data/fangc/tracegraph/data/server_eval_v0_1_handoff_260912
CONFIG="$ROOT/configs/server_eval_qwen38_27b_unified_methods_b1024_native_260918_r6.json"
PREPARED=/data/fangc/prepared_qwen38_27b_unified_methods_b1024_native_260918_r6
OUTPUT=/data/fangc/qwen38_27b_unified_methods_b1024_native_260918_r6
LOG=/data/fangc/logs/qwen38_27b_unified_methods_b1024_native_260918_r6.log
EXPECTED_CONFIG_SHA=7c47d1ded7a4c32358c4c1f58febecd44ba023150d2e337b799c0511ff906f68

exec > >(tee -a "$LOG") 2>&1

stamp() {
  printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*"
}

cd "$ROOT"
test "$(sha256sum "$CONFIG" | awk '{print $1}')" = "$EXPECTED_CONFIG_SHA"
curl -fsS --max-time 10 http://127.0.0.1:8001/v1/models | "$PYTHON" -c '
import json, sys
models = {item["id"] for item in json.load(sys.stdin)["data"]}
assert "Qwen3.8-27B-rev-1d4bf0f" in models, models
'
stamp "Qwen3.8-27B endpoint and dual native-tool config verified"

if [[ -d "$PREPARED" ]]; then
  PYTHONPATH=src "$PYTHON" -c '
from pathlib import Path
from tracegraph.benchmark.compression_audit.build import verify_file_manifest
verify_file_manifest(Path("/data/fangc/prepared_qwen38_27b_unified_methods_b1024_native_260918_r6"))
'
else
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval prepare \
    --config "$CONFIG" \
    --dataset "$DATASET" \
    --output "$PREPARED"
fi

"$PYTHON" - <<'PY'
import json
from pathlib import Path
root = Path("/data/fangc/prepared_qwen38_27b_unified_methods_b1024_native_260918_r6")
manifest = json.loads((root / "manifest.json").read_text())
population = json.loads((root / "population.json").read_text())
assert manifest["episode_count"] == 208
assert manifest["construction_jobs"] == 24
assert len(population["calibration"]) == 8
assert len(population["main"]) == 8 and len(population["diagnostic"]) == 8
PY
stamp "208-episode repaired six-method plan verified"

if [[ -f "$OUTPUT/report.json" ]]; then
  stamp "completed output already exists"
  exit 0
fi
resume_args=()
if [[ -d "$OUTPUT" ]]; then
  resume_args+=(--resume)
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
stamp "repaired Qwen3.8-27B unified development matrix complete"
