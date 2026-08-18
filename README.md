# TraceGraph：工具轨迹依赖图与生命周期压缩实验框架

本项目根据根目录的《工具调用建图_生命周期压缩调研报告》实现。核心目标不是通用文本摘要，而是在每轮 LLM 调用前，用运行时依赖图判断哪些工具轨迹可以安全移出 active context，同时完整保留可恢复原始记录。

当前实现包括：

- 10 类节点与报告定义的 10 类有向边；
- 工具调用、MCP 调用、成功、失败、超时、部分成功、side effect 与 token 记录；
- SHA-256 内容寻址外部归档和完整性校验；
- 在线增量建图、重试/解决/覆盖关系推断；
- Created、Active、Critical Evidence、Consumed、Unresolved/Resolved Failure、Superseded、Archived、Audit-required 状态；
- 图硬约束、证据路径保护和可恢复压缩；
- 第三阶段 operation-scope Failure Card：失败分类、显式过期、独立 12.5% 子预算，以及 archive/audit 与 active context 分离；
- 固定 Agent scaffold，仅替换 context manager；
- 报告列出的 7 个 baseline、4 个 ablation、第二阶段 Raw Hard Failure Retention 对照和 card-aware Full Ours；
- 离线生命周期分析、结构 Oracle 上界、在线前缀回放和对照/消融实验；
- 当前 τ³-bench 双格式结果导入、旧 τ-bench `trajectory/traj` 兼容和 live agent 入口；
- hash 固定的 Microsoft ACON 官方 observation/history optimizer 外部适配器；
- 主指标与结构可靠性指标、JSONL/CSV 聚合结果和 provenance 清单；
- 第三阶段 P1 受控干预、P2 failure-chain 双盲包/评分器、P3/P4 fail-closed Go/No-Go；
- 第四阶段 factorized v2 构念、immutable trajectory/offline evaluator 协议、next-3-action 指标和独立工程/经验门禁；
- Windows/Linux、Python 3.11–3.13 CI。

## 快速开始

核心框架无运行时第三方依赖，可直接在本机 Python 3.11 使用：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
./scripts/run_smoke.ps1
```

或创建完整开发环境：

```powershell
./scripts/bootstrap.ps1
```

烟测结果写入 `outputs/smoke/`，并在 `manifest.json` 中明确标记为 synthetic，只证明实验管线可运行，不作为论文结论。

## 导入 τ-bench / τ³-bench 结果

```powershell
$env:PYTHONPATH = "src"
python -m tracegraph import-tau `
  --input path\to\tau-results `
  --output data\processed\tau-graphs `
  --archive artifacts\tau-archive

python -m tracegraph run-offline `
  --input data\processed\tau-graphs `
  --output outputs\tau-offline `
  --archive artifacts\tau-archive `
  --budget 2048 `
  --provenance tau3_saved_results
```

`--input` 支持：当前单文件 `results.json`、当前 `results.json + simulations/*.json` 目录、单个 `SimulationRun` JSON，以及旧版带 `trajectory`/`traj` 的 JSON。

## GLM 真实 Pilot

将 GLM key 只写入被忽略的本地 `.env`，然后使用安全 runner：

```powershell
./scripts/run_glm_pilot.ps1 -Domain mock -TaskId create_task_1 `
  -Manager full_trajectory -Budget none -MaxSteps 8 -VerboseLogs
```

已完成的真实 mock/retail 结果、成本与模型协议边界见 [GLM Pilot](docs/GLM_PILOT.md)。
对于明确结束意图未映射到 τ³ 停止标记的模型，可显式添加 `-NormalizeUserStop`；该开关默认关闭，正式对比必须跨条件固定。

正式矩阵默认只生成零费用 manifest；执行必须显式提供估算成本上限，见 [正式实验矩阵](docs/FORMAL_MATRIX.md)。

`zai/glm-4.5-air` 的首轮 30-session Stage 1 官方 task success 为 0.40，历史结果见 [Stage 1 正式结果](docs/STAGE1_RESULTS.md)。随后使用免费 `zai/glm-4.7-flash` 按同一 10-task × 3-trial gate 重跑并通过全部 gate。轨迹审计发现旧 paired matrix 把 provider prompt usage 错计为消息节点大小，因此旧 120-session reward 只保留为修复前诊断，不再作为 context-manager 效果结论；`content_estimate_v2` 修正版把图预算与真实 provider usage 分离，并将推荐预算重选为 4096。修正版 `g47f_ml_c2` 已完成 120/120 sessions：Full Ours 的 raw success 为 17/30，相对 Full Trajectory 的 28 个有效配对成功率差为 `+0.1429`，但 95% CI 跨 0；全部 120 个图和 1,768 个归档对象通过校验。完整结果与边界见 [Token 计量修正](docs/TOKEN_ACCOUNTING.md) 和 [GLM-4.7-Flash 结果](docs/GLM47_FLASH_RESULTS.md)。

failure-rich 修正版 `g47f_fr_c2` 也已完成 90/90 sessions：Full Trajectory / Ours without failure retention / Full Ours raw success 分别为 10/30、4/30、7/30。Full Ours 相对去掉 failure retention 的有效配对成功率差为 `+0.0800`，但 McNemar p=0.5000，且全矩阵只有 2 条真实 `retries` 边、0 条 `resolves` 边。因此当前只能说明 failure retention 有弱正向点估计，不能证明 H4；下一步应做人工 gold 与受控故障注入或 SWE-bench 类长轨迹扩展。

## 官方 ACON 外部 baseline

第三方源码不会提交到本仓库。先下载并校验固定的 Microsoft ACON 快照：

```powershell
./scripts/setup_acon.ps1
$env:TRACEGRAPH_MANAGER = "acon_official"
$env:TRACEGRAPH_ACON_ROOT = "vendor/acon-main"
$env:TRACEGRAPH_ACON_CONFIG = "configs/acon_tau3.json"
```

适配器默认在任何 optimizer 异常、空输出、源码 hash 不符或 provider usage 缺失时 fail closed。GLM-4.7-Flash 已通过模型适用性 gate，但官方 ACON live paired run 仍需冻结 compressor 配置、限流规则和完整 provenance 后单独执行。实现与解释边界见 [强 baseline 官方实现审计](docs/STRONG_BASELINES.md)。

第三阶段已按预注册停止规则收口：P1 的 128-run 机制实验通过；P2 已完成明确标注 provenance 的 Codex 临时 A/B，但不是独立人工 gold；P3 已完成 24-session 平衡补跑和 60-session 修复后复合分析；P4 的 ACON+card 工程与门禁完成，但最新 gate 为 No-Go，因此没有绕过门禁运行扩展。精确数值和 blocker 见 [第三阶段执行结果](docs/PHASE3_RESULTS.md)。

第四阶段已按 handoff 完成零 API 的 P3b-A 可识别性修复：v2 将 Card retention 状态与 expiry cause 拆分；τ³ 支持先持久化 trajectory、再离线评测与按 simulation/hash 合并 reward；冻结的 60-session 复合矩阵已生成 next-3-action 报告（49 个 eligible failure events、98/98 action-view 对齐）。工程 gate 已通过，但 v2 仍是 Codex provisional，common-prefix fork 尚未执行，因此正向经验主张与 P3b-B 继续 No-Go。实现、数值和复现命令见 [第四阶段执行结果](docs/PHASE4_RESULTS.md)。

2026-07-21 的研究重置不再把 Failure Card 当作主算法：新的第四阶段主线是 Graph-Constrained Decision-State Compiler（GDSC），目标是把轨迹编译为下一动作所需的最小、可验证、provider-cost-aware 决策状态。旧 P3b-A 保留为 FailureGuard 机制切片和实验基础设施；Card-only fork 与 negative-results 收口不再是唯一下一步。详见 [第四阶段研究重置计划](第四阶段修改计划.md)。

GDSC 的研究协议与结果账本已和历史结果分离：[`GDSC_PREREGISTRATION.md`](docs/GDSC_PREREGISTRATION.md) 冻结 R0–R4 门禁和停止规则，[`CLAIM_EVIDENCE_MATRIX.md`](docs/CLAIM_EVIDENCE_MATRIX.md) 约束每项可写主张所需证据，[`PHASE4_GDSC_RESULTS.md`](docs/PHASE4_GDSC_RESULTS.md) 登记实际运行结果。R0 development gate 通过，但历史 R2 的 median serialized reduction 为 `14.956% < 30%`；R2.1 按真实 τ³/LiteLLM prompt 口径复算为 `15.723%`，且完整 policy + native schemas 的固定成本上界仅允许 `28.451%` 降幅，证明冻结约束下 30% 不可达。E0 eligibility 也不合格，因此不实现 v1.1、不运行 R3/R4，外部会话为 0。

2026-07-28 起，项目级下一阶段明确编号为 **Phase 5**。新计划不再把整个 GDSC 定义为 compiler，而将核心收敛为“基于决策状态图和生命周期的工具轨迹活性分析”：`GDSC-Prune` 只删除当前决策不可达且可安全回收的原始 tool spans，`GDSC-Structured` 原计划再单独检验 live subgraph 的结构化投影。Phase 4 的 R2/E0 No-Go 和30%门槛保持历史冻结。Phase 5 的 outcome-blind F5-E0 已完成：261 个 prefixes 的工程/安全离线不变量通过，但 185 个 cost-eligible prefixes 仅 4 个降低完整 serialized input，paired median Prune−Raw token delta=`0`，未达到冻结的 `<0` 门槛，因此 F5-G1 No-Go，停止在 Structured 与外部 pilot 之前。详见 [第五阶段修改计划](第五阶段修改计划.md)和 [Phase 5 结果账本](docs/PHASE5_RESULTS.md)。

## 文档

- [架构与数据流](docs/ARCHITECTURE.md)
- [完整实验协议](docs/EXPERIMENTS.md)
- [指标定义](docs/METRICS.md)
- [τ³-bench 集成](docs/TAU3_INTEGRATION.md)
- [GLM 真实 Pilot](docs/GLM_PILOT.md)
- [正式实验矩阵与成本门控](docs/FORMAL_MATRIX.md)
- [Stage 1 正式结果与决策](docs/STAGE1_RESULTS.md)
- [GLM-4.7-Flash 机器标签实验结果](docs/GLM47_FLASH_RESULTS.md)
- [Token 计量修正与实验版本边界](docs/TOKEN_ACCOUNTING.md)
- [生命周期人工双标协议](docs/LIFECYCLE_ANNOTATION.md)
- [生命周期分歧诊断与 failure-rich 任务选择](docs/LIFECYCLE_DIAGNOSTICS.md)
- [强 baseline 官方实现审计](docs/STRONG_BASELINES.md)
- [第三阶段修改计划](第三阶段修改计划.md)
- [第三阶段执行结果与门槛状态](docs/PHASE3_RESULTS.md)
- [第四阶段研究重置计划（GDSC v2.0）](第四阶段修改计划.md)
- [旧 Phase 4/P3b-A 执行结果与门槛状态](docs/PHASE4_RESULTS.md)
- [GDSC R0–R4 预注册](docs/GDSC_PREREGISTRATION.md)
- [第五阶段修改计划：生命周期活性分析与 Live Subgraph 投影](第五阶段修改计划.md)
- [GDSC 执行结果与门禁状态](docs/PHASE4_GDSC_RESULTS.md)
- [GDSC 主张—证据矩阵](docs/CLAIM_EVIDENCE_MATRIX.md)
- [数据与结果格式](docs/DATA_FORMAT.md)
- [已执行验证](docs/VALIDATION.md)
- [调研报告逐项追踪](docs/REQUIREMENTS_TRACEABILITY.md)

## 研究完整性

- “移除”只表示不进入下一轮 LLM context，原始记录不会物理删除。
- synthetic 结果始终带 `synthetic=true` 和解释警告。
- 离线回放不伪造 counterfactual task success 或 policy violation；这两项只从真实 benchmark 评价或 live run 读取。
- `llm_only_pruning`、`agentdiet_style`、`acon_style` 会标记为 proxy；`acon_official` 只有在 live runtime provenance 完整且无 fallback 时才具备主结果资格。
