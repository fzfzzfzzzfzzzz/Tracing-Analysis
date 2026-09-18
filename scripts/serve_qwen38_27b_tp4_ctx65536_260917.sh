#!/usr/bin/env bash
set -euo pipefail

GPUSTAT=/data/fangc/tools/uv-tools/gpustat/bin/gpustat
PYTHON=/data/fangc/envs/sglang-qwen38-260915/bin/python
MODEL=/data/fangc/models/Qwen3.8-27B
SERVED_MODEL=Qwen3.8-27B-rev-1d4bf0f

"$GPUSTAT" --json | "$PYTHON" -c '
import json, sys
state = json.load(sys.stdin)
selected = [gpu for gpu in state["gpus"] if gpu["index"] in {0, 1, 2, 3}]
if len(selected) != 4:
    raise SystemExit("expected GPU 0-3")
busy = []
for gpu in selected:
    foreign = [p for p in gpu["processes"] if p.get("username") != "gdm"]
    if gpu["memory.used"] > 100 or gpu["utilization.gpu"] > 5 or foreign:
        busy.append({"index": gpu["index"], "memory": gpu["memory.used"],
                     "utilization": gpu["utilization.gpu"], "processes": foreign})
if busy:
    raise SystemExit(f"refusing busy GPUs: {busy}")
'

if ss -ltn | grep -qE '127\.0\.0\.1:8000|0\.0\.0\.0:8000|\[::\]:8000'; then
  echo "refusing occupied port 8000" >&2
  exit 1
fi
test -f "$MODEL/model.safetensors.index.json"

export CUDA_VISIBLE_DEVICES=0,1,2,3
export CUDA_HOME=/data/fangc/tools/cuda-12.8.93
export CUDA_PATH=$CUDA_HOME
export CUDACXX=$CUDA_HOME/bin/nvcc
export CUDAHOSTCXX=$CUDA_HOME/bin/x86_64-conda-linux-gnu-g++
export NVCC_CCBIN=$CUDAHOSTCXX
export PATH=$CUDA_HOME/bin:/data/fangc/envs/sglang-qwen38-260915/bin:$PATH
export LD_LIBRARY_PATH=$CUDA_HOME/targets/x86_64-linux/lib:${LD_LIBRARY_PATH:-}
export TVM_FFI_CACHE_DIR=/data/fangc/cache/tvm-ffi/qwen38_27b_cuda128_r4_server
export HF_HOME=/data/fangc/.cache/huggingface
export TORCHINDUCTOR_CACHE_DIR=/data/fangc/tmp/torchinductor_qwen38_27b_tp4
export TRITON_CACHE_DIR=/data/fangc/cache/triton/qwen38_27b_tp4

exec "$PYTHON" -m sglang.launch_server \
  --model-path "$MODEL" \
  --served-model-name "$SERVED_MODEL" \
  --host 127.0.0.1 \
  --port 8000 \
  --tp-size 4 \
  --dtype bfloat16 \
  --mem-fraction-static 0.90 \
  --context-length 65536 \
  --max-running-requests 1 \
  --chunked-prefill-size 2048 \
  --attention-backend triton \
  --linear-attn-backend triton \
  --sampling-backend pytorch \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --cuda-graph-max-bs 1 \
  --random-seed 20260972
