#!/usr/bin/env bash
set -euo pipefail

APPWORLD_COMMIT="${APPWORLD_COMMIT:-42b5bcf3cd334fee33f0c37c02070a9f5807add5}"
CODE_ROOT="${CODE_ROOT:-/home/fangc/appworld-${APPWORLD_COMMIT:0:12}}"
DATA_ROOT="${DATA_ROOT:-/data/fangc/appworld-${APPWORLD_COMMIT:0:12}}"
ENV_ROOT="${ENV_ROOT:-/data/fangc/envs/appworld-${APPWORLD_COMMIT:0:12}}"
PYTHON_BOOTSTRAP="${PYTHON_BOOTSTRAP:-/data/fangc/envs/tracegraph-server-eval-260912-r8/bin/python}"
export PATH="${ENV_ROOT}/bin:${PATH}"

if [[ ! -x "${PYTHON_BOOTSTRAP}" ]]; then
  echo "Python 3.11 bootstrap is missing: ${PYTHON_BOOTSTRAP}" >&2
  exit 2
fi

mkdir -p "${DATA_ROOT}" "$(dirname "${ENV_ROOT}")"

if [[ ! -d "${CODE_ROOT}/.git" ]]; then
  if [[ -e "${CODE_ROOT}" ]]; then
    echo "Refusing to replace non-git path: ${CODE_ROOT}" >&2
    exit 3
  fi
  GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/StonyBrookNLP/appworld.git "${CODE_ROOT}"
fi

git -C "${CODE_ROOT}" fetch --tags origin
git -C "${CODE_ROOT}" checkout --detach "${APPWORLD_COMMIT}"
git -C "${CODE_ROOT}" lfs pull

if [[ ! -x "${ENV_ROOT}/bin/python" ]]; then
  "${PYTHON_BOOTSTRAP}" -m venv "${ENV_ROOT}"
fi

"${ENV_ROOT}/bin/python" -m pip install --upgrade pip
"${ENV_ROOT}/bin/python" -m pip install -e "${CODE_ROOT}"

(
  cd "${CODE_ROOT}"
  "${ENV_ROOT}/bin/appworld" install --repo
)

"${ENV_ROOT}/bin/appworld" download data --root "${DATA_ROOT}"
# AppWorld's verifier expects one logical root containing both downloaded data
# and repository tests.  Keep the physical placement compliant with the server
# rules (code under /home, data under /data) and expose tests through a symlink.
if [[ -L "${DATA_ROOT}/tests" ]]; then
  if [[ "$(readlink -f "${DATA_ROOT}/tests")" != "$(readlink -f "${CODE_ROOT}/tests")" ]]; then
    echo "Refusing to replace unexpected tests symlink: ${DATA_ROOT}/tests" >&2
    exit 4
  fi
elif [[ -e "${DATA_ROOT}/tests" ]]; then
  echo "Refusing to replace existing tests path: ${DATA_ROOT}/tests" >&2
  exit 5
else
  ln -s "${CODE_ROOT}/tests" "${DATA_ROOT}/tests"
fi
"${ENV_ROOT}/bin/appworld" verify tests --root "${DATA_ROOT}"
"${ENV_ROOT}/bin/appworld" verify tasks --root "${DATA_ROOT}"

"${ENV_ROOT}/bin/python" - "${CODE_ROOT}" "${DATA_ROOT}" "${ENV_ROOT}" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

code_root = pathlib.Path(sys.argv[1]).resolve()
data_root = pathlib.Path(sys.argv[2]).resolve()
env_root = pathlib.Path(sys.argv[3]).resolve()
commit = subprocess.check_output(
    ["git", "-C", str(code_root), "rev-parse", "HEAD"], text=True
).strip()
dataset_files = sorted(path for path in (data_root / "data").rglob("*") if path.is_file())
digest = hashlib.sha256()
for path in dataset_files:
    relative = path.relative_to(data_root).as_posix().encode("utf-8")
    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    digest.update(path.stat().st_size.to_bytes(8, "big"))

receipt = {
    "schema_version": "appworld_setup_receipt_v1",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "source_url": "https://github.com/StonyBrookNLP/appworld.git",
    "source_commit": commit,
    "code_root": str(code_root),
    "data_root": str(data_root),
    "environment_root": str(env_root),
    "dataset_file_count": len(dataset_files),
    "dataset_total_bytes": sum(path.stat().st_size for path in dataset_files),
    "dataset_path_size_manifest_sha256": digest.hexdigest(),
    "verification": {"tests": "passed", "tasks": "passed"},
}
(data_root / "setup_receipt.json").write_text(
    json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(receipt, ensure_ascii=False, indent=2))
PY
