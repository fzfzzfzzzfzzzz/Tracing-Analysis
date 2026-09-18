"""Import externally authored evidence alternatives and causal partial orders."""

from __future__ import annotations

from pathlib import Path

from ..compression_audit.development_scoring import STRICT_FIELDS, make_rubric
from ..compression_audit.io import load_jsonl, stable_digest


def validate_rubric(row: dict, query, prefix, gold) -> dict:
    value = dict(row)
    claimed = value.pop("rubric_hash", None)
    if claimed and stable_digest(value) != claimed:
        raise ValueError("external rubric hash differs")
    if value["query_id"] != query.query_id or value["gold_hash"] != gold.gold_hash:
        raise ValueError("external rubric query/gold identity differs")
    if set(value["strict_values"]) - STRICT_FIELDS:
        raise ValueError("unknown strict fact")
    if any(not isinstance(v, str) or not v.strip() for v in value["necessary_facts"].values()):
        raise ValueError("necessary facts must be nonempty text")
    visible = {e["event_id"] for e in prefix.events}
    relevant = set(value["relevant_evidence_ids"])
    if not relevant <= visible:
        raise ValueError("rubric references nonexistent public evidence")
    alternatives = value["alternative_evidence_sets"]
    if not alternatives or any(not a or len(a) != len(set(a)) or not set(a) <= relevant for a in alternatives):
        raise ValueError("invalid alternative evidence sets")
    modes = ("strict", "partial_order")
    if value.get("causal_mode", "strict") not in modes:
        raise ValueError("unknown causal scoring mode")
    paths = value.get("causal_paths") or [{"evidence_ids": a,
        "constraints": value["causal_constraints"]} for a in alternatives]
    if value.get("causal_mode") == "partial_order":
        if {frozenset(p["evidence_ids"]) for p in paths} != {frozenset(a) for a in alternatives}:
            raise ValueError("every evidence alternative needs its own causal constraints")
    for path in paths:
        nodes = set(path["evidence_ids"])
        edges = path["constraints"]
        if any(len(e) != 2 or e[0] == e[1] or not set(e) <= nodes for e in edges):
            raise ValueError("causal edge outside its evidence path")
        pending = set(nodes)
        while pending:
            roots = {n for n in pending if not any(b == n and a in pending for a, b in edges)}
            if not roots:
                raise ValueError("causal constraints contain a cycle")
            pending -= roots
    if not set(value["strict_chain"]) <= visible or len(value["strict_chain"]) != len(set(value["strict_chain"])):
        raise ValueError("invalid strict reference chain")
    if value["expected_scope"] not in ("current", "historical"):
        raise ValueError("invalid fact scope")
    if value.get("human_validated") and not value.get("annotation_receipt"):
        raise ValueError("human validation requires an explicit annotation receipt")
    value["causal_paths"] = paths
    value["rubric_hash"] = stable_digest(value)
    return value


def import_rubrics(path: Path | None, queries, prefixes, gold) -> dict:
    external = load_jsonl(path) if path else []
    if len({r["query_id"] for r in external}) != len(external):
        raise ValueError("duplicate external rubric")
    query_map = {q.query_id: q for q in queries}
    result = {}
    for row in external:
        q = query_map[row["query_id"]]
        result[q.query_id] = validate_rubric(row, q, prefixes[q.prefix_id], gold[q.prefix_id])
    for q in queries:
        result.setdefault(q.query_id, make_rubric(q, gold[q.prefix_id]))
    return result


def causal_evaluation(rubric: dict, cited: list) -> tuple[bool, float | None]:
    positions = {v: i for i, v in enumerate(cited)}
    if rubric.get("causal_mode", "strict") != "partial_order":
        constraints = rubric["causal_constraints"]
        hits = sum(a in positions and b in positions and positions[a] < positions[b] for a, b in constraints)
        return hits == len(constraints), hits / len(constraints) if constraints else None
    eligible = [p for p in rubric["causal_paths"] if set(p["evidence_ids"]) <= set(cited)]
    if not eligible:
        return False, 0.0
    rates = []
    for path in eligible:
        edges = path["constraints"]
        hits = sum(positions[a] < positions[b] for a, b in edges)
        rates.append(hits / len(edges) if edges else 1.0)
    return max(rates) == 1, max(rates)
