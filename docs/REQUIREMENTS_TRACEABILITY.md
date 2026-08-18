# 调研报告逐项追踪

## GDSC v2.0 当前追踪

状态含义：`合同冻结` 表示接口/门禁已在文档预注册，不等于代码或实验通过；工程项由测试验证，机制与 eligibility 项由冻结 artifact 裁决。R2/E0 当前均为 No-Go。

| GDSC 要求 | 预期实现/产物 | 当前证据状态 |
| --- | --- | --- |
| EventGraph 兼容、旧 `full_ours` 不变 | 原 `TraceGraph`/manager registry；兼容回归 | 通过；改造后全量 `144 passed` |
| 稳定事件/atom/edge/state hash | canonical JSON + `DecisionStateGraph` reducer | 通过 deterministic/prefix/suffix tests |
| 纯 tool-call assistant 生成 Decision | τ³ adapter/EventGraph capture | adapter regression 通过 |
| cutoff-only neutral reducer | Decision-state reducer | 未来 suffix 不影响 prefix test 通过 |
| 确定性 `DecisionQuery` | pending operation/retry/schema/policy scope | 候选工具保守保留 tests 通过 |
| 六类表示与来源约束 | compiler representation manifest | equivalence/provenance/tamper/illegal-guard tests 通过 |
| hard closure + deterministic beam | compiler，默认 beam=16 | coverage/protocol/budget/tie-break tests 通过 |
| 软预算与 provider hard limit 分离 | `PromptBundle` budget status | conservative bundle / hard abort tests 通过 |
| 五层成本 | prompt cost profiler + request artifact + usage completion | 192/192 closure mismatch 已复现；新请求 hash/usage 回填 tests 通过 |
| deterministic/calibrated omission risk | safety mask；task-held-out logistic artifact | split/round-trip tests 通过；无合格 harm gold，运行时保持 deterministic |
| benchmark eligibility | `benchmark_eligibility.json` | **E0 No-Go**；retail/airline 均只有 5 tasks、median actions 7/6，且动态历史/snapshot/evaluator 不完整 |
| 新 manager 与组合条件 | `decision_state_compiler` / `gdsc_core_v1`；`acon_official_with_gdsc_state` | registry/runtime compatibility tests 通过 |
| τ³ request/snapshot provenance | pre-send request artifact、snapshot restore、post-return usage | request/usage 集成 tests 通过；旧数据 snapshot 证据缺失导致 E0 失败 |
| R2/R3/R4 gate | `r2_offline_mechanism.json`, `benchmark_eligibility.json` | **R2 No-Go**（14.956% <30%）且 E0 No-Go；R3/R4 未运行 |
| R2.1 成本归因与可达性 | `cost_attribution.py`, `analyze_gdsc_cost_attribution.py`, `render_gdsc_cost_attribution.py`; `outputs/gdsc_r2_1/` | 261/261 baseline 与 runtime hash；runtime 降幅 15.723%，fixed-floor 上界 28.451% <30%；分支 B，不实现 v1.1 |

## Phase 1–4 历史追踪

| 报告要求 | 实现 | 验证证据 |
| --- | --- | --- |
| Trace Capture 全字段 | `capture.py`, `schema.py` | core tests |
| 10 类节点/10 类边 | `schema.py`, `graph.py` | typed-edge tests |
| session 内在线增量建图 | `ToolExecutor`, `ContextManagedAgent` | runtime tests |
| retries/resolves/supersedes/compresses | `capture.py`, `adapters/tau.py`, `lifecycle.py` | adapter/lifecycle/runtime tests |
| 完整生命周期 | `LifecycleState`, `LifecycleEngine` | lifecycle tests |
| 未解决错误逻辑保留且原文可恢复 | `failure_cards.py`, `ArchiveStore` | scoped card、expiry、archive separation tests |
| 未解决错误不自动原文回注 | `GraphLifecycleManager`, `project_context_items_to_messages()` | card budget 与 protocol projection tests |
| 唯一不可恢复 evidence 硬保护；可恢复 evidence 预算内优先 | `GraphLifecycleManager` | lifecycle context / synthetic experiment tests |
| side effect 外部保存但不自动进入 active context | `ArchiveStore`, Audit-required | archive separation tests |
| summary + raw_ref 可恢复 | `compress_nodes()` | archive/compression test |
| active context view | `context.py` | context tests |
| Full trajectory agent | `runtime.py` + `FullTrajectoryManager` | runtime tests |
| τ-bench 主实验适配 | `adapters/tau.py`, `integrations/tau3_agent.py` | current/legacy JSON tests；官方 τ³ 环境导入与 agent 注册通过 |
| 实验一：离线生命周期 | `lifecycle_analysis.json` | experiment test |
| 实验二：Oracle | `OracleUpperBoundManager` | experiment test |
| 实验三：在线 manager | prefix replay + τ³ live agent + `paired.py` | `glm-4.7-flash` Stage 1 通过；旧 120-session 矩阵降级为修复前诊断；`content_estimate_v2` corrected smoke 与 120-session `g47f_ml_c2` 完成 |
| 实验四：ablation | 4 个独立 manager | registry/ablation tests |
| 7 个 baseline | 统一 registry | registry test |
| 结构可靠性指标 | `metrics.py` | context/experiment tests |
| 可复现与审计 | manifest、JSONL/CSV、CI、archive verify | CLI smoke |
| 正式矩阵与成本门控 | `matrix.py`, `plan_glm_matrix.py`, frozen JSON config | 10 runs / 30 sessions dry-run；secret/cost/duplicate validation tests |
| Stage 1 聚合与 gate | `stage1.py`, `analyze_glm_stage1.py` | 30/30 sessions/traces；官方 reward/action/termination；完整性硬门槛 |
| Paired live 聚合 | `paired.py`, `analyze_live_matrix.py` | manager/分域指标、Pass^1–Pass^k、task+trial 配对、exact McNemar、Holm 校正、bootstrap、selected-context token 差 |
| 压缩消息协议闭包 | `message_protocol.py`, `tau3_agent.py` | tool call/result 闭包、最近 user anchor、Failure Card 不恢复历史工具交换 |
| 第三阶段 Failure Card P0 | `failure_cards.py`, `FailureCard`, `GraphLifecycleManager`, `RawHardFailureRetentionManager` | scope 聚合、分类、expiry、card budget、legacy 对照 tests |
| 第三阶段 P1 机制干预 | `interventions.py`, `run-p1-interventions` | 4 类 × 8 tasks × 4 conditions；128/128 图有效；controlled precision/expiry = 1.0 |
| 第三阶段 P2 failure-chain 双标 | `failure_chain_annotation.py`, export/score scripts | 32 controlled + 28 natural；Codex 临时 A/B、provenance/identity/warning、裁决和评分完成；formal gate 拒绝非人工 provenance |
| 第三阶段 P3/P4 gate | `phase3_gates.py`, `evaluate_phase3_gates.py`, matrix execution guard | 24-session evaluator-fix 平衡补跑与 60-session 修复后复合分析完成；P3 数据完整但 formal gate 不通过；P4 No-Go 时 API 前拒绝 |
| ACON + Failure Card | `acon_official_with_failure_cards`, `tau3_agent.py` | 官方 ACON plan + bounded native card overlay；runtime eligibility required；最新 P4 gate 为 No-Go |
| 真实预算选择 | `budget_sweep.py`, `run_budget_sweep.py` | `content_estimate_v2` 的 30 图 2048/4096/8192/12288/16384 sweep；推荐 4096 |
| 人工双标工具 | `annotation.py`, export/score scripts | 盲化双表、隔离 key、Cohen's κ、裁决表 tests |
| 生命周期分歧诊断 | `lifecycle_diagnostics.py`, `analyze_lifecycle_disagreements.py` | 修正版 30 个 lifecycle/no-lifecycle 配对、11 个 raw 成功分歧、4 个失败信号配对、12 条优先 trace；修复前 120 条定向盲标包仅用于错误分析 |
| failure-rich 任务选择 | `failure_selection.py`, `select_failure_rich_tasks.py` | 官方 GPT-4.1 四轮历史结果的 Error/retry 排名；冻结 10-task failure-retention 矩阵 |
| Token 口径审计 | `retokenize.py`, provider usage 聚合、matrix runtime guard | 30 条 Stage 1 图重计量；中位 4,875；推荐预算 4096；旧 paired 结果撤回 |
| 强 baseline provenance | `manager_provenance.py`, `STRONG_BASELINES.md` | manifest 标记 native/proxy、主结果资格和官方来源；unknown fail closed |

## 当前外部资源与范围边界

1. 旧 Card-only P4 gate 仍是该历史矩阵的 No-Go，不限制按 GDSC 新预注册运行 τ³ development 实验；也不得被 GDSC 工程完成追溯改成 Go。
2. GDSC 外部运行只允许预注册的免费 `zai/glm-4.7-flash`，无付费/模型 fallback；价格、额度和零成本估算必须在启动时重新核验。
3. 当前 60-session 修复后复合数据集可用于负结果和 measurement 分析，但不得写成一次连续运行或正式 human-validated P3 成功。
4. 两位独立人工 gold 与第二 benchmark 未包含在本轮 τ³-only development 范围；没有它们不得声称最终 construct validity 或 AAAI 双 benchmark evidence。
5. E0、R2、R3 任一门禁失败都要求停止；不得挑任务、降低阈值、增加样本或自动补跑以追求正向结论。

官方 τ³ 所需 `uv`、CPython 3.12 和隔离环境已经安装并通过 `tau2 check-data`、TraceGraph editable import、`tracegraph_agent`/`tracegraph_user_simulator` registry 注册及上游 CLI 入口验证。GLM mock live task 已通过；两次 retail 真实 pilot 和三图离线结构分析已执行并按失败边界记录。

`glm-4.5-air` Stage 1 的失败记录保留在 `docs/STAGE1_RESULTS.md`；`glm-4.7-flash` 已完成 30/30 并在修正 token 口径后继续通过 gate。旧 120-session 矩阵不再作为效果结论，corrected 矩阵采用 4096 预算并分开报告内容估算与 provider usage。详细证据见 `docs/TOKEN_ACCOUNTING.md` 与 `docs/GLM47_FLASH_RESULTS.md`。

以上项目不影响代码与实验管线完整性，但没有这些外部输入时不能声称真实 benchmark 假设已被证实。
