#!/usr/bin/env bash
set -euo pipefail

ACON_URL="https://github.com/microsoft/acon.git"
ACON_COMMIT="d63f9ae18959dc7215ff62899c94c5e8c56847ae"
ACON_ROOT="/home/fangc/acon-d63f9ae18959"
APPWORLD_ROOT="/home/fangc/appworld-42b5bcf3cd33"
APPWORLD_DATA_ROOT="/data/fangc/appworld-42b5bcf3cd33"
APPWORLD_DATA_DIR="${APPWORLD_DATA_ROOT}/data"
ENV_ROOT="/data/fangc/envs/acon-appworld-d63f9ae18959"
PYTHON_BIN="/data/fangc/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11"

if [[ ! -d "${ACON_ROOT}/.git" ]]; then
  git clone "${ACON_URL}" "${ACON_ROOT}"
fi

git -C "${ACON_ROOT}" fetch --quiet origin "${ACON_COMMIT}"
git -C "${ACON_ROOT}" checkout --detach "${ACON_COMMIT}"
test "$(git -C "${ACON_ROOT}" rev-parse HEAD)" = "${ACON_COMMIT}"

if [[ ! -x "${ENV_ROOT}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${ENV_ROOT}"
fi

"${ENV_ROOT}/bin/python" -m pip install --upgrade pip
"${ENV_ROOT}/bin/python" -m pip install -e "${APPWORLD_ROOT}"
"${ENV_ROOT}/bin/python" -m pip install -e "${ACON_ROOT}"

DATA_LINK="${ACON_ROOT}/experiments/appworld/data"
if [[ -L "${DATA_LINK}" ]]; then
  CURRENT_TARGET="$(readlink -f "${DATA_LINK}")"
  EXPECTED_TARGET="$(readlink -f "${APPWORLD_DATA_DIR}")"
  if [[ "${CURRENT_TARGET}" = "${APPWORLD_DATA_ROOT}" ]]; then
    ln -sfn "${APPWORLD_DATA_DIR}" "${DATA_LINK}"
  else
    test "${CURRENT_TARGET}" = "${EXPECTED_TARGET}"
  fi
elif [[ -e "${DATA_LINK}" ]]; then
  echo "Refusing to replace existing non-symlink: ${DATA_LINK}" >&2
  exit 1
else
  ln -s "${APPWORLD_DATA_DIR}" "${DATA_LINK}"
fi

EXPERIMENTS_LINK="${ACON_ROOT}/experiments/appworld/experiments"
APPWORLD_EXPERIMENTS_DIR="${APPWORLD_DATA_ROOT}/experiments"
if [[ -L "${EXPERIMENTS_LINK}" ]]; then
  test "$(readlink -f "${EXPERIMENTS_LINK}")" = "$(readlink -f "${APPWORLD_EXPERIMENTS_DIR}")"
elif [[ -e "${EXPERIMENTS_LINK}" ]]; then
  echo "Refusing to replace existing non-symlink: ${EXPERIMENTS_LINK}" >&2
  exit 1
else
  ln -s "${APPWORLD_EXPERIMENTS_DIR}" "${EXPERIMENTS_LINK}"
fi

export PATH="${ENV_ROOT}/bin:${PATH}"
cd "${ACON_ROOT}/experiments/appworld"

"${ENV_ROOT}/bin/python" - <<'PY'
from appworld import AppWorld, load_task_ids
from productive_agents.agents.utils import LLMManager

ids = load_task_ids("dev")
assert len(ids) == 57, len(ids)
assert "23cf851_1" in ids
print({"status": "ready", "dev_tasks": len(ids), "canary_present": True})
PY
