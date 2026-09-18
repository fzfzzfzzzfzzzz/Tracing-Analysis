#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/fangc/tracegraph-qwen3-14b-pytest5221-guarded-260917-r1
LOG=/data/fangc/logs/qwen3_14b_pytest5221_guarded_260917_r1.vllm.log
LAUNCH_RECEIPT=/data/fangc/tracegraph/runtime/qwen3_14b_pytest5221_guarded_260917_r1.argv.json
VLLM_BIN=/data/fangc/envs/vllm-0.8.5/bin

if [[ -e "$LAUNCH_RECEIPT" ]]; then
  echo "Refusing to overwrite $LAUNCH_RECEIPT" >&2
  exit 2
fi

mkdir -p /data/fangc/logs /data/fangc/tracegraph/runtime
cd "$ROOT"
export CUDA_VISIBLE_DEVICES=0,1
export PYTHONUNBUFFERED=1
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export PATH="$VLLM_BIN:$PATH"

MODEL_PATH=/data/fangc/models/Qwen3-14B \
SERVED_MODEL=Qwen3-14B-rev-40c0698 \
TOOL_CALL_PARSER=hermes \
DTYPE=bfloat16 \
QUANTIZATION=none \
LAUNCH_RECEIPT="$LAUNCH_RECEIPT" \
CONTEXT_WINDOW=32768 \
TENSOR_PARALLEL_SIZE=2 \
MAX_NUM_SEQS=4 \
exec bash scripts/serve_eval_model.sh >>"$LOG" 2>&1
