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
| 实验三：在线 manager | prefix replay + τ³ live agent | mock live reward 1.0；retail 工具完成但 user stop 协议失败，正式矩阵待兼容 user model |
| 实验四：ablation | 4 个独立 manager | registry/ablation tests |
| 7 个 baseline | 统一 registry | registry test |
| 结构可靠性指标 | `metrics.py` | context/experiment tests |
| 可复现与审计 | manifest、JSONL/CSV、CI、archive verify | CLI smoke |

## 尚需真实外部资源的事项

1. 提供有额度且能遵守 τ³ `###STOP###` 协议的 user model，或预注册并跨条件固定、明确披露的 protocol adapter。
2. 冻结正式 retail/airline task IDs、agent/user models、seeds、budgets 和 trial 数。
3. 运行 10-task × 3-trial Full Trajectory pilot，再运行全部 manager。
4. 为生命周期 gold labels 做人工双标。
5. 将 proxy baseline 替换成论文/官方强实现后再形成论文主表。

官方 τ³ 所需 `uv`、CPython 3.12 和隔离环境已经安装并通过 `tau2 check-data`、TraceGraph editable import、`tracegraph_agent` registry 注册及上游 CLI 入口验证。GLM mock live task 已通过；retail 真实 pilot 和离线结构分析已执行并按失败边界记录。

以上项目不影响代码与实验管线完整性，但没有这些外部输入时不能声称真实 benchmark 假设已被证实。
