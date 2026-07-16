# GLM Stage 1 正式结果（2026-07-16）

> 本页记录 `zai/glm-4.5-air` 的历史 Stage 1。随后免费 `zai/glm-4.7-flash` 已用同一 gate 重跑并通过；最新结果见 [GLM-4.7-Flash 机器生命周期标签实验结果](GLM47_FLASH_RESULTS.md)。

## 决策

`glm_full_trajectory_pilot_v1` 已完整执行，但 **未通过模型与 harness 适用性 gate**。唯一失败项是官方 task success rate：`12/30 = 0.40 < 0.50`。因此没有启动任何付费在线 context-manager 对照；后续离线结果只用于生命周期、Oracle 和结构可靠性分析，不声称 counterfactual task success。

## 冻结配置与完整性

- domains：retail、airline，各 tasks `0–4`；
- 每任务 3 trials，共 10 runs / 30 sessions；
- agent/user model：`zai/glm-4.5-air`；
- Full Trajectory、base seed 300、max steps 50；
- user-stop adapter 固定开启，自动重试关闭，单并发；
- 30/30 官方 simulation、30/30 TraceGraph、30/30 非零 token 轨迹；
- infrastructure errors 0、graph validation errors 0、malformed sessions 0；
- 446 个内容寻址 archive objects 全部通过 hash 校验；
- Stage 1 实际 agent+user cost：`$0.1020457`，低于 `$0.30` 保守估算。

运行产物保存在被 Git 忽略的 `outputs/`、`data/processed/` 和 `artifacts/`，不包含在公开仓库中。

## 预注册 gate

| Gate | 阈值 | 结果 | 判定 |
| --- | ---: | ---: | --- |
| task success rate | ≥ 0.50 | 0.40 | **失败** |
| normal stop rate | ≥ 0.90 | 1.00 | 通过 |
| median tool calls | ≥ 5 | 8 | 通过 |
| median estimated trajectory tokens | ≥ 4096 | 82,382 | 通过 |
| infrastructure error rate | ≤ 0.05 | 0.00 | 通过 |

所有 30 次均以官方 `user_stop` 正常结束。失败不是停止协议、轨迹长度或基础设施造成，而是 agent 任务完成能力不足。

## 按域与任务结果

| Domain | Task | Success / 3 |
| --- | ---: | ---: |
| retail | 0 | 1 |
| retail | 1 | 3 |
| retail | 2 | 0 |
| retail | 3 | 0 |
| retail | 4 | 0 |
| airline | 0 | 3 |
| airline | 1 | 0 |
| airline | 2 | 0 |
| airline | 3 | 2 |
| airline | 4 | 3 |

retail 为 `4/15 = 0.2667`，airline 为 `8/15 = 0.5333`。这说明模型可完成部分固定任务，但在 retail 和若干 airline 工具参数/信息要求上不稳定。

## 官方失败诊断

失败原因按官方 evaluator 多标签统计，因此同一 session 可同时出现多种错误：

- read action mismatch：9；总计 159 个期望读动作，正确 150；
- write action mismatch：4；总计 18 个期望写动作，正确 14；
- database mismatch：8；30 次中 DB match 22；
- natural-language assertion mismatch：9；9 个断言均未满足；
- communication mismatch：8；12 个 communication checks 中满足 4。

同一 key 的固定 Function Call probe 证明 `glm-4.5-air` 仍可正常调用；`glm-4.6` 与 `glm-5-turbo` 均返回 HTTP 429 / provider code `1113`（余额不足或无可用资源包），`glm-4.5` 也返回 429。因此当前不能用更强模型重跑 gate，不能把目录可见模型误认为可用额度。

## 30 图离线生命周期与 Oracle

30 图共 999 个节点：30 constraint、60 goal、105 subgoal、358 decision、223 tool call、223 observation。生命周期状态为 Active 244、Consumed 710、Critical Evidence 9、Superseded 5、Audit-required 31；观察到 710 次 `active→consumed`、9 次 `active→critical_evidence` 和 5 次 `active→superseded`。

结构 Oracle：

- mean original tokens：79,659.67；
- mean mandatory input tokens：2,177.73；
- mean compression ratio：0.96478；
- constraint/evidence/unresolved-failure retention：1.0；
- unsafe removal：0；archive recoverability：1.0。

这支持“真实工具轨迹足够长且存在显著安全压缩空间”，但 lifecycle 仍是机器 post-hoc 推断，不是人工 gold。

## 预算 sweep 与 12-manager 结构结果

根据 30 图 mandatory-context 与 manager 实际选择分布：

- 4096：Oracle overflow 0%，但 Full Ours 实际 overflow 100%；
- 8192：Oracle overflow 0%，但 Full Ours 实际 overflow 100%；
- 16384：Oracle overflow 0%，Full Ours 实际 overflow 0%，因此推荐 16384 用于未来 live paired pilot。

在 16384 下，Full Ours mean input tokens 为 14,029.57、mean compression ratio 为 0.77296，constraint/evidence/unresolved-failure retention 均为 1.0，unsafe removal 为 0。对照的结构风险包括：

- Last-k：constraint retention 0，mean unsafe removals 4.57；
- Summary-only：constraint retention 0、evidence retention 0.70、unsafe 7.83；
- LLM-only proxy：constraint retention 0.20、unsafe 3.20；
- AgentDiet-style proxy：constraint retention 0.60、unsafe 1.13；
- ACON-style proxy：evidence retention 0.70、unsafe 1.33；
- 去掉 constraint retention：constraint retention 0.5667、unsafe 0.4333。

这些是 30 图离线结构结果。除 Full Trajectory 外，task success 保持空值；AgentDiet/ACON/LLM-only 仍是 proxy，不能当作论文官方 baseline 结果。论文与官方代码审计、τ³ 接入验收条件见[强 baseline 官方实现审计](STRONG_BASELINES.md)。

## 不可识别项与下一步

30 图没有 tool error 节点、`failed_with`、`retries` 或 `resolves` 边。任务失败来自正确性/参数/communication，而非工具执行异常。因此去掉 failure retention、graph edges 和 lifecycle states 的三个离线 ablation 与 Full Ours 相同，不能支持对应因果假设；Full Trajectory 的 evidence-path preservation 也只有 0.30，说明当前 SUPPORTS-to-final-decision 覆盖仍有限。

下一步硬条件：

1. 给 `glm-4.6` 或更强模型充值/开通资源包，然后用同一 10-task × 3-trial gate 重跑；
2. 只有 Stage 1 成功率达到 0.50 才允许 16384 预算的 live paired manager pilot；
3. 选择含真实工具失败/重试的任务或扩展 benchmark，才能识别 failure/edge/lifecycle ablation；
4. 按[生命周期人工双标协议](LIFECYCLE_ANNOTATION.md)由两位独立标注者完成 120 条 pilot，冻结后计算 Cohen's κ 并裁决分歧；
5. 正式主表前，用论文/官方实现替换三个 proxy baseline。

## 调研报告 10 项计划状态

| 计划 | 当前证据 | 状态 |
| --- | --- | --- |
| 1. 选择 benchmark | 官方 τ³ retail/airline | 完成 |
| 2. 固定 baseline agent | 同一 scaffold，只替换 manager | 完成 |
| 3. 收集完整轨迹 | 30 个真实 Full Trajectory sessions | 完成 |
| 4. 生命周期标签 | 自动状态与双标工具完成，人工 gold 未完成 | 部分完成 |
| 5. 离线生命周期 | 30 图 / 999 节点统计 | 完成（非 gold） |
| 6. Oracle 上界 | 30 图 mean compression 0.96478 | 完成 |
| 7. 在线 manager | 工程/结构验证完成，live paired 被模型 gate 阻止 | 未完成 |
| 8. ablation | 结构运行完成，3 项因无错误事件不可识别 | 部分完成 |
| 9. 强 baseline | proxy 结构运行完成，官方实现未接入 | 部分完成 |
| 10. 阶段性判断 | 有压缩空间；当前模型不适合作主实验 | 完成 |
