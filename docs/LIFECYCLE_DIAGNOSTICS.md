# 生命周期分歧诊断与 failure-rich 任务选择

> `g47f_ml_3t1` 后续被发现使用了错误的 prompt-usage 节点计量。下文的
> 分歧/Error 覆盖仍可用于挑选人工标注样本，因为它读取真实 reward 与图结构；
> 但任何成功率或 token 差都只能视为修复前诊断，不能用于方法效果结论。
> 修正版见 [Token 计量修正](TOKEN_ACCOUNTING.md)。

本页记录机器生命周期标签三轮矩阵之后的诊断步骤。目标不是从同一批结果继续扩大性能主张，而是回答两个更基础的问题：

1. Full Ours 与 no-lifecycle 的差异集中在哪里，人工应该优先标哪些节点？
2. 当前任务是否真的包含足够的工具失败、重试和修复，能够识别 failure retention 的作用？

## 修正版三轮矩阵分歧诊断

正式诊断输入已替换为 `g47f_ml_c2` 的 30 个
`Ours without lifecycle states` / `Full Ours` task+trial 原始配对。性能推断使用
paired 分析器排除两个 Full Ours timeout 后的 28 个有效配对；错误分析仍读取
30 条已落盘 trace，以便 timeout 之前发生的状态和工具行为不被丢弃。

结果：

- 原始配对：30/30，缺失 0；
- raw success 分歧：11/30；
- 出现任何 Error/`failed_with`/`retries`/`resolves` 信号：4/30；
- 出现真实 `retries` 边：0/30；
- 出现真实 `resolves` 边：0/30；
- Full Ours 中存在 selected Error item 的配对：4/30；
- 入选后续优先检查的 Full Ours traces：12。

在 28 个性能有效配对中，both success / no-lifecycle only / Full Ours only /
neither 为 `10 / 2 / 7 / 9`，Full Ours success delta 为 `+0.1786`，
95% bootstrap CI `[-0.0357, 0.3929]`，exact McNemar `p=0.1797`。它是正向但
未显著的机器标签信号，不能代替人工 gold。

修正版按任务的 raw 分歧：

| Task | No-lifecycle success | Full Ours success | 分歧 trials | 含失败信号 trials |
| --- | ---: | ---: | ---: | ---: |
| retail/0 | 1/3 | 1/3 | 0 | 0 |
| retail/1 | 1/3 | 2/3 | 1 | 1 |
| retail/2 | 0/3 | 2/3 | 2 | 1 |
| retail/3 | 3/3 | 2/3 | 1 | 2 |
| retail/4 | 1/3 | 1/3 | 2 | 0 |
| airline/0 | 2/3 | 3/3 | 1 | 0 |
| airline/1 | 0/3 | 0/3 | 0 | 0 |
| airline/2 | 1/3 | 1/3 | 2 | 0 |
| airline/3 | 2/3 | 2/3 | 2 | 0 |
| airline/4 | 3/3 | 3/3 | 0 | 0 |

普通 0–4 任务中仍没有 retry/resolve 边，且大部分成功分歧没有失败信号。
因此本矩阵可以用于检查生命周期选择，但不能识别 H4 的失败保留作用。

可复现命令：

```powershell
python scripts/analyze_lifecycle_disagreements.py `
  --report outputs/live_matrix_analysis/g47f_ml_c2_lifecycle_reference/live_matrix_report.json `
  --output outputs/lifecycle_diagnostics/g47f_ml_c2 `
  --reference-manager ours_without_lifecycle_states `
  --comparator-manager full_ours
```

修正版 12 条优先 trace 已导出为
`outputs/annotations/g47f_ml_c2_targeted/`：两位标注者各 120 条，均不包含机器
预测；隔离 key 中的机器状态为 Active 50、Consumed 50、Audit-required 10、
Critical Evidence 6、Unresolved Failure 3、Superseded 1。修复前
`g47f_ml_3t1_targeted` 仍可用于历史机器标签错误分析，但不能与修正版性能差
建立因果对应，两套包也不能合并计算一个 κ。

## 修复前三轮矩阵分歧诊断（仅用于旧盲标包来源）

### 输入与结果

输入为 `g47f_ml_3t1` 的 30 个 `Ours without lifecycle states` / `Full Ours` task+trial 配对。分析器读取官方成功结果与两侧 TraceGraph，统计 Error 节点、`failed_with`、`retries`、`resolves`、生命周期状态和 selected-context token 差。

结果：

- 完整配对：30/30；
- 成功结果分歧：14/30；
- 出现任何 Error/`failed_with`/`retries`/`resolves` 信号：6/30；
- 同时有成功分歧和失败信号：5/30；
- 出现真实 `retries` 边：0/30；
- 出现真实 `resolves` 边：0/30；
- 入选针对性人工标注的 Full Ours traces：15。

14 个成功分歧中，no-lifecycle-only success 为 8，Full-Ours-only success 为 6。分歧主要集中在 retail：

| Task | No-lifecycle success | Full Ours success | 分歧 trials | 含失败信号 trials |
| --- | ---: | ---: | ---: | ---: |
| retail/0 | 2/3 | 1/3 | 3 | 1 |
| retail/1 | 3/3 | 0/3 | 3 | 2 |
| retail/2 | 2/3 | 1/3 | 1 | 1 |
| retail/3 | 0/3 | 2/3 | 2 | 1 |
| retail/4 | 1/3 | 2/3 | 3 | 0 |

这说明成功率差不能简单归结为 Error 保留：9/14 个成功分歧完全没有错误节点，且同一任务不同 trial 的方向可以相反。人工诊断必须检查机器生命周期状态、被选消息与任务执行路径，不能只看最终 reward。

可复现命令：

```powershell
python scripts/analyze_lifecycle_disagreements.py `
  --report outputs/live_matrix_analysis/g47f_ml_3t1/live_matrix_report.json `
  --output outputs/lifecycle_diagnostics/g47f_ml_3t1 `
  --annotation-output outputs/annotations/g47f_ml_3t1_targeted `
  --sample-size 120 `
  --seed 301
```

针对性盲标包由 15 条优先 trace 生成，`annotator_a.csv` 与 `annotator_b.csv` 各 120 条，均不包含机器预测。隔离 key 中的机器状态分布为：Active 46、Consumed 46、Audit-required 18、Critical Evidence 7、Unresolved Failure 2、Superseded 1。

## 为什么需要新的 failure-rich 任务

原 0–4 任务矩阵没有任何真实 retry/resolve 边，因此不能识别 H4“失败负证据保留减少重复失败调用”。为避免凭任务描述主观选择，本项目扫描 τ³ 随仓库发布的 GPT-4.1 官方四轮 Full Trajectory 历史结果，并按以下顺序排名：

1. 出现 retry 的 session 数；
2. 出现 Error 的 session 数；
3. Error 总数；
4. 平均工具调用数；
5. task ID。

选出的任务：

| Domain/task | Split | Historical success | Error sessions | Errors | Retry sessions | Mean tool calls |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| retail/27 | test | 0/4 | 4/4 | 5 | 1 | 8.00 |
| retail/33 | test | 4/4 | 4/4 | 5 | 1 | 7.00 |
| retail/34 | train | 0/4 | 3/4 | 6 | 1 | 6.75 |
| retail/76 | train | 1/4 | 3/4 | 4 | 1 | 13.00 |
| retail/36 | test | 1/4 | 4/4 | 13 | 0 | 12.75 |
| airline/21 | train | 0/4 | 3/4 | 7 | 0 | 14.25 |
| airline/13 | test | 4/4 | 3/4 | 3 | 0 | 8.75 |
| airline/37 | test | 2/4 | 2/4 | 3 | 0 | 7.50 |
| airline/23 | train | 0/4 | 2/4 | 2 | 0 | 15.25 |
| airline/17 | train | 1/4 | 1/4 | 1 | 0 | 7.75 |

可复现命令：

```powershell
python scripts/select_failure_rich_tasks.py `
  --input retail=vendor/tau3-bench/data/tau2/results/final/gpt-4.1-2025-04-14_retail_default_gpt-4.1-2025-04-14_4trials.json `
  --input airline=vendor/tau3-bench/data/tau2/results/final/gpt-4.1-2025-04-14_airline_default_gpt-4.1-2025-04-14_4trials.json `
  --split retail=vendor/tau3-bench/data/tau2/domains/retail/split_tasks.json `
  --split airline=vendor/tau3-bench/data/tau2/domains/airline/split_tasks.json `
  --output outputs/failure_task_selection/gpt41_official_4trial.json `
  --top-per-domain 5
```

## Failure-retention 在线矩阵

修正版冻结配置为 `configs/glm47_flash_failure_retention_paired_3trial_v2.json`：

- 10 个上述任务；
- 每任务 3 trials；
- Full Trajectory、Ours without failure retention、Full Ours；
- 90 sessions；
- 压缩预算 4096；
- 跨 run 冷却 20 秒；
- `glm-4.7-flash` agent/user model；
- token accounting：`content_estimate_v2`；
- 估算成本上限 `$0.009`，实际 provider 成本单独记录。

该矩阵回答的是“在更可能产生工具错误的任务上，failure retention 是否改变真实重试、成功率和上下文成本”。历史 GPT-4.1 的失败只能用于任务选择，不保证 GLM 会复现相同错误；如果 GLM 仍不产生 retry/resolve，则 H4 在 τ³ 当前任务与模型组合下仍不可识别，应转向受控故障注入或 SWE-bench 类长轨迹扩展，而不能把没有发生的重试解释成方法收益。

### `g47f_fr_c2` 完成后的诊断

`g47f_fr_c2` 已完成 30/30 runs、90/90 sessions。完整性校验通过：90 条 trace 可加载且图校验 0 错，1,460 个 archive objects hash 0 失败，raw archive references 0 缺失。

以 Ours without failure retention 为参照，Full Ours 的 failure-retention 消融结果为：

- 25 个有效配对，5 个因 timeout 等基础设施中止排除；
- both / no-failure-only / Full-Ours-only / neither = 4 / 0 / 2 / 19；
- success delta = +0.0800，95% CI [0.0000, 0.2000]，McNemar p = 0.5000；
- selected-context token delta = -1,596.1，CI 跨 0；
- actual agent input token delta = -1,513.8，CI 跨 0。

结构诊断显示 failure signal 覆盖明显增加，但 H4 的核心现象仍不足：

- 30/30 配对匹配，无缺失；
- success disagreements：3；
- pairs with failure signal：19；
- pairs with selected error items：19；
- pairs with retry edges：2；
- pairs with resolve edges：0；
- Full Ours 条件下没有真实 retry edge。

因此，failure-rich 选择确实让 Error/failed_with 更常出现，但 GLM-4.7-Flash 在当前 τ³ 任务中几乎没有产生“失败后重试并解决”的可观测链条。H4 仍应标记为“当前 benchmark/model 组合不可识别”，下一步应优先做受控故障注入，或转到 SWE-bench/mini-SWE-agent 这类天然包含测试失败、日志读取和修复重试的长轨迹环境。
