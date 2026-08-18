# Phase 5 execution ledger

> Ledger version: `phase5_liveness_results_v2`
>
> Current stage: F5-E0 offline replay completed; stopped before F5-G2
>
> Gate status: F5-G0 passed; **F5-G1 No-Go**
>
> External provider sessions: `0`; generation remains unauthorized

## Current decision

The Phase 4 dirty worktree was preserved in a recoverable checkpoint before Phase 5 tracked
changes. Phase 4 R2/E0 remain No-Go, including the historical 30% threshold and all frozen
results. The new `lifecycle_graph_context` path implements a shared `LiveSubgraph` and the
deletion-only `gdsc_prune_v1` strategy. `gdsc_structured_v1` remains gate locked and has no
online implementation.

F5-G0 passed. F5-G1 failed its outcome-blind, pre-frozen aggregate cost criterion. Across
185 cost-eligible prefixes, only 4 had a smaller complete serialized request and the paired
median Prune-minus-Raw token delta was exactly `0`, while the frozen criterion required a
strictly negative median. Engineering and offline safety checks passed, but this does not
override the failed cost gate. Per the plan, work stops before F5-G2, GDSC-Structured, or any
online/external pilot.

## F5-WP0 checkpoint

The complete baseline, recovery instructions, hashes, and failed first generator attempt are
documented in [PHASE5_CHECKPOINT.md](PHASE5_CHECKPOINT.md).

| Item | Result |
| --- | --- |
| Base revision | `fe9ee53c955652a12bd1cc39a02773247982efbd` |
| Dirty diff SHA-256 | `b19682e1845bd8160141551f50c2d79bb1a9b9b95f0481135a92f6aaad48d74d` |
| Checkpoint SHA-256 | `f5d30ed51d7988204e7a3318e125afa6a0781ace0f6132be43bb17e5d90a4004` |
| Baseline tests | `147 passed` |
| Phase 4 artifact hash audit | passed |
| New output root | `outputs/phase5` |

The Phase 5 config and schema are
[`phase5_liveness.json`](../configs/phase5_liveness.json) and
[`phase5_liveness.schema.json`](../configs/phase5_liveness.schema.json).

## F5-WP1–WP3 engineering

Implemented:

- `build_state()` → prefix-only `DecisionLifecycleGraph`;
- `derive_roots()` → explicit, provenance-bearing `LivenessRoots`;
- `analyze_liveness()` → shared `LiveSubgraph` with protocol span grouping;
- `project_context()` → deletion-only `LifecycleContextView`;
- `LifecycleGraphContextManager` compatibility adapter;
- canonical round-trip/hash validation for every new artifact;
- request-hash assertion and same-hash provider usage join;
- query-change reactivation without changing empty historical `decision_query_v1` hashes;
- fail-closed Structured entry before F5-G2.

The current conservative eviction rule requires all of the following:

1. the complete tool span is outside the live closure;
2. every member has an explicit resolved/superseded terminal reason within the cutoff;
3. parallel call/result grouping is complete;
4. all tool nodes have content-addressed archive references;
5. archive reads verify successfully;
6. final provider protocol remains valid.

Pending, uncertain, low-confidence, policy/confirmation/receipt, unverified archive, mixed
assistant content, missing result, and partially live parallel spans are retained. Missing,
duplicate, or out-of-order protocol records make the request send-ineligible rather than
causing additional hard-live deletion.

## F5-E0 frozen replay and F5-G1

The outcome-blind development manifest includes all 261 frozen Phase 4 decision points from
30 source sessions. Selection used only identity, cutoff, prefix-hash, and prefix-derived
structural fields; it did not inspect treatment outcomes or task rewards.

| Artifact | SHA-256 |
| --- | --- |
| Development manifest | `4da20d81ccbc61635baad08edad684234b3f74c0a51f0ca82612232b5fdd86f7` |
| Native tool-schema artifact | `02f6290f1867da5ae9734bdd8091305727db1b62fc048a508ade892aace2f622` |
| Authoritative replay-v2 run manifest | `b2430eebe767ddfecc809f020a07d250142dc1454b1a8d4e67e80aa73e2dc081` |

The frozen corpus contains 185 cost-analysis-eligible prefixes and 24 structural
reactivation candidates. Only 4 prefixes produced an actually evictable span after the
conservative lifecycle and protocol checks.

| Frozen F5-G1 criterion | Observed | Required | Result |
| --- | ---: | ---: | --- |
| Same-prefix artifact determinism | `1.000` | `1.000` | pass |
| Future-suffix independence | `1.000` | `1.000` | pass |
| Protocol validity | `1.000` | `1.000` | pass |
| Root-event recall | `1.000` | `1.000` | pass |
| Critical-event recall | `1.000` | `1.000` | pass |
| Policy/confirmation/receipt false-dead | `0/0/0` | `0/0/0` | pass |
| Archive reactivation/hash | `1.000` | `1.000` | pass |
| Pre-send/request hash match | `1.000` | `1.000` | pass |
| Prefixes with lower serialized input | `4/185` | at least `1` | pass |
| Paired median serialized token delta | `0` | `<0` | **fail** |
| External provider generations | `0` | `0` | pass |

The authoritative reports are under
`outputs/phase5/e0_development_v1/prune_replay_v2/`. The preserved `prune_replay_v1`
attempt reported a false-negative reactivation rate because its audit compared raw archived
provider envelopes with normalized node content. That attempt was not overwritten. Replay
v2 correctly verifies the content-addressed archive payload against its SHA-256 handle and
confirms 4/4 reactivations.

Five of the 30 Phase 4 `event_graph_sha256` fields cannot be reproduced because legacy graph
loading assigned random IDs and timestamps to derived canonical lifecycle edges. Old hashes
and artifacts remain unchanged. Phase 5 fixed the loader for deterministic derived-edge
identity, froze each raw source-file hash, verified repeat-load hashes, and matched every one
of the 261 frozen prefix-node hashes before evaluation.

## Validation

Post-replay local validation:

```text
pytest: 172 passed in 6.95s
ruff check .: All checks passed!
external provider sessions: 0
```

The Phase 5 liveness/prune/offline fixtures cover stable hashes, future-suffix independence,
neutrality to old derived lifecycle labels, explicit supersession/resolution/consumption/
invalidation, low-confidence terminal-edge rejection, query reactivation, archive
unavailable/tamper fallback, side-effect receipts, missing results, parallel tool calls,
duplicate/out-of-order results, mixed-content raw fallback, budget fail-closed behavior,
exact raw message preservation, request/usage hash join, artifact round trip, manager
non-mutation, and the Structured gate.

These are engineering invariants only. They do not establish human construct validity,
ContextSafetyBench sensitivity/specificity, provider-actual savings, task-success
non-inferiority, or representation-induced-harm rates.

## Stop decision

F5-G1 is No-Go. Do not implement the Structured condition, construct the F5-G2 benchmark
claim, or run a common-prefix external pilot under this plan. Do not lower the cost threshold,
select only the four favorable prefixes, or overwrite either replay attempt. Any future work
beyond offline diagnosis requires a new user-approved plan; it cannot reinterpret this gate
as passed.

## Phase 5.1 lifecycle-evidence addendum

The independent Phase 5.1 evidence-ceiling audit is reported in
[PHASE51_LIFECYCLE_EVIDENCE_RESULTS.md](PHASE51_LIFECYCLE_EVIDENCE_RESULTS.md). It did not
reopen this F5-G1 decision. Grade A deterministic scalar-consumption evidence increased the
number of cost-eligible prefixes with lower serialized input from 4 to 10. Even the unsafe,
ceiling-only Grade B interpretation reduced only 36/185 eligible prefixes, below the frozen
93-prefix requirement for a strictly negative paired median; the observed ceiling median
delta remained 0. P51-G0 therefore stopped the old-corpus path. F5-G1 remains No-Go and no
Structured or external condition was authorized.
