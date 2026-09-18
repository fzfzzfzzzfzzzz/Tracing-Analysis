#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/fangc/tracegraph-qwen38-attribution-260915
DATASET=/data/fangc/tracegraph/data/server_eval_v0_1_handoff_260912
MODEL=/data/fangc/models/Qwen3.8-27B
PYTHON=/data/fangc/envs/sglang-qwen38-260915/bin/python
GPUSTAT=/data/fangc/tools/uv-tools/gpustat/bin/gpustat
REVISION=1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
SERVED_MODEL=Qwen3.8-27B-rev-1d4bf0f
TOKENIZER="$ROOT/artifacts/tokenizers/Qwen3.8-27B-tokenizer-1d4bf0f"
RECEIPT="$ROOT/artifacts/runtime/qwen38_27b_bf16_tp4_260916_r4.receipt.json"
SOURCE_CONFIG="$ROOT/configs/server_eval_qwen3_14b_pytest5221_thinking_b16384_s40_260915_r4.json"
CONFIG="$ROOT/configs/server_eval_qwen38_27b_pytest5221_tp4_260916_r5.json"
CAL_PREP=/data/fangc/prepared_qwen38_27b_pytest5221_calibration_260916_r5
CAL_RUN=/data/fangc/qwen38_27b_pytest5221_calibration_260916_r5
MINI_PREP=/data/fangc/prepared_qwen38_27b_pytest5221_smoke_260916_r5
MINI_RUN=/data/fangc/qwen38_27b_pytest5221_smoke_260916_r5
TASKS="$ROOT/data/real_canary/pytest5221_thinking_attribution_260915_r4/tasks.jsonl"
TASK_ID=realcanary:swebench_lite:pytest-dev__pytest-5221
SERVER_LOG=/data/fangc/logs/qwen38_27b_sglang_tp4_260916_r5.log
PROBE="$ROOT/artifacts/runtime/qwen38_27b_agent_probe_260916_r5.json"
SERVER_PID=""

# These eight prefixes formed the now-inspected r4 calibration population.
R4_EXPOSED=(
  controlled:shell_syntax:data:R0:long
  controlled:parameter_schema:operations:R0:short
  controlled:parameter_schema:operations:R1:short
  controlled:shell_syntax:software:R1:long
  controlled:parameter_schema:data:R2:short
  controlled:shell_syntax:software:R2:long
  controlled:parameter_schema:software:R3:short
  controlled:shell_syntax:data:R3:long
)

# Selected without reading gold: SHA-256 order of public IDs under seed 20260916,
# taking two remaining development prefixes per recoverability level.
R5_CALIBRATION=(
  controlled:shell_syntax:operations:R0:long
  controlled:shell_syntax:software:R0:short
  controlled:parameter_schema:data:R1:short
  controlled:parameter_schema:operations:R1:long
  controlled:parameter_schema:operations:R2:short
  controlled:parameter_schema:software:R2:long
  controlled:shell_syntax:software:R3:short
  controlled:parameter_schema:software:R3:long
)

stamp() {
  printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*"
}

wait_for_gpus() {
  while ! "$GPUSTAT" --json | "$PYTHON" -c '
import json, sys
state = json.load(sys.stdin)
selected = [gpu for gpu in state["gpus"] if gpu["index"] in {0, 1, 2, 3}]
free = len(selected) == 4 and all(
    gpu["memory.used"] <= 100
    and gpu["utilization.gpu"] <= 5
    and not [p for p in gpu["processes"] if p.get("username") != "gdm"]
    for gpu in selected
)
raise SystemExit(0 if free else 1)
'; do
    stamp "waiting for GPU 0-3 to become free"
    sleep 60
  done
  stamp "GPU 0-3 are free"
}

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    stamp "stopping Qwen3.8 server process group $SERVER_PID"
    kill -TERM -- "-$SERVER_PID" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "$SERVER_PID" 2>/dev/null || break
      sleep 2
    done
    kill -KILL -- "-$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

test -f "$MODEL/model.safetensors.index.json"
[[ "$(find "$MODEL" -maxdepth 1 -name 'model-*.safetensors' | wc -l)" -eq 18 ]]
"$PYTHON" -c 'import importlib.metadata as m; assert m.version("sglang") == "0.5.10"; assert m.version("torch") == "2.9.1"; assert m.version("transformers") == "5.3.0"'
test -d "$TOKENIZER"
test -f "$RECEIPT"
stamp "Qwen3.8 weights, tokenizer, runtime receipt, and environment verified"

cd "$ROOT"
if [[ -e "$CONFIG" ]]; then
  stamp "existing r5 model config retained"
else
  derive_args=(
    --source "$SOURCE_CONFIG"
    --tokenizer-dir "$TOKENIZER"
    --runtime-receipt "$RECEIPT"
    --output "$CONFIG"
    --weights-revision "$REVISION"
    --served-model "$SERVED_MODEL"
    --server-version sglang-0.5.10
    --seed 20260916
  )
  for prefix_id in "${R4_EXPOSED[@]}"; do
    derive_args+=(--calibration-exclude-prefix-id "$prefix_id")
  done
  for prefix_id in "${R5_CALIBRATION[@]}"; do
    derive_args+=(--calibration-prefix-id "$prefix_id")
  done
  "$PYTHON" scripts/derive_qwen38_attribution_config.py "${derive_args[@]}"
fi

if [[ -e "$CAL_PREP" ]]; then
  PYTHONPATH=src "$PYTHON" -c 'from pathlib import Path; from tracegraph.benchmark.compression_audit.build import verify_file_manifest; verify_file_manifest(Path("/data/fangc/prepared_qwen38_27b_pytest5221_calibration_260916_r5"))'
  stamp "existing r5 calibration plan verified"
else
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval prepare \
    --config "$CONFIG" \
    --dataset "$DATASET" \
    --output "$CAL_PREP"
fi
"$PYTHON" - <<'PY'
import json
from pathlib import Path

root = Path("/data/fangc/prepared_qwen38_27b_pytest5221_calibration_260916_r5")
expected = [
    "controlled:shell_syntax:operations:R0:long",
    "controlled:shell_syntax:software:R0:short",
    "controlled:parameter_schema:data:R1:short",
    "controlled:parameter_schema:operations:R1:long",
    "controlled:parameter_schema:operations:R2:short",
    "controlled:parameter_schema:software:R2:long",
    "controlled:shell_syntax:software:R3:short",
    "controlled:parameter_schema:software:R3:long",
]
population = json.loads((root / "population.json").read_text())
manifest = json.loads((root / "manifest.json").read_text())
assert population == {"calibration": expected, "diagnostic": [], "main": []}
assert manifest["calibration_exclusion_count"] == 33
assert manifest["episode_count"] == 40
PY

if [[ -e "$MINI_PREP" ]]; then
  stamp "existing r5 mini plan retained"
else
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval mini-prepare \
    --config "$CONFIG" \
    --tasks "$TASKS" \
    --output "$MINI_PREP"
fi
stamp "fresh r5 calibration and unchanged pytest-5221 plans frozen"

wait_for_gpus
setsid "$ROOT/scripts/serve_qwen38_27b_tp4_260915.sh" > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!
stamp "server launched as process group $SERVER_PID"
for _ in $(seq 1 120); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "Qwen3.8 server exited during startup" >&2
    exit 1
  fi
  sleep 15
done
curl -fsS --max-time 10 http://127.0.0.1:8000/v1/models >/dev/null
stamp "server ready"

"$PYTHON" scripts/probe_qwen38_agent_endpoint.py \
  --model "$SERVED_MODEL" \
  --output "$PROBE"
stamp "thinking and native-tool probe passed"

if [[ -e "$CAL_RUN/report.json" ]]; then
  stamp "existing completed r5 calibration retained"
else
  resume_args=()
  if [[ -d "$CAL_RUN" ]]; then
    resume_args+=(--resume)
    stamp "resuming incomplete r5 calibration"
  fi
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval run \
    --prepared "$CAL_PREP" \
    --dataset "$DATASET" \
    --output "$CAL_RUN" \
    --mode live \
    --execute \
    "${resume_args[@]}"
fi
"$PYTHON" -c 'import json, pathlib; r=json.loads((pathlib.Path("/data/fangc/qwen38_27b_pytest5221_calibration_260916_r5")/"report.json").read_text()); c=next(x for x in r["cells"] if x["cell_id"] == "qwen38_27b-b16384"); assert r["judge_gate"]["pass"] and c["calibration"]["pass"]'
stamp "fresh Qwen3.8 calibration gates passed"

if [[ -e "$MINI_RUN" ]]; then
  echo "refusing to overwrite existing r5 pytest-5221 output: $MINI_RUN" >&2
  exit 1
fi
PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval mini-run \
  --prepared "$MINI_PREP" \
  --output "$MINI_RUN" \
  --task-id "$TASK_ID" \
  --model-id qwen38_27b \
  --method full_history \
  --budget 16384 \
  --calibration "$CAL_RUN" \
  --execute
stamp "pytest-5221 Qwen3.8 ability-attribution run complete"
