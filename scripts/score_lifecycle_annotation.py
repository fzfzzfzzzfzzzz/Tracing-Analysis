"""检查两份人工判断，计算一致程度并导出分歧。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import json
from pathlib import Path

from tracegraph.annotation import score_annotations, write_annotation_score


def main() -> None:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument("--annotator-a", type=Path, required=True)
    parser.add_argument("--annotator-b", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = json.loads(args.key.read_text(encoding="utf-8"))
    expected_ids = {item["annotation_id"] for item in key["items"]}
    report = score_annotations(
        args.annotator_a, args.annotator_b, expected_ids=expected_ids
    )
    write_annotation_score(report, args.output)
    print(
        json.dumps(
            {
                "n": report["n"],
                "observed_agreement": report["observed_agreement"],
                "cohen_kappa": report["cohen_kappa"],
                "disagreement_count": report["disagreement_count"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
