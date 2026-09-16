#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/fangc/tracegraph-qwen38-attribution-260915
DATASET=/data/fangc/tracegraph/data/server_eval_v0_1_handoff_260912
MODEL=/data/fangc/models/Qwen3.8-27B
PYTHON=/data/fangc/envs/sglang-qwen38-260915/bin/python
UV=/data/fangc/tools/uv-0.12.13/uv
GPUSTAT=/data/fangc/tools/uv-tools/gpustat/bin/gpustat
DOWNLOAD_PID=915251
INSTALL_PID=914542
REVISION=1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
SERVED_MODEL=Qwen3.8-27B-rev-1d4bf0f
TOKENIZER="$ROOT/artifacts/tokenizers/Qwen3.8-27B-tokenizer-1d4bf0f"
RECEIPT="$ROOT/artifacts/runtime/qwen38_27b_bf16_tp4_260916_r4.receipt.json"
CONFIG="$ROOT/configs/server_eval_qwen38_27b_pytest5221_tp4_260916_r4.json"
CAL_PREP=/data/fangc/prepared_qwen38_27b_pytest5221_calibration_260916_r3
CAL_RUN=/data/fangc/qwen38_27b_pytest5221_calibration_260916_r3
MINI_PREP=/data/fangc/prepared_qwen38_27b_pytest5221_smoke_260916_r3
MINI_RUN=/data/fangc/qwen38_27b_pytest5221_smoke_260916_r3
TASKS="$ROOT/data/real_canary/pytest5221_thinking_attribution_260915_r4/tasks.jsonl"
TASK_ID=realcanary:swebench_lite:pytest-dev__pytest-5221
SERVER_LOG=/data/fangc/logs/qwen38_27b_sglang_tp4_260916_r3.log
PROBE="$ROOT/artifacts/runtime/qwen38_27b_agent_probe_260916_r3.json"
SERVER_PID=""

stamp() {
  printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*"
}

wait_for_pid() {
  local pid="$1"
  local label="$2"
  while kill -0 "$pid" 2>/dev/null; do
    stamp "waiting for $label (pid=$pid)"
    sleep 30
  done
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

wait_for_pid "$DOWNLOAD_PID" "Qwen3.8 weights"
test -f "$MODEL/model.safetensors.index.json"
[[ "$(find "$MODEL" -maxdepth 1 -name 'model-*.safetensors' | wc -l)" -eq 18 ]]
if find "$MODEL/.cache" -name '*.incomplete' -print -quit 2>/dev/null | grep -q .; then
  echo "weight download left incomplete files" >&2
  exit 1
fi
stamp "weight download complete"

wait_for_pid "$INSTALL_PID" "SGLang environment"
"$PYTHON" -c 'import importlib.metadata as m; assert m.version("sglang") == "0.5.10"; assert m.version("torch") == "2.9.1"; assert m.version("transformers") == "5.3.0"'
stamp "SGLang environment complete"

env UV_CACHE_DIR=/data/fangc/cache/uv "$UV" pip install \
  --python "$PYTHON" -e "$ROOT[server-eval]"
stamp "TraceGraph installed into isolated environment"

shopt -s nullglob
tokenizer_files=(
  "$MODEL"/tokenizer.json
  "$MODEL"/tokenizer_config.json
  "$MODEL"/vocab.json
  "$MODEL"/merges.txt
  "$MODEL"/chat_template*.json
)
if [[ "${#tokenizer_files[@]}" -lt 2 ]]; then
  echo "incomplete tokenizer files in model snapshot" >&2
  exit 1
fi
if [[ -e "$TOKENIZER" ]]; then
  for source in "${tokenizer_files[@]}"; do
    cmp --silent "$source" "$TOKENIZER/$(basename "$source")"
  done
  stamp "existing tokenizer snapshot verified"
else
  mkdir -p "$TOKENIZER"
  cp -a "${tokenizer_files[@]}" "$TOKENIZER/"
  stamp "tokenizer snapshot created"
fi

cd "$ROOT"
if [[ -e "$RECEIPT" ]]; then
  stamp "existing r4 runtime receipt retained"
else
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval record-runtime \
    --spec artifacts/runtime/qwen38_27b_bf16_tp4_260916_r4.spec.json \
    --weights "$MODEL" \
    --tokenizer "$TOKENIZER" \
    --output "$RECEIPT"
fi

if [[ -e "$CONFIG" ]]; then
  stamp "existing r4 model config retained"
else
  "$PYTHON" scripts/derive_qwen38_attribution_config.py \
    --source configs/server_eval_qwen3_14b_pytest5221_thinking_b16384_s40_260915_r4.json \
    --tokenizer-dir "$TOKENIZER" \
    --runtime-receipt "$RECEIPT" \
    --output "$CONFIG" \
    --weights-revision "$REVISION" \
    --served-model "$SERVED_MODEL" \
    --server-version sglang-0.5.10 \
    --seed 20260972
fi

if [[ -e "$CAL_PREP" ]]; then
  PYTHONPATH=src "$PYTHON" -c 'from pathlib import Path; from tracegraph.benchmark.compression_audit.build import verify_file_manifest; verify_file_manifest(Path("/data/fangc/prepared_qwen38_27b_pytest5221_calibration_260916_r3"))'
  stamp "existing calibration plan verified"
else
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval prepare \
    --config "$CONFIG" \
    --dataset "$DATASET" \
    --output "$CAL_PREP"
fi
if [[ -e "$MINI_PREP" ]]; then
  stamp "existing mini plan retained"
else
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval mini-prepare \
    --config "$CONFIG" \
    --tasks "$TASKS" \
    --output "$MINI_PREP"
fi
stamp "calibration and mini plans frozen"

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

PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval run \
  --prepared "$CAL_PREP" \
  --dataset "$DATASET" \
  --output "$CAL_RUN" \
  --mode live \
  --execute
"$PYTHON" -c 'import json, pathlib; r=json.loads((pathlib.Path("/data/fangc/qwen38_27b_pytest5221_calibration_260916_r3")/"report.json").read_text()); c=next(x for x in r["cells"] if x["cell_id"] == "qwen38_27b-b16384"); assert r["judge_gate"]["pass"] and c["calibration"]["pass"]'
stamp "Qwen3.8 calibration gates passed"

PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval mini-run \
  --prepared "$MINI_PREP" \
  --output "$MINI_RUN" \
  --task-id "$TASK_ID" \
  --model-id qwen38_27b \
  --method full_history \
  --budget 16384 \
  --calibration "$CAL_RUN" \
  --execute
stamp "pytest-5221 Qwen3.8 smoke run complete"
