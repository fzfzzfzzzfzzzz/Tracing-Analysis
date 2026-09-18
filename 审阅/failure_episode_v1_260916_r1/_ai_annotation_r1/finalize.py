"""Merge pass outputs into final ai_draft files, validate, and compute A/B agreement."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import anno_lib as A

WORK = Path(__file__).resolve().parent
BASE = WORK.parent
EXEMPLAR_CID = "2180355931c9563fc692c4ae0a957bdb411cfd379a742c20ee6f6d04f187f02a"


def load_pass(tag: str) -> dict[str, dict]:
    rows = {}
    for p in sorted(WORK.glob(f"pass_{tag}/batch_*.jsonl")):
        for line in p.read_bytes().split(b"\n"):
            if line.strip():
                r = json.loads(line)
                rows[r["candidate_id"]] = r
    return rows


def referenced_events(episode: dict) -> set[str]:
    out = {episode["anchor_source_event_id"], *episode["initial_result_source_event_ids"]}
    for step in episode["repair_steps"]:
        out.update(step["decision_source_event_ids"])
        out.add(step["action_source_event_id"])
        out.update(step["result_source_event_ids"])
    out.update(episode["resolution_source_event_ids"])
    out.update(episode["required_core_source_event_ids"])
    out.update(episode["optional_support_source_event_ids"])
    return out


def f1(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    tp = len(a & b)
    if not tp:
        return 0.0
    p = tp / len(a)
    r = tp / len(b)
    return 2 * p * r / (p + r)


def cohen_kappa(labels_a: list, labels_b: list) -> float:
    assert len(labels_a) == len(labels_b)
    n = len(labels_a)
    cats = set(labels_a) | set(labels_b)
    po = sum(x == y for x, y in zip(labels_a, labels_b)) / n
    pe = sum(
        (labels_a.count(c) / n) * (labels_b.count(c) / n) for c in cats
    )
    if pe == 1.0:
        return float("nan")  # constant category, kappa not estimable
    return (po - pe) / (1 - pe)


def main() -> None:
    cases_order = list(A.cases())
    stats = {}
    passes = {}
    for tag in ("a", "b"):
        rows = load_pass(tag)
        out_rows = []
        for cid in cases_order:  # packet order
            r = json.loads(json.dumps(rows[cid]))
            r["annotator_kind"] = "ai"  # honest deviation from schema const "human"
            r.pop("rejection_reason", None) if False else None
            out_rows.append(r)
        # single identity per file
        anns = {r["annotator"] for r in out_rows}
        assert len(anns) == 1, anns
        dest = BASE / f"reviews.reviewer_{tag}.ai_draft.jsonl"
        with dest.open("w", encoding="utf-8", newline="\n") as fh:
            for r in out_rows:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        errs = A.check_file(dest)
        passes[tag] = {r["candidate_id"]: r for r in out_rows}
        stats[tag] = {
            "file": str(dest),
            "rows": len(out_rows),
            "status_counts": dict(Counter(r["annotation_status"] for r in out_rows)),
            "annotator": sorted(anns)[0],
            "mechanical_check_errors": errs,
        }
        print(f"reviewer_{tag}: wrote {dest.name}, {len(out_rows)} rows, check errors: {len(errs)}")

    # ---- A/B agreement (exclude exemplar case 001) ----
    a, b = passes["a"], passes["b"]
    ids = [c for c in cases_order if c != EXEMPLAR_CID]
    sa = [a[c]["annotation_status"] for c in ids]
    sb = [b[c]["annotation_status"] for c in ids]
    agree_status = sum(x == y for x, y in zip(sa, sb))
    kappa_status = cohen_kappa(sa, sb)

    both_ann = [c for c in ids if a[c]["annotation_status"] == "annotated" and b[c]["annotation_status"] == "annotated"]
    disc_ann = [c for c in ids if {a[c]["annotation_status"], b[c]["annotation_status"]} == {"annotated", "rejected"}]

    if both_ann:
        rec_a = [a[c]["failure_episode"]["recoverability"] for c in both_ann]
        rec_b = [b[c]["failure_episode"]["recoverability"] for c in both_ann]
        fam_a = [a[c]["failure_episode"]["failure_family"] for c in both_ann]
        fam_b = [b[c]["failure_episode"]["failure_family"] for c in both_ann]
        anchor_agree = sum(
            a[c]["failure_episode"]["anchor_source_event_id"] == b[c]["failure_episode"]["anchor_source_event_id"]
            for c in both_ann
        )
        sig_agree = sum(
            a[c]["failure_episode"]["error_signature"] == b[c]["failure_episode"]["error_signature"]
            for c in both_ann
        )
        fs = [f1(referenced_events(a[c]["failure_episode"]), referenced_events(b[c]["failure_episode"])) for c in both_ann]
        core_fs = [f1(set(a[c]["failure_episode"]["required_core_source_event_ids"]),
                      set(b[c]["failure_episode"]["required_core_source_event_ids"])) for c in both_ann]
        steps_a = [len(a[c]["failure_episode"]["repair_steps"]) for c in both_ann]
        steps_b = [len(b[c]["failure_episode"]["repair_steps"]) for c in both_ann]
        ep_stats = {
            "doubly_annotated": len(both_ann),
            "recoverability_kappa": cohen_kappa(rec_a, rec_b),
            "recoverability_raw_agree": sum(x == y for x, y in zip(rec_a, rec_b)) / len(both_ann),
            "failure_family_exact_agree": sum(x == y for x, y in zip(fam_a, fam_b)) / len(both_ann),
            "anchor_exact_agree": anchor_agree / len(both_ann),
            "error_signature_exact_agree": sig_agree / len(both_ann),
            "referenced_events_F1_mean": sum(fs) / len(fs),
            "referenced_events_F1_min": min(fs),
            "core_events_F1_mean": sum(core_fs) / len(core_fs),
            "repair_step_count_mean_abs_diff": sum(abs(x - y) for x, y in zip(steps_a, steps_b)) / len(steps_a),
        }
    else:
        ep_stats = {"doubly_annotated": 0}

    disagreement_cases = [
        {
            "candidate_id": c,
            "case_no": cases_order.index(c) + 1,
            "a": a[c]["annotation_status"],
            "b": b[c]["annotation_status"],
            "a_reason": (a[c].get("rejection_reason") or "")[:120],
            "b_reason": (b[c].get("rejection_reason") or "")[:120],
        }
        for c in disc_ann
    ]

    report = {
        "schema_version": "compression_audit_failure_episode_ai_draft_summary_v1",
        "generated": "2026-09-16",
        "annotator_model": "zcode / GLM-5.2 (AI drafts; NOT the formal double-human annotation gate)",
        "known_deviations": [
            "annotator_kind='ai' (schema const is 'human') — these files are development drafts",
            "case 001 shared a worked exemplar between passes; excluded from agreement stats",
            "both passes run the same underlying model; independence is process-level only",
        ],
        "passes": stats,
        "agreement": {
            "n": len(ids),
            "status_raw_agreement": agree_status / len(ids),
            "status_cohen_kappa": kappa_status,
            "disagreement_count": len(disc_ann),
            **ep_stats,
        },
        "status_disagreements": disagreement_cases,
    }
    dest = WORK / "summary_report.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report["agreement"].items() if not isinstance(v, list)},
                     ensure_ascii=False, indent=1))
    print("disagreements:", len(disagreement_cases))


if __name__ == "__main__":
    main()
