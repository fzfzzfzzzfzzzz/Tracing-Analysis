# 调研报告逐项追踪

| 报告要求 | 实现 | 验证证据 |
| --- | --- | --- |
| Trace Capture 全字段 | `capture.py`, `schema.py` | core tests |
| 10 类节点/10 类边 | `schema.py`, `graph.py` | typed-edge tests |
| session 内在线增量建图 | `ToolExecutor`, `ContextManagedAgent` | runtime tests |
| retries/resolves/supersedes/compresses | `capture.py`, `adapters/tau.py`, `lifecycle.py` | adapter/lifecycle/runtime tests |
| 完整生命周期 | `LifecycleState`, `LifecycleEngine` | lifecycle tests |
| 未解决错误不可删除 | `safety_decision()` | hard-constraint test |
| 唯一 evidence 不可删除 | `safety_decision()` | hard-constraint test |
| side effect 外部保存 | `ArchiveStore`, Audit-required | side-effect test |
| summary + raw_ref 可恢复 | `compress_nodes()` | archive/compression test |
| active context view | `context.py` | context tests |
| Full trajectory agent | `runtime.py` + `FullTrajectoryManager` | runtime tests |
| τ-bench 主实验适配 | `adapters/tau.py`, `integrations/tau3_agent.py` | current/legacy JSON tests；官方 τ³ 环境导入与 agent 注册通过 |
| 实验一：离线生命周期 | `lifecycle_analysis.json` | experiment test |
| 实验二：Oracle | `OracleUpperBoundManager` | experiment test |
| 实验三：在线 manager | prefix replay + τ³ live agent + `paired.py` | `glm-4.7-flash` Stage 1 通过；2-task smoke 8/8 完整；10-task × 4-condition single-trial pilot 40/40 完整 |
| 实验四：ablation | 4 个独立 manager | registry/ablation tests |
| 7 个 baseline | 统一 registry | registry test |
| 结构可靠性指标 | `metrics.py` | context/experiment tests |
| 可复现与审计 | manifest、JSONL/CSV、CI、archive verify | CLI smoke |
| 正式矩阵与成本门控 | `matrix.py`, `plan_glm_matrix.py`, frozen JSON config | 10 runs / 30 sessions dry-run；secret/cost/duplicate validation tests |
| Stage 1 聚合与 gate | `stage1.py`, `analyze_glm_stage1.py` | 30/30 sessions/traces；官方 reward/action/termination；完整性硬门槛 |
| Paired live 聚合 | `paired.py`, `analyze_live_matrix.py` | manager 指标、task+trial 配对、exact McNemar、bootstrap、selected-context token 差 |
| 压缩消息协议闭包 | `message_protocol.py`, `tau3_agent.py` | tool call/result 闭包、最近 user anchor、在线 Last-k 故障条件复验 |
| 真实预算选择 | `budget_sweep.py`, `run_budget_sweep.py` | 30 图 4096/8192/16384 sweep；推荐 16384 |
| 人工双标工具 | `annotation.py`, export/score scripts | 盲化双表、隔离 key、Cohen's κ、裁决表 tests |
| 强 baseline provenance | `manager_provenance.py`, `STRONG_BASELINES.md` | manifest 标记 native/proxy、主结果资格和官方来源；unknown fail closed |

## 尚需真实外部资源的事项

1. 将已完成的 10-task × 1-trial pilot 扩到至少 3 trials；免费端点需保留显式冷却和 infrastructure/timeout exclusion。
2. 为生命周期 gold labels 安排两位独立人工标注者并完成裁决；工具和 120 条 blind pilot 包已准备。
3. 选择含真实工具失败/重试的任务，补足 failure/edge/lifecycle ablation 的可识别性。
4. 将 proxy baseline 替换成论文/官方强实现后再形成论文主表。

官方 τ³ 所需 `uv`、CPython 3.12 和隔离环境已经安装并通过 `tau2 check-data`、TraceGraph editable import、`tracegraph_agent`/`tracegraph_user_simulator` registry 注册及上游 CLI 入口验证。GLM mock live task 已通过；两次 retail 真实 pilot 和三图离线结构分析已执行并按失败边界记录。

`glm-4.5-air` Stage 1 的失败记录保留在 `docs/STAGE1_RESULTS.md`；`glm-4.7-flash` 已完成 30/30 并通过 gate，在线 paired pipeline smoke 和 40-session preliminary pilot 也已跑通。详细证据见 `docs/GLM47_FLASH_RESULTS.md`。

以上项目不影响代码与实验管线完整性，但没有这些外部输入时不能声称真实 benchmark 假设已被证实。
