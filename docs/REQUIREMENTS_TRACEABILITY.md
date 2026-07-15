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
| 实验三：在线 manager | prefix replay + τ³ live agent | mock live reward 1.0；retail 分别暴露 stop 协议和工具 variant 错误，正式矩阵待稳定模型 |
| 实验四：ablation | 4 个独立 manager | registry/ablation tests |
| 7 个 baseline | 统一 registry | registry test |
| 结构可靠性指标 | `metrics.py` | context/experiment tests |
| 可复现与审计 | manifest、JSONL/CSV、CI、archive verify | CLI smoke |
| 正式矩阵与成本门控 | `matrix.py`, `plan_glm_matrix.py`, frozen JSON config | 10 runs / 30 sessions dry-run；secret/cost/duplicate validation tests |
| Stage 1 聚合与 gate | `stage1.py`, `analyze_glm_stage1.py` | 30/30 sessions/traces；官方 reward/action/termination；完整性硬门槛 |
| 真实预算选择 | `budget_sweep.py`, `run_budget_sweep.py` | 30 图 4096/8192/16384 sweep；推荐 16384 |
| 人工双标工具 | `annotation.py`, export/score scripts | 盲化双表、隔离 key、Cohen's κ、裁决表 tests |
| 强 baseline provenance | `manager_provenance.py`, `STRONG_BASELINES.md` | manifest 标记 native/proxy、主结果资格和官方来源；unknown fail closed |

## 尚需真实外部资源的事项

1. 为 `glm-4.6` 或更强模型开通余额/资源包；当前 `glm-4.5-air` Stage 1 成功率只有 0.40。
2. 用同一冻结配置重新通过 10-task × 3-trial gate 后，才运行 16384 预算的 live manager 对照。
3. 为生命周期 gold labels 安排两位独立人工标注者并完成裁决；工具和 120 条 blind pilot 包已准备。
4. 选择含真实工具失败/重试的任务，补足 failure/edge/lifecycle ablation 的可识别性。
5. 将 proxy baseline 替换成论文/官方强实现后再形成论文主表。

官方 τ³ 所需 `uv`、CPython 3.12 和隔离环境已经安装并通过 `tau2 check-data`、TraceGraph editable import、`tracegraph_agent`/`tracegraph_user_simulator` registry 注册及上游 CLI 入口验证。GLM mock live task 已通过；两次 retail 真实 pilot 和三图离线结构分析已执行并按失败边界记录。

Full Trajectory Stage 1 已完成 30/30 sessions，正式判定失败；详细证据见 `docs/STAGE1_RESULTS.md`。在线 context-manager 矩阵按协议暂停，未把模型能力失败误归因于压缩算法。

以上项目不影响代码与实验管线完整性，但没有这些外部输入时不能声称真实 benchmark 假设已被证实。
