# 调研报告逐项追踪

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

## 尚需真实外部资源的事项

1. 当前 P4 已判 No-Go，不需要为继续当前扩展而临时找人工；若投稿或重新主张构念有效性，再安排两位独立人工从无 Codex 标签泄漏的干净副本开始标注。
2. 若开启新一轮方法实验，先针对 Codex 临时结果暴露的 expiry precision `0.744` 和 scope error `0.400` 收缩规则，再重新预注册任务与门槛。
3. 当前 60-session 修复后复合数据集可用于负结果和 measurement 分析，但不得写成一次连续运行或正式 human-validated P3 成功。
4. 只有新证据令 P4 gate 转为 Go 后，才运行 ACON+card、第二模型家族和第二环境；不得绕过现有 No-Go。

官方 τ³ 所需 `uv`、CPython 3.12 和隔离环境已经安装并通过 `tau2 check-data`、TraceGraph editable import、`tracegraph_agent`/`tracegraph_user_simulator` registry 注册及上游 CLI 入口验证。GLM mock live task 已通过；两次 retail 真实 pilot 和三图离线结构分析已执行并按失败边界记录。

`glm-4.5-air` Stage 1 的失败记录保留在 `docs/STAGE1_RESULTS.md`；`glm-4.7-flash` 已完成 30/30 并在修正 token 口径后继续通过 gate。旧 120-session 矩阵不再作为效果结论，corrected 矩阵采用 4096 预算并分开报告内容估算与 provider usage。详细证据见 `docs/TOKEN_ACCOUNTING.md` 与 `docs/GLM47_FLASH_RESULTS.md`。

以上项目不影响代码与实验管线完整性，但没有这些外部输入时不能声称真实 benchmark 假设已被证实。
