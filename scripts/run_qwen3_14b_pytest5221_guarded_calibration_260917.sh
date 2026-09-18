#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/fangc/tracegraph-qwen3-14b-pytest5221-guarded-260917-r1
DATASET=/data/fangc/tracegraph/data/server_eval_v0_1_handoff_260912
CONFIG="$ROOT/configs/server_eval_qwen3_14b_pytest5221_thinking_b16384_s40_260915_r4.json"
PREPARED=/data/fangc/prepared_qwen3_14b_pytest5221_guarded_calibration_260917_r1
RUN=/data/fangc/qwen3_14b_pytest5221_guarded_calibration_260917_r1
RUN_LOG=/data/fangc/logs/qwen3_14b_pytest5221_guarded_calibration_260917_r1.run.log
PYTHON=/data/fangc/envs/tracegraph-server-eval-260912-r8/bin/python

cd "$ROOT"
export PYTHONUNBUFFERED=1
export TRACEGRAPH_SERVER_API_KEY=local-self-hosted

if [[ ! -e "$PREPARED" ]]; then
  PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval prepare \
    --config "$CONFIG" \
    --dataset "$DATASET" \
    --output "$PREPARED"
fi
if [[ -e "$RUN/report.json" ]]; then
  echo "Refusing to overwrite completed calibration $RUN" >&2
  exit 2
fi

resume=()
if [[ -d "$RUN" ]]; then
  resume+=(--resume)
fi
PYTHONPATH=src "$PYTHON" -m tracegraph.benchmark.server_eval run \
  --prepared "$PREPARED" \
  --dataset "$DATASET" \
  --output "$RUN" \
  --mode live \
  --execute \
  "${resume[@]}" | tee "$RUN_LOG"

"$PYTHON" - "$RUN/report.json" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
cell = next(row for row in report["cells"] if row["cell_id"] == "qwen3_14b-b16384")
assert report["judge_gate"]["pass"] and cell["calibration"]["pass"]
print(json.dumps({
    "completed_episodes": report["completed_episodes"],
    "provider_requests": report["provider_requests"],
    "judge_gate": report["judge_gate"],
    "calibration": cell["calibration"],
}, ensure_ascii=False, indent=2))
PY
