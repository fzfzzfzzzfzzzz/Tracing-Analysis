"""Print the deterministic, development-only policy v4 acceptance report."""

from __future__ import annotations

import json

from tracegraph.evaluation.policy_acceptance import verify_policy_v4_acceptance


def main() -> int:
    print(json.dumps(verify_policy_v4_acceptance(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
