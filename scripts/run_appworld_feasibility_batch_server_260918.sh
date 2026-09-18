#!/usr/bin/env bash
set -euo pipefail

ENV_ROOT=/data/fangc/envs/acon-appworld-d63f9ae18959
RUNNER=/home/fangc/appworld_feasibility_260918/run_qwen38_appworld_feasibility_server_260918.py
FREEZE=/data/fangc/external_benchmark_freezes/feasibility_260918/appworld/development_feasibility_ids.jsonl
LOG_ROOT=/data/fangc/logs/appworld_feasibility_260918
EXPECTED_FREEZE_SHA=a2cd54fb447494386ef683a3c72ecc38ef216b23bfaffa6db007555f518a9c61

export PATH="${ENV_ROOT}/bin:${PATH}"
mkdir -p "${LOG_ROOT}"
test "$(sha256sum "${FREEZE}" | awk '{print $1}')" = "${EXPECTED_FREEZE_SHA}"
mapfile -t TASK_IDS < <("${ENV_ROOT}/bin/python" - "${FREEZE}" <<'PY'
import json
import pathlib
import sys

rows = [json.loads(line) for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if line.strip()]
ids = [str(row["task_id"]) for row in rows]
assert len(ids) == 20 and len(set(ids)) == 20
print("\n".join(ids))
PY
)

run_one() {
  local method="$1"
  local label="$2"
  local task_id="$3"
  local output_dir="/data/fangc/outputs/appworld_feasibility_260918/${label}/task_${task_id}"
  if [[ -f "${output_dir}/run_receipt.json" ]]; then
    printf 'skip completed %s %s\n' "${method}" "${task_id}"
    return 0
  fi
  if [[ -d "${output_dir}" ]]; then
    printf 'refusing partial output %s\n' "${output_dir}" >&2
    return 2
  fi
  "${ENV_ROOT}/bin/python" "${RUNNER}" \
    --task-id "${task_id}" \
    --method "${method}" \
    --budget 8192 \
    --run-label "${label}" \
    2>&1 | tee "${LOG_ROOT}/${label}_${task_id}.log"
}

for task_id in "${TASK_IDS[@]}"; do
  run_one full_history full_history_feasibility20_qwen38_r1 "${task_id}"
  run_one tracegraph tracegraph_lifecycle_b8192_feasibility20_qwen38_r1 "${task_id}"
done
