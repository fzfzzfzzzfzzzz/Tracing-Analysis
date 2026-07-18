# Token 计量修正与实验版本边界

## 问题

2026-07-16 的轨迹审计发现，旧版 τ 导入器曾把 assistant 消息的
`prompt_tokens + completion_tokens` 作为该消息节点自身的 `token_count`。
`prompt_tokens` 包含当轮之前的完整输入历史、系统提示和工具 schema，不是该条
assistant 消息的内容长度。把它写入节点后会重复累计历史，并夸大
ContextView 的预算压力。

一个真实例子中，几十词的 assistant 回复被记为约 5,000 tokens；修正后同一
会话的最终图为 4,676 个内容估算 tokens，而该会话所有 agent 调用的真实累计
provider prompt usage 为 48,961 tokens。这三者分别表示：

- 节点内容长度：用于图内预算选择；
- 单轮/累计 provider prompt usage：用于真实 API 输入遥测；
- provider completion usage：用于输出遥测。

它们不能互相替代。

## 修正版口径

新版本固定为 `content_estimate_v2`：

1. 所有 TraceGraph 节点的 `token_count` 只对节点自身内容做确定性的
   byte-aware 估算；
2. provider 的 prompt/output usage 原样保存在节点 metadata 和官方
   simulation 中，不进入节点大小；
3. 在线矩阵把 `token_accounting` 写入 config、plan、run 环境、TraceGraph 和
   ContextView；配置与运行时代码版本不一致时 fail closed；
4. paired 聚合同时报告估算的 selected-context tokens 和真实
   `agent_provider_input_tokens`，效果结论优先参考后者。

## 已有结果的边界

- Stage 1 的官方 reward、termination、工具动作和成本不受该问题影响；
- 30 条 Stage 1 Full-Trajectory 图已用
  `scripts/retokenize_traces.py` 重计量并重新验证；
- Stage 1 中位内容轨迹为 `4,875` tokens，仍通过冻结的 `≥ 4,096` 门槛；
- 修正后的预算 sweep 推荐 `4,096`，而不是旧口径下的 `16,384`；
- `g47f_ml_3t1` 的压缩条件实际模型输入受旧预算口径影响，因此只能保留为
  修复前诊断，不能继续作为正式 paired 结论；
- `g47f_fr_3t1` 在进入压缩条件前被主动中止，已有 Full-Trajectory 样本只用于
  确认 failure-rich 选择能产生真实 Error，不构成完整矩阵结果。

替代矩阵：

- corrected smoke：`g47f_ml_s3`，8/8 sessions/traces/archives 已通过；
- corrected lifecycle matrix：`g47f_ml_c2`，120/120 sessions/traces、1,768 个
  archive objects 已通过；Full Ours 对 Full Trajectory 的 28 个有效配对成功率
  差为 `+0.1429`，95% CI `[-0.0714, 0.3571]`；
- corrected failure-retention matrix：`g47f_fr_c2`。

`g47f_fr_c2` 已完成 90/90 sessions，全部 trace 均记录 `content_estimate_v2`。Full Ours 相对 Ours without failure retention 的 paired token 结果为：mean selected-context delta -1,596.1，95% CI [-12,300.2, 8,662.5]；mean actual agent provider input delta -1,513.8，95% CI [-24,108.8, 21,278.1]。因此 failure retention 在该矩阵中没有形成显著 token 改善；同时 Full Ours 相对 Full Trajectory 的 selected-context 与 actual-input delta 均为正点估计，说明 failure-rich 长尾任务上当前策略可能带入额外噪声或增加轨迹长度。

corrected smoke 的四个条件均成功 1/2，且 8 条轨迹全部记录
`content_estimate_v2`、0 graph/archive/API/参数错误。它还验证了两种输入统计不会
混为一谈：Last-k 相比 Full Trajectory 每次 agent 调用平均少约 1,356 个真实
prompt tokens，但由于调用次数更多，累计真实输入差为 +1,439。该 smoke 只用于
管线验收，不作效果推断。

`g47f_ml_c2` 的 120 条轨迹全部记录 `content_estimate_v2`，provider input usage
覆盖所有非 timeout 会话。Full Ours 相对 Full Trajectory 的 mean estimated
selected-context 配对差为 `-9,306.2`，95% CI `[-20,662.6, -139.9]`；真实
agent provider input 配对差为 `-13,533.7`，95% CI
`[-32,678.8, 2,630.5]`。因此当前可以声称图内选中内容减少，但不能声称真实
provider 输入已显著减少。

## 可复现命令

```powershell
$env:PYTHONPATH = "src"

python scripts/retokenize_traces.py `
  --input outputs/tau3_live/g47f_s1_v1 `
  --output outputs/token_accounting/g47f_s1_v1_repair.json

python scripts/analyze_glm_stage1.py `
  --plan outputs/plans/g47f_s1_v1.json `
  --results-root vendor/tau3-bench/data/simulations `
  --output outputs/stage1_analysis/g47f_s1_v1 `
  --graph-output data/processed/g47f_s1_v1_graphs `
  --archive-output artifacts/g47f_s1_v1_archive

python scripts/run_budget_sweep.py `
  --input data/processed/g47f_s1_v1_graphs `
  --archive artifacts/g47f_s1_v1_archive `
  --output outputs/budget_sweep/g47f_s1_v1_content_v2 `
  --budgets 2048 4096 8192 12288 16384
```

原始 provider usage、修复报告、raw results、traces 和 archives 均位于 Git 忽略
目录；公开仓库只保存实现、冻结配置、统计定义和诚实的结果边界。
