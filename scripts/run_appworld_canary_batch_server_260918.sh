#!/usr/bin/env bash
set -euo pipefail

ENV_ROOT="/data/fangc/envs/acon-appworld-d63f9ae18959"
RUNNER="/home/fangc/run_qwen38_appworld_canary_server_260918.py"
RUN_LABEL="full_history_qwen38_v3"
LOG_ROOT="/data/fangc/logs/appworld_external_canary_260918"

export PATH="${ENV_ROOT}/bin:${PATH}"
mkdir -p "${LOG_ROOT}"

for task_id in 6c2c621_1 3ab5b8b_2 383cbac_1 50e1ac9_1; do
  "${ENV_ROOT}/bin/python" "${RUNNER}" \
    --task-id "${task_id}" \
    --run-label "${RUN_LABEL}" \
    2>&1 | tee "${LOG_ROOT}/${RUN_LABEL}_${task_id}.log"
done

