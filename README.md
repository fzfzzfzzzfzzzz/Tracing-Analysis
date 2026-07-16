# TraceGraph：工具轨迹依赖图与生命周期压缩实验框架

本项目根据根目录的《工具调用建图_生命周期压缩调研报告》实现。核心目标不是通用文本摘要，而是在每轮 LLM 调用前，用运行时依赖图判断哪些工具轨迹可以安全移出 active context，同时完整保留可恢复原始记录。

当前实现包括：

- 10 类节点与报告定义的 10 类有向边；
- 工具调用、MCP 调用、成功、失败、超时、部分成功、side effect 与 token 记录；
- SHA-256 内容寻址外部归档和完整性校验；
- 在线增量建图、重试/解决/覆盖关系推断；
- Created、Active、Critical Evidence、Consumed、Unresolved/Resolved Failure、Superseded、Archived、Audit-required 状态；
- 图硬约束、证据路径保护和可恢复压缩；
- 固定 Agent scaffold，仅替换 context manager；
- 报告列出的 7 个 baseline、4 个 ablation 和 Full Ours；
- 离线生命周期分析、结构 Oracle 上界、在线前缀回放和对照/消融实验；
- 当前 τ³-bench 双格式结果导入、旧 τ-bench `trajectory/traj` 兼容和 live agent 入口；
- hash 固定的 Microsoft ACON 官方 observation/history optimizer 外部适配器；
- 主指标与结构可靠性指标、JSONL/CSV 聚合结果和 provenance 清单；
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

`zai/glm-4.5-air` 的首轮 30-session Stage 1 官方 task success 为 0.40，历史结果见 [Stage 1 正式结果](docs/STAGE1_RESULTS.md)。随后使用免费 `zai/glm-4.7-flash` 按同一 10-task × 3-trial gate 重跑，取得 `16/30 = 0.5333`、normal stop `0.90`、0 infrastructure error、实际成本 `$0.00`，全部 gate 通过。30 图机器生命周期/Oracle/预算实验、8-session pipeline smoke 和 40-session single-trial preliminary paired pilot 见 [GLM-4.7-Flash 结果](docs/GLM47_FLASH_RESULTS.md)。

## 官方 ACON 外部 baseline

第三方源码不会提交到本仓库。先下载并校验固定的 Microsoft ACON 快照：

```powershell
./scripts/setup_acon.ps1
$env:TRACEGRAPH_MANAGER = "acon_official"
$env:TRACEGRAPH_ACON_ROOT = "vendor/acon-main"
$env:TRACEGRAPH_ACON_CONFIG = "configs/acon_tau3.json"
```

适配器默认在任何 optimizer 异常、空输出、源码 hash 不符或 provider usage 缺失时 fail closed。GLM-4.7-Flash 已通过模型适用性 gate，但官方 ACON live paired run 仍需冻结 compressor 配置、限流规则和完整 provenance 后单独执行。实现与解释边界见 [强 baseline 官方实现审计](docs/STRONG_BASELINES.md)。

## 文档

- [架构与数据流](docs/ARCHITECTURE.md)
- [完整实验协议](docs/EXPERIMENTS.md)
- [指标定义](docs/METRICS.md)
- [τ³-bench 集成](docs/TAU3_INTEGRATION.md)
- [GLM 真实 Pilot](docs/GLM_PILOT.md)
- [正式实验矩阵与成本门控](docs/FORMAL_MATRIX.md)
- [Stage 1 正式结果与决策](docs/STAGE1_RESULTS.md)
- [GLM-4.7-Flash 机器标签实验结果](docs/GLM47_FLASH_RESULTS.md)
- [生命周期人工双标协议](docs/LIFECYCLE_ANNOTATION.md)
- [强 baseline 官方实现审计](docs/STRONG_BASELINES.md)
- [数据与结果格式](docs/DATA_FORMAT.md)
- [已执行验证](docs/VALIDATION.md)
- [调研报告逐项追踪](docs/REQUIREMENTS_TRACEABILITY.md)

## 研究完整性

- “移除”只表示不进入下一轮 LLM context，原始记录不会物理删除。
- synthetic 结果始终带 `synthetic=true` 和解释警告。
- 离线回放不伪造 counterfactual task success 或 policy violation；这两项只从真实 benchmark 评价或 live run 读取。
- `llm_only_pruning`、`agentdiet_style`、`acon_style` 会标记为 proxy；`acon_official` 只有在 live runtime provenance 完整且无 fallback 时才具备主结果资格。
