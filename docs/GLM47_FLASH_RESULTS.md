# GLM-4.7-Flash 机器生命周期标签实验结果（2026-07-17）

> **版本边界：** 本页后半保留的 `g47f_ml_3t1` 数字来自修复前运行。
> 该运行的官方 reward/termination 仍是真实观测，但压缩条件使用了错误的
> prompt-usage 节点计量，因此不能再作为正式 context-manager 效果结论。
> 修正版 `content_estimate_v2`、Stage 1 重计量和替代矩阵见
> [Token 计量修正](TOKEN_ACCOUNTING.md)。正式在线主结果已替换为
> `g47f_ml_c2`；修复前结果移至本页后半，仅用于历史诊断。

## 结论

`zai/glm-4.7-flash` 已把当前实验从模型适用性 gate 推进到修正版在线 paired matrix：

- 真实 Function Call 探针通过；
- 官方 τ³ mock 端到端 reward `1.0`；
- 10 tasks × 3 trials 的 Full Trajectory Stage 1 通过全部 gate；
- 30 图机器生命周期、Oracle、12-manager 离线结构实验和修正版预算 sweep 完成；
- `content_estimate_v2` 的 2 tasks × 4 conditions corrected smoke 完整运行，8/8 正常停止、0 infrastructure error；
- 10 tasks × 4 conditions 的单 trial preliminary paired pilot 完整运行，40/40 sessions 与 traces 齐全；
- 修复前 10 tasks × 4 conditions × 3 trials 的 raw matrix 完整，但仅保留为诊断；
- 替代的 `g47f_ml_c2` 120-session corrected matrix、双参照统计和生命周期分歧诊断已完成；
- 120/120 TraceGraph、1,768 个 archive objects 与全部 archive references 通过完整性校验；
- provider 报告的实际 agent/user cost 均为 `$0.00`。

生命周期状态仍是规则引擎的 **machine-inferred pseudo labels**，不是人工 gold。修正版三轮矩阵中，Full Ours 相对 Full Trajectory 和 no-lifecycle 的点估计均为正，但成功率差的 95% CI 都跨 0；估算 selected-context 相对 Full Trajectory 的下降区间低于 0，真实 provider input token 差的区间仍跨 0。因此当前结果证明机器伪标签路径可以稳定跑通并给出值得复验的正向信号，但不构成 H2/H3 已成立、性能显著提升或人工语义有效性的证据。

## 可用性与最小端到端验证

直接 API 探针使用模型名 `glm-4.7-flash`，成功产生一次指定 Function Call：

- prompt tokens：190；
- completion tokens：12；
- total tokens：202；
- finish reason：`tool_calls`。

τ³ 使用 LiteLLM 路由名 `zai/glm-4.7-flash`。`mock/create_task_1` 结果：

- reward：`1.0`；
- DB check：`1.0`；
- write action：`1/1`；
- termination：`user_stop`；
- agent/user cost：均为 `$0.00`；
- TraceGraph 与 archive hash：全部有效。

## Stage 1：10 tasks × 3 trials

冻结配置：`configs/glm47_flash_full_trajectory_stage1_v1.json`。

| Gate | 阈值 | 结果 | 判定 |
| --- | ---: | ---: | --- |
| task success rate | ≥ 0.50 | `16/30 = 0.5333` | 通过 |
| normal stop rate | ≥ 0.90 | `27/30 = 0.9000` | 通过 |
| median tool calls | ≥ 5 | `7` | 通过 |
| median estimated trajectory tokens | ≥ 4096 | `4,875.0` | 通过 |
| infrastructure error rate | ≤ 0.05 | `0/30 = 0` | 通过 |

完整性：

- 10/10 runs、30/30 simulations、30/30 traces；
- graph validation errors：0；
- zero-token traces：0；
- malformed sessions：0；
- 合并 archive objects：440，全部通过 hash 校验；
- termination：27 次 `user_stop`、3 次 `max_steps`；
- 实际成本：`$0.00`。

按域结果：

| Domain | Success | Normal stop | Median tool calls | Median trajectory tokens |
| --- | ---: | ---: | ---: | ---: |
| retail | `6/15 = 0.4000` | `0.9333` | 10 | 5,450 |
| airline | `10/15 = 0.6667` | `0.8667` | 7 | 4,312 |

按任务成功数：

| Domain | Task 0 | Task 1 | Task 2 | Task 3 | Task 4 |
| --- | ---: | ---: | ---: | ---: | ---: |
| retail | 2/3 | 1/3 | 1/3 | 0/3 | 2/3 |
| airline | 3/3 | 0/3 | 2/3 | 3/3 | 2/3 |

失败诊断仍以官方 evaluator 为准：database mismatch 11、write mismatch 7、read mismatch 6、abnormal termination 3、natural-language assertion mismatch 1、communication mismatch 1。同一 session 可同时出现多种原因。

## 机器生命周期与离线结构结果

30 图共 934 个节点，机器推断状态为：

- Active 287；
- Consumed 574；
- Critical Evidence 16；
- Superseded 25；
- Audit-required 31；
- Unresolved Failure 1。

观察到的状态转移：

- `active→consumed`：574；
- `active→critical_evidence`：16；
- `active→superseded`：25。

结构 Oracle：

- mean original content tokens：5,173.5；
- mean mandatory input tokens：2,258.7；
- mean compression ratio：0.5066；
- constraint/evidence/unresolved-failure retention：均为 1.0；
- unsafe removal：0；
- archive recoverability：1.0。

4096 下 Full Ours 的 mean input 为 3,820.1、mean compression 为 0.1921；no-lifecycle 与 no-failure-retention 在这批只有一个 Unresolved Failure 的图上得到相同离线结构值，说明普通 Stage 1 任务不足以识别 failure/lifecycle 消融。Last-k 的 mean input 为 1,201.2、compression 为 0.7403，但 constraint retention 仅 0.0333、unsafe removal 平均 5.9。

这些结果证明轨迹中存在可被规则捕获的状态变化和结构压缩空间；它们尚不能证明自动状态与人类语义判断一致，也不能把离线 view 解释为反事实 task success。

## 预算选择

`content_estimate_v2` 对 30 图的 sweep：

| Budget | Mandatory overflow | Full Ours overflow | Mean input tokens | Mean compression | Unsafe removals | Feasible |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2048 | 19/30 | 30/30 | 2,580.8 | 0.4423 | 0 | 否 |
| 4096 | 0/30 | 0/30 | 3,820.1 | 0.1921 | 0 | 是 |
| 8192 | 0/30 | 0/30 | 4,829.2 | 0.0303 | 0 | 是 |
| 12288 | 0/30 | 0/30 | 4,829.2 | 0.0303 | 0 | 是 |
| 16384 | 0/30 | 0/30 | 4,829.2 | 0.0303 | 0 | 是 |

4096 是最小结构可行预算：mandatory context 和 Full Ours 都无溢出，Constraint、Unresolved Failure、Evidence retention 的最小值均为 1.0，unsafe removal 为 0。8192 以上几乎不再产生有意义压缩，因此 corrected matrix 固定 4096。

## 在线 paired pipeline smoke

冻结配置：`configs/glm47_flash_machine_lifecycle_paired_smoke_v2.json`，matrix ID 为 `g47f_ml_s3`。

范围：

- tasks：retail/0、airline/0；
- conditions：Full Trajectory、Last-k、Ours without lifecycle states、Full Ours；
- 每条件每任务 1 trial，共 8 sessions；
- budget：压缩条件均为 4096；
- token accounting：`content_estimate_v2`；
- run 间固定冷却 20 秒；
- user-stop adapter、seed、temperature、模型和工具集合跨条件固定。

完整性：

- 8/8 simulations、8/8 traces；
- 8/8 正常 `user_stop`；
- infrastructure errors、graph errors、zero-token traces：均为 0；
- 每个 session 的 archive hash 全部有效，共 62 个 archive objects；
- 8/8 traces 与 context views 均记录 `content_estimate_v2`；
- 实际成本：`$0.00`。

| Manager | Success | Estimated selected tokens | Actual agent input tokens | Actual input / call | Mean context compression |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full Trajectory | 1/2 | 19,068.0 | 41,143.5 | 6,329.8 | 0.0000 |
| Last-k | 1/2 | 12,452.0 | 42,582.5 | 5,009.7 | 0.4375 |
| Ours without lifecycle | 1/2 | 27,303.5 | 62,998.0 | 6,999.8 | 0.0500 |
| Full Ours | 1/2 | 23,533.0 | 53,176.5 | 6,256.1 | 0.0186 |

四个条件在两个任务上均为 retail 失败、airline 成功，因此 success delta 都是 0。Last-k 每次 agent 调用的实际输入相对 Full Trajectory 平均少约 1,356 tokens，但累计实际输入差为 +1,439，因为它产生了更多 agent calls。Full Ours 每次调用少约 203 tokens，累计差为 +12,033，同样受到轨迹长度影响。smoke 只验证双口径统计和管线完整性，不判断方法优劣。

## 修正版 10-task × 3-trial paired matrix

冻结配置：`configs/glm47_flash_machine_lifecycle_paired_3trial_v2.json`，matrix ID 为 `g47f_ml_c2`。

范围：

- tasks：retail/0–4、airline/0–4；
- conditions：Full Trajectory、Last-k、Ours without lifecycle states、Full Ours；
- 每条件每任务 3 trials，共 120 sessions；
- 压缩预算：4096；
- token accounting：`content_estimate_v2`；
- 跨 run 冷却 20 秒，timeout 900 秒；
- model、task、trial seed、user-stop adapter 与工具集合跨条件固定。

完整性：

- 40/40 runs、120/120 simulations、120/120 traces；
- 120/120 TraceGraph schema validation 通过；
- 120/120 session archives 通过 hash 校验，共 1,768 个 archive objects；
- graph validation errors、missing archive references、zero-token traces、malformed sessions：均为 0；
- provider input usage：120/120 traces 有记录，其中 2 个 timeout 会话按预注册规则从 provider 聚合和配对效果统计中排除；
- termination：107 次 `user_stop`、11 次 `max_steps`、2 次 wall-clock `timeout`；
- 日志中 `RateLimitError`、非法 messages、traceback、runtime token-accounting mismatch：均为 0；
- 实际成本：`$0.00`。

分析器将 2 次 wall-clock timeout 归入 `infrastructure_errors` 以便预注册排除；它们分别来自 `retail/3` 和 `airline/2` 的 Full Ours trial。这里的“基础设施型中止”是统计分类，不表示 GLM API 不可用。两次超时前后模型请求都正常返回，整个矩阵没有 provider/API 错误。

条件指标：

| Manager | Raw success | Evaluated success | Pass^1 | Pass^2 | Pass^3 | Mean selected context | Mean actual agent input | Actual input / call |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Trajectory | 13/30 | 13/30 = 0.4333 | 0.4333 | 0.1667 | 0.1000 | 40,028.1 | 79,510.9 | 7,362.1 |
| Last-k | 13/30 | 13/30 = 0.4333 | 0.4333 | 0.2667 | 0.2000 | 13,698.6 | 58,436.3 | 4,589.2 |
| Ours without lifecycle | 14/30 | 14/30 = 0.4667 | 0.4667 | 0.2667 | 0.2000 | 30,354.7 | 65,350.3 | 6,783.8 |
| Full Ours | 17/30 | 17/28 = 0.6071 | 0.6167 | 0.4000 | — | 27,310.3 | 58,726.5 | 6,630.4 |

Full Ours 的 Pass^1 是先在每个任务内按可评估 trial 计算、再跨任务平均，因此不等于直接用 `17/28` 相除。两个任务各排除一个 timeout，最小可评估 trial 数为 2，故不报告 Pass^3。

相对 Full Trajectory 的配对结果：

| Comparator | Eligible / excluded | Both / reference-only / comparator-only / neither | Success delta | 95% CI | McNemar p | Holm p | Selected-context delta | 95% CI | Actual-input delta | 95% CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Last-k | 30 / 0 | 7 / 6 / 6 / 11 | 0.0000 | [-0.2333, 0.2333] | 1.0000 | 1.0000 | -26,329.6 | [-40,473.6, -14,796.9] | -21,074.6 | [-42,681.8, -3,175.0] |
| Ours without lifecycle | 30 / 0 | 6 / 7 / 8 / 9 | +0.0333 | [-0.2333, 0.3000] | 1.0000 | 1.0000 | -9,673.5 | [-24,381.1, 2,975.7] | -14,160.6 | [-39,327.7, 7,719.7] |
| Full Ours | 28 / 2 | 10 / 3 / 7 / 8 | +0.1429 | [-0.0714, 0.3571] | 0.3438 | 1.0000 | -9,306.2 | [-20,662.6, -139.9] | -13,533.7 | [-32,678.8, 2,630.5] |

Full Ours 相对 Full Trajectory 的成功率点估计为 `+14.3` 个百分点，估算 selected-context 的配对区间低于 0；但成功率区间与真实 provider input token 区间都跨 0。Last-k 在成功率持平的同时显著降低了本轮实际 provider input，这表明一个强的非结构 baseline 仍不可省略。

机器生命周期标签的直接消融以 Ours without lifecycle states 为参考，在排除两个 Full Ours timeout 后：

- both success / no-lifecycle only / Full Ours only / neither：`10 / 2 / 7 / 9`；
- Full Ours success delta：`+0.1786`；
- success delta 95% bootstrap CI：`[-0.0357, 0.3929]`；
- exact McNemar：`p=0.1797`，Holm-adjusted `p=0.5391`；
- mean selected-context token delta：`-3,692.6`，95% CI `[-11,839.4, 2,547.2]`；
- mean actual agent input token delta：`-5,991.9`，95% CI `[-20,838.3, 6,102.6]`。

该点估计与修复前矩阵方向相反，说明旧预算口径确实不能外推到修正版。当前更准确的结论是“观察到正向、但不显著的机器生命周期信号”；下一步必须用人工 gold 和更大 paired 样本验证，不能把它写成已证实的独立贡献。

按域的 raw success：

| Domain | Full Trajectory | Last-k | No-lifecycle | Full Ours |
| --- | ---: | ---: | ---: | ---: |
| retail | 6/15 | 4/15 | 6/15 | 8/15 |
| airline | 7/15 | 9/15 | 8/15 | 9/15 |

修正版分歧诊断覆盖 30/30 个 no-lifecycle/Full Ours 原始配对：11 个成功分歧、4 个包含失败信号、0 个 `retries` 边、0 个 `resolves` 边，入选 12 条优先 Full Ours trace。普通 0–4 任务仍不足以识别 H4，failure-retention 结论必须等待单独的 failure-rich 矩阵。

## 修复前 paired 结果（仅诊断）

## Failure-retention corrected matrix: `g47f_fr_c2`

冻结配置：`configs/glm47_flash_failure_retention_paired_3trial_v2.json`。该矩阵使用官方 GPT-4.1 历史结果选出的 10 个 failure-rich 任务，每个任务 3 trials，比较 Full Trajectory、Ours without failure retention 与 Full Ours，共 90 sessions。

完整性：

- 30/30 runs、90/90 simulations、90/90 traces 完成；
- 90/90 TraceGraph validation 通过；
- 1,460 个 session archive objects hash 校验通过；
- missing raw archive references、zero-token traces、malformed sessions、graph validation errors 均为 0；
- termination：70 `user_stop`、7 `max_steps`、11 `timeout`、2 `too_many_errors`；
- 日志中未出现 RateLimitError、Traceback、非法 messages 或 token-accounting runtime mismatch；
- 有 9 条 output-token 上限警告、31 条 airline seat-release benchmark 工具警告。

条件指标：

| Manager | Raw success | Evaluated success | Infrastructure errors | Mean selected context | Mean actual agent input | Actual input / call |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Trajectory | 10/30 | 10/24 = 0.4167 | 6/30 | 40,208.3 | 80,762.8 | 7,074.1 |
| Ours without failure retention | 4/30 | 4/27 = 0.1481 | 3/30 | 46,712.4 | 111,552.7 | 7,722.9 |
| Full Ours | 7/30 | 7/28 = 0.2500 | 2/30 | 44,563.3 | 101,138.0 | 7,571.8 |

相对 Full Trajectory：

| Comparator | Eligible / excluded | Both / reference-only / comparator-only / neither | Success delta | 95% CI | McNemar p | Selected-context delta | Actual-input delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Ours without failure retention | 22 / 8 | 3 / 6 / 1 / 12 | -0.2273 | [-0.4545, 0.0000] | 0.1250 | +8,362.6 | +27,508.9 |
| Full Ours | 23 / 7 | 6 / 4 / 1 / 12 | -0.1304 | [-0.3043, 0.0435] | 0.3750 | +1,597.0 | +12,851.7 |

直接检验 failure retention 的消融，即 Full Ours 相对 Ours without failure retention：

- eligible / excluded pairs：25 / 5；
- both / no-failure-only / Full-Ours-only / neither：4 / 0 / 2 / 19；
- success delta：+0.0800；
- 95% bootstrap CI：[0.0000, 0.2000]；
- exact McNemar：p = 0.5000；
- mean selected-context token delta：-1,596.1，95% CI [-12,300.2, 8,662.5]；
- mean actual agent input token delta：-1,513.8，95% CI [-24,108.8, 21,278.1]。

解释边界：在 failure-rich 任务上，failure retention 相对去掉 failure retention 的版本有正向点估计，但只有 2 个 Full-Ours-only 成功配对，统计证据很弱；同时 Full Ours 仍低于 Full Trajectory，说明当前失败保留策略没有在 τ³ + GLM-4.7-Flash 组合下形成 H4 所需的强证据。更关键的是，全矩阵只有 2 条真实 `retries` 边、0 条 `resolves` 边，Full Ours 条件下没有 retry edge。因此当前结果不能声称“失败负证据减少重复失败调用”，只能说该任务选择确实提高了 Error/failed_with 覆盖，但 retry/resolve 机制仍不可识别。

执行风险：一条 Full Ours session 的 wall-clock duration 达到 13,229.3s，明显超过配置的 900s timeout，说明上游 timeout 只在响应边界检查，长模型响应会造成超时延迟。该问题影响运行时间和基础设施错误解释，但不表示 GLM API 不可用。

## 10-task preliminary paired pilot

冻结配置：`configs/glm47_flash_machine_lifecycle_paired_pilot_v1.json`，matrix ID 为 `g47f_ml_p2`。

范围：

- tasks：retail/0–4、airline/0–4；
- conditions：Full Trajectory、Last-k、Ours without lifecycle states、Full Ours；
- 每条件每任务 1 trial，共 40 sessions；
- 压缩预算：16384；
- run 间固定冷却：20 秒；
- timeout：每 session 900 秒；
- model、task、base seed、user-stop adapter 与工具集合跨条件固定。

完整性：

- 40/40 runs、40/40 simulations、40/40 traces；
- 40/40 TraceGraph schema validation 通过；
- 40/40 session archives 通过 hash 校验，共 490 个 archive objects；
- graph validation errors、zero-token traces、malformed sessions：均为 0；
- termination：34 次 `user_stop`、4 次 `max_steps`、2 次 `timeout`；
- 精确 `infrastructure_error`：0；分析器将 2 次 wall-clock timeout 按基础设施型中止处理，并从相应配对中排除；
- 实际成本：`$0.00`。

原始条件指标：

| Manager | Success | Evaluated sessions | Normal stop | Mean total selected-context tokens | Mean per-turn selected tokens | Mean context compression |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Trajectory | 4/10 | 8 | 8/8 | 199,808.1 | 20,610.0 | 0.0000 |
| Last-k | 6/10 | 10 | 9/10 | 93,884.0 | 9,472.6 | 0.3067 |
| Ours without lifecycle | 4/10 | 10 | 8/10 | 151,153.6 | 11,563.5 | 0.3462 |
| Full Ours | 5/10 | 10 | 9/10 | 131,204.5 | 11,590.1 | 0.3541 |

Full Trajectory 的 task success rate 按 8 个非基础设施会话计算为 `4/8 = 0.50`。表中的累计 selected-context tokens 同时受轨迹长度和停止原因影响，因此效果判断以相同 task 的配对差为主。

相对 Full Trajectory 的配对结果：

| Comparator | Eligible / excluded pairs | Success delta | 95% bootstrap CI | McNemar p | Mean selected-token delta | 95% bootstrap CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Last-k | 8 / 2 | +0.125 | [0.000, 0.375] | 1.000 | -61,031.8 | [-102,957.0, -8,832.4] |
| Ours without lifecycle | 8 / 2 | -0.125 | [-0.500, 0.250] | 1.000 | -5,157.5 | [-84,732.6, 85,108.0] |
| Full Ours | 8 / 2 | 0.000 | [-0.375, 0.375] | 1.000 | -27,146.4 | [-74,208.9, 29,250.5] |

Full Ours 对 Full Trajectory 的 8 个有效配对中：3 个同时成功、1 个仅基线成功、1 个仅 Full Ours 成功、3 个同时失败。因此当前数据只支持“在该小样本中成功率持平，并观察到平均上下文减少”，不支持统计显著的成功率或 token 改善。

机器生命周期标签的直接消融配对以 Ours without lifecycle states 为参考，覆盖全部 10 个任务：

- Full Ours success delta：`+0.10`；
- both success / no-lifecycle only / Full Ours only / neither：`4 / 0 / 1 / 5`；
- exact McNemar：`p=1.0`；
- success delta 95% bootstrap CI：`[0.0, 0.3]`；
- mean selected-context token delta：`-19,949.1`；
- token delta 95% bootstrap CI：`[-86,287.4, 25,193.5]`。

这表明机器生命周期标签在本轮比无生命周期消融多成功 1 个任务，但样本量和 trial 数不足，token 区间也跨 0；它是下一轮需要验证的信号，不是已证实的独立贡献。该单 trial 信号没有在下面的三轮矩阵中复现。

## 10-task × 3-trial paired matrix

冻结配置：`configs/glm47_flash_machine_lifecycle_paired_3trial_v1.json`，matrix ID 为 `g47f_ml_3t1`。

范围：

- tasks：retail/0–4、airline/0–4；
- conditions：Full Trajectory、Last-k、Ours without lifecycle states、Full Ours；
- 每条件每任务 3 trials，共 120 sessions；
- 压缩预算：16384；
- run 间固定冷却：20 秒；
- timeout：每 session 900 秒；
- model、task、trial seed、user-stop adapter 与工具集合跨条件固定。

完整性：

- 40/40 runs、120/120 simulations、120/120 traces；
- 120/120 TraceGraph schema validation 通过；
- 120/120 session archives 通过 hash 校验，共 1,660 个 archive objects；其中 4 个空 archive 对应无工具原始 payload 的有效会话；
- graph validation errors、zero-token traces、malformed sessions：均为 0；
- infrastructure error 与 wall-clock timeout：均为 0；
- termination：106 次 `user_stop`、11 次 `max_steps`、3 次 `too_many_errors`；
- 实际成本：`$0.00`。

条件指标：

| Manager | Success | Pass^1 | Pass^2 | Pass^3 | Normal stop | Mean total selected-context tokens | Mean per-turn selected tokens | Mean compression |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Trajectory | 18/30 = 0.6000 | 0.6000 | 0.4000 | 0.3000 | 0.9333 | 378,240.0 | 28,097.5 | 0.0000 |
| Last-k | 12/30 = 0.4000 | 0.4000 | 0.2333 | 0.1000 | 0.7667 | 128,449.6 | 9,505.4 | 0.4084 |
| Ours without lifecycle | 17/30 = 0.5667 | 0.5667 | 0.4667 | 0.4000 | 0.8667 | 130,615.9 | 11,399.0 | 0.3015 |
| Full Ours | 15/30 = 0.5000 | 0.5000 | 0.3000 | 0.2000 | 0.9667 | 122,834.9 | 11,556.7 | 0.3040 |

这里的 Pass^k 使用按任务组合估计 `C(c_i,k)/C(n_i,k)` 后再跨任务平均；基础设施型中止从 `n_i` 排除。本矩阵每个任务均有 3 个有效 trial。

相对 Full Trajectory 的 30 个完整配对：

| Comparator | Both / reference-only / comparator-only / neither | Success delta | 95% bootstrap CI | McNemar p | Holm p | Mean selected-token delta | 95% bootstrap CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Last-k | 9 / 9 / 3 / 9 | -0.2000 | [-0.4333, 0.0333] | 0.1460 | 0.4380 | -249,790.4 | [-483,175.1, -76,944.8] |
| Ours without lifecycle | 10 / 8 / 7 / 5 | -0.0333 | [-0.3000, 0.2333] | 1.0000 | 1.0000 | -247,624.1 | [-465,821.5, -93,669.6] |
| Full Ours | 11 / 7 / 4 / 8 | -0.1000 | [-0.3000, 0.1000] | 0.5488 | 1.0000 | -255,405.1 | [-475,164.9, -99,274.1] |

三种压缩条件的 selected-context token 差置信区间均完全低于 0，说明在本矩阵上确实减少了累计发送上下文。成功率差的置信区间均跨 0，Holm 校正后也无显著比较。Full Ours 比 Full Trajectory 少约 67.5% 的 mean cumulative selected-context tokens，但成功数少 3/30，不能解释为性能保持或提升。

机器生命周期标签的直接消融以 no-lifecycle 为参考：

- both success / no-lifecycle only / Full Ours only / neither：`9 / 8 / 6 / 7`；
- Full Ours success delta：`-0.0667`；
- success delta 95% bootstrap CI：`[-0.3000, 0.1667]`；
- exact McNemar：`p=0.7905`，Holm-adjusted `p=1.0`；
- mean selected-context token delta：`-7,781.0`；
- token delta 95% bootstrap CI：`[-32,891.5, 15,733.1]`。

机器生命周期标签在三轮矩阵中比 no-lifecycle 少成功 2/30，成功率与 token 差的区间都跨 0。当前证据不支持生命周期伪标签的独立贡献；在四个条件中，no-lifecycle 是成功率最高的压缩条件，并取得最高的 Pass^3 `0.40`。

按域观察到明显不对称：

| Domain | Full Trajectory | No-lifecycle | Full Ours |
| --- | ---: | ---: | ---: |
| retail | 7/15 = 0.4667 | 8/15 = 0.5333 | 6/15 = 0.4000 |
| airline | 11/15 = 0.7333 | 9/15 = 0.6000 | 9/15 = 0.6000 |

Full Ours 与 no-lifecycle 在 airline 同为 9/15，差异主要来自 retail。下一轮应优先把 retail 中两条件分歧的节点送入人工盲标，并选择包含真实工具失败/重试的任务检查标签规则，而不是继续扩大机器标签的性能主张。

## 执行中发现并修复的问题

第一次 paired 尝试暴露两个基础设施问题，均与正式结果隔离：

1. 长 matrix ID 触发 Windows archive 临时文件路径过长；正式 ID 缩短后路径估算为 227 字符以内。
2. Last-k 可能让压缩历史以 assistant/tool 开头，GLM 返回 `messages 参数非法`。运行时现在会：
   - 闭包 assistant tool call 与全部 tool results；
   - 在压缩切片前补入最近的 user anchor；
   - 在 context-view metadata 中记录实际 selected message ordinals/roles。

原故障条件 `retail/0 + last_k` 单独复验后正常 `user_stop`，随后 8-session 新矩阵无非法消息。

免费端点还曾短暂返回“该模型当前访问量过大”。正式 smoke 固定 20 秒跨 run 冷却并取得 0 infrastructure error；全量矩阵仍需预注册限流排除与显式重跑规则。

## 可复现命令

```powershell
python scripts/plan_glm_matrix.py `
  --config configs/glm47_flash_full_trajectory_stage1_v1.json `
  --output outputs/plans/g47f_s1_v1.json `
  --execute `
  --max-estimated-cost-usd 0.01

python scripts/analyze_glm_stage1.py `
  --plan outputs/plans/g47f_s1_v1.json `
  --results-root vendor/tau3-bench/data/simulations `
  --output outputs/stage1_analysis/g47f_s1_v1 `
  --graph-output data/processed/g47f_s1_v1_graphs `
  --archive-output artifacts/g47f_s1_v1_archive

python scripts/run_budget_sweep.py `
  --input data/processed/g47f_s1_v1_graphs `
  --archive artifacts/g47f_s1_v1_archive `
  --output outputs/budget_sweep/g47f_s1_v1 `
  --budgets 4096 8192 16384

python scripts/plan_glm_matrix.py `
  --config configs/glm47_flash_machine_lifecycle_paired_smoke_v1.json `
  --output outputs/plans/g47f_ml_sm2.json `
  --execute `
  --max-estimated-cost-usd 0.01

python scripts/analyze_live_matrix.py `
  --plan outputs/plans/g47f_ml_sm2.json `
  --results-root vendor/tau3-bench/data/simulations `
  --output outputs/live_matrix_analysis/g47f_ml_sm2

python scripts/plan_glm_matrix.py `
  --config configs/glm47_flash_machine_lifecycle_paired_pilot_v1.json `
  --output outputs/plans/g47f_ml_p2.json `
  --execute `
  --max-estimated-cost-usd 0.01

python scripts/analyze_live_matrix.py `
  --plan outputs/plans/g47f_ml_p2.json `
  --results-root vendor/tau3-bench/data/simulations `
  --output outputs/live_matrix_analysis/g47f_ml_p2 `
  --reference-manager full_trajectory `
  --bootstrap-samples 10000 `
  --bootstrap-seed 300

python scripts/plan_glm_matrix.py `
  --config configs/glm47_flash_machine_lifecycle_paired_3trial_v1.json `
  --output outputs/plans/g47f_ml_3t1.json `
  --execute `
  --max-estimated-cost-usd 0.012

python scripts/analyze_live_matrix.py `
  --plan outputs/plans/g47f_ml_3t1.json `
  --results-root vendor/tau3-bench/data/simulations `
  --output outputs/live_matrix_analysis/g47f_ml_3t1 `
  --reference-manager full_trajectory `
  --bootstrap-samples 10000 `
  --bootstrap-seed 300

python scripts/analyze_live_matrix.py `
  --plan outputs/plans/g47f_ml_3t1.json `
  --results-root vendor/tau3-bench/data/simulations `
  --output outputs/live_matrix_analysis/g47f_ml_3t1_lifecycle_reference `
  --reference-manager ours_without_lifecycle_states `
  --bootstrap-samples 10000 `
  --bootstrap-seed 300
```

所有 raw results、traces、archives、plans 和分析输出继续位于 Git 忽略目录，不发布到公开仓库。

## 下一步

1. 将 `outputs/annotations/g47f_s1_v1/annotator_a.csv` 和 `annotator_b.csv` 分别交给两位独立标注者，完成 120 条 blind pilot、Cohen's κ 和第三方裁决；
2. 用人工 gold 计算自动 lifecycle 的混淆矩阵，并优先分析 retail 中 Full Ours/no-lifecycle 的分歧；
3. 增加含真实工具失败/重试的任务，识别 failure/edge/lifecycle ablation；
4. 标签规则验证后再扩到每域 10–20 tasks；
5. 在形成论文主表前，用正式 LLM scorer/论文官方实现替换 proxy baselines。
