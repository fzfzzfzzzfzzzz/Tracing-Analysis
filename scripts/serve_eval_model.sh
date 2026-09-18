#!/usr/bin/env bash
# Run manually on the Linux server after choosing and installing a vLLM version.
set -euo pipefail
: "${MODEL_PATH:?Set the absolute local weight directory}"
: "${SERVED_MODEL:?Set the served model name used by bind-model}"
: "${TOOL_CALL_PARSER:?Choose the parser supported by these exact weights and vLLM version}"
: "${DTYPE:?Set the exact dtype; do not leave it implicit}"
: "${QUANTIZATION:?Set the quantization method or none}"
: "${LAUNCH_RECEIPT:?Set a new launch argv JSON path}"
args=(vllm serve "$MODEL_PATH" \
  --served-model-name "$SERVED_MODEL" \
  --host "${BIND_HOST:-127.0.0.1}" --port "${PORT:-8000}" \
  --max-model-len "${CONTEXT_WINDOW:-65536}" \
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE:-1}" \
  --max-num-seqs "${MAX_NUM_SEQS:-4}" \
  --generation-config vllm --dtype "$DTYPE" \
  --enable-auto-tool-choice --tool-call-parser "$TOOL_CALL_PARSER")
if [[ "$QUANTIZATION" != "none" ]]; then
  args+=(--quantization "$QUANTIZATION")
fi
python -c 'import json,sys; f=open(sys.argv[1],"x"); json.dump(sys.argv[2:],f,indent=2); f.close()' \
  "$LAUNCH_RECEIPT" "${args[@]}"
exec "${args[@]}"
