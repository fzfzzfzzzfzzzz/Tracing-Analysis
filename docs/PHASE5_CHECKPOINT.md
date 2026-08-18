# Phase 5 F5-WP0 checkpoint

> Checkpoint schema: `phase5_checkpoint_v1`
>
> Checkpoint ID: `f5_wp0_phase4_baseline`
>
> Development branch: `codex/phase5-livesubgraph-prune`
>
> External provider sessions: `0`; generation remains unauthorized

## Frozen Phase 4 baseline

The checkpoint was created before tracked Phase 5 implementation changes. It preserves the
dirty Phase 4 working tree without staging, committing, resetting, or rewriting historical
artifacts.

| Item | Frozen value |
| --- | --- |
| Base revision | `fe9ee53c955652a12bd1cc39a02773247982efbd` |
| Dirty diff SHA-256 | `b19682e1845bd8160141551f50c2d79bb1a9b9b95f0481135a92f6aaad48d74d` |
| Tracked patch SHA-256 | `80965d5057e484c9a6ab68ed09ea0b99d288eaa3e9e982afa91780d70622a516` |
| Untracked archive SHA-256 | `2f427e9138d983a46a1b2a238ba1fd1d585f89be0d6e895839dd2f108ba6d0d7` |
| Checkpoint manifest SHA-256 | `f5d30ed51d7988204e7a3318e125afa6a0781ace0f6132be43bb17e5d90a4004` |
| Pytest | `147 passed in 5.96s` |
| Ruff | `All checks passed!` |
| `git diff --check` | passed |

The successful append-only manifest is
`outputs/phase5/checkpoints/f5_wp0_phase4_baseline_v2/manifest.json`. The first generator
attempt is retained separately at `outputs/phase5/checkpoints/f5_wp0_phase4_baseline/`; it
stopped on Windows console decoding before a manifest was finalized and did not mutate
Phase 4 artifacts.

## Artifact audit

All ten targeted embedded hashes and all six files pinned by the R2.1 baseline manifest
recomputed successfully.

| Protected root | File count | Tree SHA-256 |
| --- | ---: | --- |
| `outputs/gdsc_r0_audit` | 20 | `15ac8851550f3b3a7f9e4ce6caaf826252bb5a10b679814daadfcb02bb381613` |
| `outputs/gdsc_r2_1` | 6 | `12e443366e814eb3403601952dc88763a90bb24c482862402d9110da40d7f491` |
| `outputs/phase4` | 15 | `85a75eb998b08591f426ff64ce328ccc867407042f93485e254b4bf685b93867` |

The manifest contains every artifact file path, size, and SHA-256. Phase 5 outputs are rooted
at `outputs/phase5`; the three protected Phase 4 roots remain outside that namespace.

## Frozen identity and gate boundary

- manager: `lifecycle_graph_context`
- implementation version: `lifecycle_graph_context_v1`
- primary strategy: `gdsc_prune_v1`
- gated incremental strategy: `gdsc_structured_v1`
- compatibility identity: `decision_state_compiler/gdsc_core_v1`

`GDSC-Structured` remains gated after `F5-G2`. The checkpoint does not authorize external
model sessions and does not change the Phase 4 R2/E0 No-Go, the historical 30% threshold, or
any old result.

## Post-checkpoint status

The checkpoint itself remains the pre-Phase-5 baseline. Subsequent tracked work on
`codex/phase5-livesubgraph-prune` implemented the offline LiveSubgraph and GDSC-Prune
interfaces. After F5-E0, the current regression is `172 passed`, with ruff and
`git diff --check` passing; all three protected artifact tree hashes above remain unchanged.
F5-G1 is No-Go because the pre-frozen eligible-set paired median serialized token delta was
`0`, not `<0`. External provider generation remains unauthorized.
