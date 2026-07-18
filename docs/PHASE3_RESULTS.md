# 第三阶段执行结果与门槛状态

> 快照日期：2026-07-18
> 结论：P1 已完成；P2 已完成 **Codex 临时版**，但未取得独立人工 gold；P3 已完成 60-session 修复后复合分析；预注册 P4 gate 为 **No-Go**，因此 P4 扩展实验不应执行。

## 1. 结果总览

| 阶段 | 工程状态 | 研究状态 | 判定 |
| --- | --- | --- | --- |
| P0 | Failure Card、expiry、预算、投影和 Raw Hard 对照已完成 | 自然轨迹构念仍需未来人工复核 | 工程完成 |
| P1 | 128 次受控干预与完整产物已完成 | 只作机制可识别性证据 | 通过 |
| P2 | 60-chain 包、Codex A/B、裁决和评分均完成 | 同模型同会话的两个 pass，不是两位独立人工 | 临时版完成；正式 gate 不通过 |
| P3 | 四条件 runner、评测器修复、24-session 平衡补跑和 60-session 复合分析均完成 | 数据完整，但 P2 正式门槛及三项效果门槛未通过 | 临时实验完成；正式 gate 不通过 |
| P4 | ACON+card manager 与 fail-closed gate 已完成 | `p4.go_gate_passed=false`，扩展实验被正确阻止 | 工程完成；实验 No-Go |

## 2. P1：确定性机制干预

冻结产物：`outputs/phase3/p1_interventions_v2/`。

- 4 类干预：参数修正、只保留最新失败、替代工具完成、malformed 后合法调用；
- 每类 8 个固定任务，共 32 个任务；
- 4 个条件：Full Trajectory、Remove、Raw Hard、Compact Failure Card；
- 共 128 runs，128/128 TraceGraph 校验通过；
- 受控 card precision = 1.0，expiry correctness = 1.0；
- Compact Card 相对 Remove：平均 repeated invalid action `-1.0`，平均 recovery steps `-1.0`，task success delta `0`；
- Compact Card 相对 Raw Hard：平均 selected representation tokens `-206.75`，protocol-closed tokens `-350.5`，本地 controller 序列化输入 `-277.75`，task success delta `0`；
- 四类干预方向一致。

这里的 P1 `actual_provider_input_tokens` 是确定性本地 controller 的精确序列化输入，不是外部 LLM tokenizer usage；manifest 已显式记录这一边界。

## 3. P2：Codex 临时标注版

冻结包：`outputs/phase3/p2_failure_chain_v1/`。

用户暂时无法安排人工标注，因此 A/B 先由 Codex 完成，并在每份 CSV 和报告中写入：

- `annotation_provenance=codex_provisional`；
- A 身份：`codex_gpt5_pass_a`；
- B 身份：`codex_gpt5_pass_b`；
- independence warning：`same_model_same_thread_not_independent_human_gold`。

Codex A/B 使用不同顺序和不同判定倾向，共标 60 条 chain。11 个字段分歧分布在 7 条 chain，随后由 Codex 临时裁决；最终无 unresolved adjudication。说明文件为 `CODEX_PROVISIONAL_NOTICE.md`，评分报告为 `codex_provisional_p2_report.json`。

临时评分结果：

| 指标 | Codex 临时值 | 计划门槛 | 状态 |
| --- | ---: | ---: | --- |
| 最小字段 Cohen's κ | 0.000 | ≥ 0.70 | 未通过；card coverage 近单类别导致 κ 退化 |
| actionable precision | 0.942 | ≥ 0.75 | 通过 |
| actionable recall | 1.000 | ≥ 0.75 | 通过 |
| expiry precision | 0.744 | ≥ 0.90 | 未通过 |
| operation-scope 聚合错误率 | 0.400 | 需可接受且分析错误 | 偏高 |

正式 gate 会显式拒绝 Codex provenance，不会把两个 Codex pass 冒充两位独立人工。当前无需为了决定是否进入 P4 而立即补人工：即使 P2 日后由人工通过，P3 仍有三个独立效果 blocker。若要投稿或重新主张构念有效性，仍必须补两位独立人工标注。

## 4. P3：单环境四条件实验

### 4.1 原始矩阵的评测器偏差

原始 `p3_card_retail_codex_v1` 执行了 20 runs / 60 sessions，但其中 17 个 session 在对话完成后的 τ³ natural-language assertion evaluator 阶段触发 OpenAI `insufficient_quota`。错误集中在后执行的 task 76/36 和 Remove/Raw/Card 条件，而先执行的 Full 没有错误，因此直接使用原始有效子集会产生条件顺序偏差。

这些错误 session 的 `SimulationRun` 已丢弃对话，不能只补算 reward。项目因此没有把 43 个有效 session 当作正式矩阵，而是对受影响的两个任务把四个条件全部平衡重跑。

### 4.2 评测器修复与平衡补跑

修复没有修改 vendor 源码：

- `run_glm_pilot.ps1` 和矩阵计划支持显式 `EvaluatorModel`；
- `tau3_cli.py` 同时覆盖 `tau2.config` 和 evaluator 已复制的模块全局值；
- ZAI 返回 fenced JSON 时，包装层先尝试严格 JSON，再只在失败时提取 fenced JSON；
- evaluator 的模型、参数和 JSON 模式均记录在进程环境与计划中。

两个诊断批次分别因“旧默认值已被模块复制”和“fenced JSON”而停止，完整移入 `outputs/phase3/aborted_attempts/`，不进入分析。最终平衡补跑配置为 `configs/phase3_p3_compact_card_evalfix_codex_v1.json`，使用 `zai/glm-4.7-flash` 统一充当 agent、user simulator 和 evaluator，结果为：

- 8/8 runs；
- 24/24 sessions；
- 0 infrastructure error；
- 0 graph validation error；
- provider usage coverage 100%。

### 4.3 修复后 60-session 复合数据集

可复现构建脚本：`scripts/build_phase3_repaired_composite.py`。复合计划为 `outputs/phase3/plans/p3_card_retail_codex_repaired_composite_v1.json`：

- task 27/33/34：来自原始矩阵；
- task 76/36：四个条件全部来自平衡补跑；
- 每个 task 内的模型、seed、trial、budget、停止协议和 evaluator 保持配对一致；
- evaluator 可以在 task strata 之间不同，因此这不是“一次不中断的单批运行”，只汇总 task 内配对差值。

完整性结果：20/20 runs、60/60 sessions、60/60 traces、0 infra、0 graph error、0 zero-token、0 malformed、provider usage 100%。

| 条件 | success | normal stop | 平均 provider input | 平均 protocol-closed tokens | 平均 repeated invalid |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full Trajectory | 2/15 (13.3%) | 93.3% | 93,494 | 94,864 | 0.067 |
| Remove | 4/15 (26.7%) | 86.7% | 94,000 | 75,744 | 0.133 |
| Raw Hard | 2/15 (13.3%) | 86.7% | 95,649 | 90,821 | 0.000 |
| Compact Card | 4/15 (26.7%) | 93.3% | 86,123 | 74,593 | 0.000 |

Compact Card 的 task 内配对差值：

| 参考条件 | success Δ [95% CI] | provider input Δ [95% CI] | protocol token Δ [95% CI] | repeated invalid Δ [95% CI] |
| --- | ---: | ---: | ---: | ---: |
| Full | +0.133 [-0.133, 0.400] | -7,371 [-37,254, 18,157] | -20,272 [-77,770, 29,068] | -0.067 [-0.200, 0.000] |
| Remove | 0.000 [-0.200, 0.200] | -7,877 [-46,901, 24,514] | -1,152 [-41,512, 40,012] | -0.133 [-0.400, 0.000] |
| Raw Hard | +0.133 [-0.133, 0.400] | -9,526 [-37,611, 17,323] | -16,228 [-62,759, 30,115] | 0.000 [0.000, 0.000] |

点估计显示 Card 没有复现 Raw Hard 的无界回注，并在三组参考下都有较低的平均 provider input；但样本只有 15 对，token CI 均跨 0。相对 Remove 的 repeated-invalid CI 上界等于 0，而预注册机制门槛要求严格小于 0；可配对的 resolved-failure session 也不足以估计 recovery CI。成功率非劣门槛为 CI 下界 ≥ `-0.05`，Card 对 Raw/Remove 的下界分别为 `-0.133` 和 `-0.200`，因此不能宣称非劣。

三份报告位于：

- `outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/full_trajectory_reference/`；
- `outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/remove_reference/`；
- `outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/raw_reference/`。

## 5. P4：工程完成，扩展实验 No-Go

`acon_official_with_failure_cards` 已实现：保持官方 ACON selected-message plan 不变，再叠加受界 Failure Card fragment；源码 hash、runtime usage、fallback 和 eligibility 均 fail closed。

最新 gate 报告：`outputs/phase3/gates/p3_card_retail_codex_repaired_composite_v1_gate_report.json`。

已通过的检查：P1 工程、P3 矩阵完整、provider usage 完整、card 受界且无 raw replay、failure-type 一致性、Card 不增加 Raw repeated invalid。

未通过的检查：

1. `p2_human_construct_gate`：Codex 不是独立人工 gold；
2. `card_reduces_raw_protocol_and_provider_input`：两个 token CI 上界均未小于 0；
3. `card_improves_repeats_or_recovery_vs_remove`：repeat CI 上界为 0，recovery CI 不可估；
4. `task_success_noninferior`：对 Raw/Remove 的 CI 下界低于 `-0.05`。

因此 `p4.go_gate_passed=false`。runner 会在任何 P4 外部 API 会话开始前 fail closed；不应绕过 gate 运行 ACON 扩展、第二模型家族或第二环境。现有账号也只有 GLM 家族，本机没有已配置的 SWE/mini-SWE 第二环境。

这不是“P4 代码没写完”，而是计划中的 No-Go 分支已经被执行：工程适配保留，扩大实验停止。更合适的后续方向是分析 failure-retention policy 的负结果，或先增加自然 failure-rich 任务以提高机制事件数，再预注册一个新的实验，而不是追跑更多 baseline。

## 6. 复现与验证

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q

python scripts/build_phase3_repaired_composite.py `
  --original-plan outputs/phase3/plans/p3_card_retail_codex_v1_executed.json `
  --repair-plan outputs/phase3/plans/p3_card_retail_codex_evalfix_v1_executed.json `
  --repair-task-id 76 --repair-task-id 36 `
  --matrix-id p3_card_retail_codex_repaired_composite_v1 `
  --output outputs/phase3/plans/p3_card_retail_codex_repaired_composite_v1.json

python scripts/analyze_live_matrix.py `
  --plan outputs/phase3/plans/p3_card_retail_codex_repaired_composite_v1.json `
  --results-root vendor/tau3-bench/data/simulations `
  --reference-manager raw_hard_failure_retention `
  --output outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/raw_reference
```

当前全量回归：`103 passed`。正式 P4 仍只能在 `evaluate_phase3_gates.py` 输出 `p4.go_gate_passed=true` 后执行。
