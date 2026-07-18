"""Apply a frozen Codex label pass to a blind P2 annotation sheet."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from tracegraph.failure_chain_annotation import (
    ANNOTATION_FIELDS,
    ANNOTATION_METADATA_FIELDS,
    LABEL_FIELDS,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--version", default="codex_p2_v1")
    args = parser.parse_args()

    with args.labels.open(encoding="utf-8-sig", newline="") as handle:
        label_rows = {
            row["annotation_id"]: row for row in csv.DictReader(handle)
        }
    with args.sheet.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    sheet_ids = {row["annotation_id"] for row in rows}
    if sheet_ids != set(label_rows):
        raise ValueError(
            f"sheet/label id mismatch: missing={sheet_ids - set(label_rows)}, "
            f"extra={set(label_rows) - sheet_ids}"
        )

    warning = "same_model_same_thread_not_independent_human_gold"
    for row in rows:
        labels = label_rows[row["annotation_id"]]
        for field, allowed in LABEL_FIELDS.items():
            value = str(labels.get(field) or "").strip()
            if value not in allowed:
                raise ValueError(f"invalid {field}={value!r} for {row['annotation_id']}")
            row[field] = value
        row.update(
            {
                "annotation_provenance": "codex_provisional",
                "annotator_identity": args.identity,
                "annotation_version": args.version,
                "independence_warning": warning,
                "confidence": str(labels.get("confidence") or "").strip(),
                "notes": str(labels.get("notes") or "").strip(),
            }
        )

    fieldnames = list(ANNOTATION_FIELDS)
    missing_metadata = set(ANNOTATION_METADATA_FIELDS) - set(fieldnames)
    if missing_metadata:
        raise AssertionError(f"annotation schema is missing metadata: {missing_metadata}")
    with args.sheet.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
