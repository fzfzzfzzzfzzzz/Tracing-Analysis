#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/fangc/tracegraph-failure-episode-ai-260917-r1
DATASET=/data/fangc/failure_episode_ai_dev_260917_r2
PREPARED=/data/fangc/prepared_failure_episode_ai_canary_260917_r6
RUN=/data/fangc/qwen3_14b_failure_episode_ai_canary_260917_r1
SERVER_LOG=/data/fangc/logs/qwen3_14b_failure_episode_ai_canary_260917_r1.vllm.log
RUN_LOG=/data/fangc/logs/qwen3_14b_failure_episode_ai_canary_260917_r1.run.log
MODEL_RECEIPT=/data/fangc/qwen3_14b_failure_episode_ai_canary_260917_r1.models.json
LAUNCH_RECEIPT=/data/fangc/tracegraph/runtime/qwen3_14b_failure_episode_ai_canary_260917_r1.argv.json
EVAL_PYTHON=/data/fangc/envs/tracegraph-server-eval-260912-r8/bin/python
VLLM_BIN=/data/fangc/envs/vllm-0.8.5/bin

for path in "$RUN" "$MODEL_RECEIPT" "$LAUNCH_RECEIPT"; do
  if [[ -e "$path" ]]; then
    echo "Refusing to overwrite $path" >&2
    exit 2
  fi
done

mkdir -p /data/fangc/logs
cd "$ROOT"
export CUDA_VISIBLE_DEVICES=0,1
export PYTHONUNBUFFERED=1
export TRACEGRAPH_SERVER_API_KEY=local-self-hosted
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
bash scripts/serve_eval_model.sh >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

ready=0
for _ in $(seq 1 180); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "vLLM exited before becoming ready" >&2
    exit 3
  fi
  if curl --fail --silent http://127.0.0.1:8000/v1/models >"$MODEL_RECEIPT.tmp"; then
    mv "$MODEL_RECEIPT.tmp" "$MODEL_RECEIPT"
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" != 1 ]]; then
  echo "vLLM readiness timed out" >&2
  exit 4
fi

PYTHONPATH=src "$EVAL_PYTHON" -m tracegraph.benchmark.server_eval run \
  --prepared "$PREPARED" \
  --dataset "$DATASET" \
  --output "$RUN" \
  --mode live \
  --execute | tee "$RUN_LOG"

"$EVAL_PYTHON" - "$RUN/report.json" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(json.dumps({
    "episode_count": report.get("episode_count"),
    "stop_reason": report.get("stop_reason"),
    "provider_request_count": report.get("provider_request_count"),
    "judge_gate": report.get("judge_gate"),
}, ensure_ascii=False, indent=2))
PY
