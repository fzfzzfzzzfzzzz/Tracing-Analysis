# 完整实验协议

## 0. 环境与固定变量

正式比较中固定：benchmark task IDs、trial seeds、agent model、user model、工具集合、max steps、temperature 和并发度。唯一变化是 context manager。每个条件使用相同 token budget；Full Trajectory 作为不受 budget 限制的性能上界。

先运行：

```powershell
./scripts/bootstrap.ps1
./scripts/run_smoke.ps1
```

## 1. 收集或导入 Full Trajectory

推荐先用 τ³ `retail` 与 `airline` 的固定小样本，每域 10–20 tasks、至少 3 trials。也可以先导入官方历史轨迹进行离线验证。

```powershell
python -m tracegraph import-tau `
  --input data\raw\tau3\run-name `
  --output data\processed\tau3-graphs `
  --archive artifacts\tau3-archive
```

导入后必须检查：

```powershell
Get-ChildItem data\processed\tau3-graphs\*.json | ForEach-Object {
  python -m tracegraph validate-trace $_.FullName
}
python -m tracegraph verify-archive artifacts\tau3-archive
```

## 2. 实验一：离线生命周期现象

运行完整 suite 后读取 `lifecycle_analysis.json`。报告各状态频数、`created→active`、`unresolved_failure→resolved_failure`、`active→critical_evidence`、`observation→superseded` 等转移，并按 domain、task length、tool/error 类型分层。

人工抽样至少 100 个节点做双人标注，报告 Cohen's κ；自动推断只作为候选标签，不直接当 gold。

## 3. 实验二：Oracle 压缩上界

`oracle_upper_bound.json` 只使用完整轨迹的 post-hoc 结构信息，保留所有硬保护节点。报告可安全移除 token 比例与结构可靠性。它是方向可行性的上界分析，不与在线方法混为一谈。

## 4. 实验三：在线 context manager

离线的 `online_replay.jsonl` 在每个 step 只构造该前缀图，用来检查未来信息泄漏与 context 大小。正式 task success 必须用 [τ³ live agent](TAU3_INTEGRATION.md) 重跑。

建议预算：512、1024、2048、4096；先在 10-task pilot 上选择不会让 mandatory context 大面积超预算的范围，再冻结正式配置。

## 5. 实验四：ablation

完整条件：

- `ours_without_graph_edges`
- `ours_without_lifecycle_states`
- `ours_without_failure_retention`
- `ours_without_constraint_retention`
- `full_ours`

测试已保证 failure/constraint ablation 不会被通用 Active/blocks 规则重新引入。

## 6. 强 baseline

一键 runner 包含：

- `full_trajectory`
- `last_k`
- `token_length_pruning`
- `summary_only`
- `llm_only_pruning`
- `agentdiet_style`
- `acon_style`

默认的后三类/摘要类是透明标记的 deterministic proxy，用于管线验证。论文主结果必须把 scorer/summarizer 替换为指定模型或官方方法，并在 manifest 中写明版本、prompt 和 commit。

AgentDiet/ACON/Agent-Omit 的官方实现可用性、接口差异和 τ³ 接入验收标准见 [强 baseline 官方实现审计](STRONG_BASELINES.md)。实验 manifest 会 fail-closed 地记录每个 manager 的实现类型与主结果资格。

## 7. 统计

- τ 任务成功率：paired bootstrap 95% CI；多 trial 报官方组合估计 `Pass^k = mean_task[C(c_i,k)/C(n_i,k)]`，其中基础设施型中止不进入 `n_i`。
- token/cost/overhead：按相同 task+trial 做配对差异与 bootstrap CI。
- policy violation / repeated failure：McNemar 或配对 permutation test。
- 多预算、多 domain 比较做 Holm correction。
- 预先冻结 exclusion rule：上游 infrastructure error 单独统计，不当 agent failure。

## 8. 阶段性判断

若真实小样本中 median trajectory 太短、Oracle compression ratio 低、生命周期转移稀少，或强 LLM-only 在相同预算下结构指标与 Full Ours 无显著差异，应按报告要求调整 benchmark 或缩小论文主张，而不是扩大 synthetic 结果。

## 9. 当前 GLM 阶段状态

2026-07-16 已完成真实 API 连通、Function Call、mock 端到端成功以及两次 retail 单任务 Full Trajectory。第一次完成全部 5 个目标工具动作但 user 没有停止；第二次正常 `user_stop`，但 agent 选择了错误键盘 variant，官方 reward 仍为 0。两次结果都不得改写为 task success。详细配置、成本、失败分析与三图离线结构 pilot 见 [GLM Pilot](GLM_PILOT.md)。

项目提供默认关闭的确定性 user-stop 协议 adapter；是否启用必须在所有条件中固定。`glm-4.7-flash` 已提供零成本可用路径，并通过模型适用性 gate。免费端点可能瞬时限流，因此矩阵显式记录跨 run 冷却，infrastructure error 仍单独排除。

Full Trajectory 适用性 gate 已冻结并完成 10 tasks × 3 trials；任务、seed、成本估算、执行 cap 和通过阈值见 [正式实验矩阵](FORMAL_MATRIX.md)。

历史 `glm-4.5-air` Stage 1 为 `12/30 = 0.40`。`glm-4.7-flash` 随后用同一 gate 取得 `16/30 = 0.5333` 并通过全部条件。对 Stage 1 图执行 `content_estimate_v2` 重计量后，中位内容轨迹为 4,875 tokens，gate 仍通过；预算 sweep 推荐 4096。旧 120-session paired matrix 的 reward/termination 保留为诊断，但其压缩条件受旧 token 口径影响，不能进入正式效果结论。替代的 corrected smoke、`g47f_ml_c2` 和 failure-rich `g47f_fr_c2` 使用冻结的 4096 预算，并同时报告估算 context tokens 与真实 agent provider input usage。详细边界见 [Token 计量修正](TOKEN_ACCOUNTING.md) 和 [GLM-4.7-Flash 结果](GLM47_FLASH_RESULTS.md)。
