# GLM-4.7-Flash 机器生命周期标签实验结果（2026-07-16）

## 结论

`zai/glm-4.7-flash` 已把当前实验从模型适用性 gate 推进到在线 context-manager pipeline smoke：

- 真实 Function Call 探针通过；
- 官方 τ³ mock 端到端 reward `1.0`；
- 10 tasks × 3 trials 的 Full Trajectory Stage 1 通过全部 gate；
- 30 图机器生命周期、Oracle、12-manager 离线结构实验和预算 sweep 完成；
- 2 tasks × 4 conditions 的在线 paired smoke 完整运行，8/8 正常停止、0 infrastructure error；
- 10 tasks × 4 conditions 的单 trial preliminary paired pilot 完整运行，40/40 sessions 与 traces 齐全；
- provider 报告的实际 agent/user cost 均为 `$0.00`。

生命周期状态仍是规则引擎的 **machine-inferred pseudo labels**，不是人工 gold。本文档只报告 preliminary/pipeline 结果；10-task pilot 只有一个 trial，且 Full Trajectory 有两个 wall-clock timeout，不把这些结果解释为已验证的性能提升。

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
| median estimated trajectory tokens | ≥ 4096 | `41,282.5` | 通过 |
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
| retail | `6/15 = 0.4000` | `0.9333` | 10 | 48,747 |
| airline | `10/15 = 0.6667` | `0.8667` | 7 | 36,578 |

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

- mean original tokens：64,377.33；
- mean mandatory input tokens：2,233.70；
- mean compression ratio：0.9440；
- constraint/evidence/unresolved-failure retention：均为 1.0；
- unsafe removal：0；
- archive recoverability：1.0。

这些结果证明轨迹中存在可被规则捕获的状态变化和结构压缩空间；它们尚不能证明自动状态与人类语义判断一致。

## 预算选择

新 30 图的 4096/8192/16384 sweep：

| Budget | Full Ours overflow | Mean input tokens | Mean compression | Unsafe removals | Feasible |
| ---: | ---: | ---: | ---: | ---: | --- |
| 4096 | 30/30 | 11,248.53 | 0.7466 | 0 | 否 |
| 8192 | 25/30 | 11,261.50 | 0.7459 | 0 | 否 |
| 16384 | 1/30 | 14,159.80 | 0.6489 | 0 | 是 |

16384 的 overflow rate 为 `3.33%`，低于冻结的 `5%` 上限，因此继续作为在线 pilot 预算。

## 在线 paired pipeline smoke

冻结配置：`configs/glm47_flash_machine_lifecycle_paired_smoke_v1.json`。

范围：

- tasks：retail/0、airline/0；
- conditions：Full Trajectory、Last-k、Ours without lifecycle states、Full Ours；
- 每条件每任务 1 trial，共 8 sessions；
- budget：压缩条件均为 16384；
- run 间固定冷却 20 秒；
- user-stop adapter、seed、temperature、模型和工具集合跨条件固定。

完整性：

- 8/8 simulations、8/8 traces；
- 8/8 正常 `user_stop`；
- infrastructure errors、graph errors、zero-token traces：均为 0；
- 每个 session 的 archive hash 全部有效；
- 实际成本：`$0.00`。

| Manager | Success | Mean total selected-context tokens | Mean per-turn selected tokens | Mean context compression |
| --- | ---: | ---: | ---: | ---: |
| Full Trajectory | 2/2 | 108,879.5 | 16,116.8 | 0.0000 |
| Last-k | 2/2 | 79,145.5 | 9,501.0 | 0.2247 |
| Ours without lifecycle | 1/2 | 58,531.0 | 9,457.4 | 0.2074 |
| Full Ours | 1/2 | 55,468.5 | 9,814.7 | 0.1876 |

相对 Full Trajectory，Full Ours 平均少发送 53,411 selected-context tokens；但 paired success delta 为 `-0.5`，exact McNemar `p=1.0`。只有两个 task，置信区间极宽，不能据此判断压缩方法优劣。Full Ours 与 no-lifecycle 都是 1/2，也不能支持 lifecycle 的独立因果贡献。

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

这表明机器生命周期标签在本轮比无生命周期消融多成功 1 个任务，但样本量和 trial 数不足，token 区间也跨 0；它是下一轮需要验证的信号，不是已证实的独立贡献。

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
```

所有 raw results、traces、archives、plans 和分析输出继续位于 Git 忽略目录，不发布到公开仓库。

## 下一步

1. 将已完成的 10-task single-trial preliminary pilot 扩大到至少 3 trials，并保留 run 间冷却、wall-clock timeout exclusion 和配对分析；
2. 增加含真实工具失败/重试的任务，识别 failure/edge/lifecycle ablation；
3. 完成两位独立标注者的 120 条 blind pilot、Cohen's κ 和第三方裁决；
4. 用人工 gold 计算自动 lifecycle 的混淆矩阵；
5. 在形成论文主表前，用正式 LLM scorer/论文官方实现替换 proxy baselines。
