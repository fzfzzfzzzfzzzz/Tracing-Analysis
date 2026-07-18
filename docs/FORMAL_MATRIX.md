# 正式实验矩阵与成本门控

配置文件与生成的 manifest 均禁止包含 key、secret、credential、password 或 access token 字段；真实凭据仍只从被 Git 忽略的 `.env` 加载。`glm-4.5-air` 历史结果见 [Stage 1 正式结果](STAGE1_RESULTS.md)；通过 gate 的 `glm-4.7-flash` 重跑与 paired smoke 见 [GLM-4.7-Flash 结果](GLM47_FLASH_RESULTS.md)。

## Stage 1：模型与 harness 适用性门槛

冻结配置：`configs/glm_full_trajectory_pilot_v1.json`。

- domains：retail、airline；
- tasks：每域 `0–4`，共 10 个固定任务；
- trials：每任务 3 次；
- manager：只运行 `full_trajectory`；
- agent/user model：`zai/glm-4.5-air`；
- base seed：300；
- max steps：50；
- user-stop adapter：固定开启；
- 自动重试：关闭；
- session 数：30；
- 保守估算：`$0.01/session`，合计 `$0.30`。

`$0.01/session` 是基于现有 retail pilot 最高约 `$0.0059` 的双倍缓冲，不是 provider 账单硬上限；实际费用仍可能因轨迹长度、模型计价或失败重试策略变化。runner 已关闭自动重试并使用单并发。

零费用生成 manifest 与命令：

```powershell
python scripts/plan_glm_matrix.py `
  --config configs/glm_full_trajectory_pilot_v1.json `
  --output outputs/plans/glm_full_trajectory_pilot_v1.json `
  --print-commands
```

规划器会拒绝 secret-like 配置字段、未知 manager、重复任务和重复条件。输出包含 task、condition、trial、seed、预估 session/cost 和解释边界，不包含凭据。

## 显式执行门槛

默认不执行。只有同时传入 `--execute` 和覆盖估算的成本上限才会逐 run 调用安全 runner：

```powershell
python scripts/plan_glm_matrix.py `
  --config configs/glm_full_trajectory_pilot_v1.json `
  --output outputs/plans/glm_full_trajectory_pilot_v1.json `
  --execute `
  --max-estimated-cost-usd 0.30
```

低于 `$0.30` 或缺失的 cap 会在任何 API 调用前拒绝执行。该 cap 只是本地估算门槛；执行前仍应确认 provider 余额和用户批准的实际预算。

## Stage 1 通过条件

- task success rate ≥ 0.50；
- normal stop rate ≥ 0.90；
- median tool calls ≥ 5；
- median estimated trajectory tokens ≥ 4096；
- infrastructure error rate ≤ 0.05。

这些 gate 首先回答“模型和 benchmark 是否适合研究问题”。若失败，不进入 context-manager 对照，避免把模型能力、user simulator 或基础设施错误误归因于压缩算法。

## 后续阶段

1. Stage 1 通过后，从真实 mandatory-context 分布选择预算；`content_estimate_v2` sweep 已确认 2048 过低、4096 为最小结构可行预算。
2. 固定一个预算后，运行 Full Trajectory、7 个 baseline、4 个 ablation 和 Full Ours；相同 task、trial、seed、模型与 user adapter，只替换 context manager。
3. 当前 10-task × 3-trial paired pilot 完成后，先基于人工 gold 改进生命周期标签和失败任务覆盖，再扩到每域 10–20 tasks，并执行 paired bootstrap、McNemar/permutation 与 Holm correction。

`glm-4.7-flash` 重跑已完成 30/30 sessions，并以 success `0.5333`、normal stop `0.90`、infrastructure error `0` 通过全部 gate。重计量后的中位内容轨迹为 `4,875`，30 图 sweep 推荐 4096。旧 `g47f_ml_3t1` 的官方 reward 可保留，但其压缩条件使用了错误的 prompt-usage 节点大小，因此已从正式效果结论中撤回。替代配置为 `configs/glm47_flash_machine_lifecycle_paired_3trial_v2.json`；failure-rich 替代配置为 `configs/glm47_flash_failure_retention_paired_3trial_v2.json`。两者都冻结 `content_estimate_v2`，运行时版本不一致会在 API 调用前后 fail closed。详见 [Token 计量修正](TOKEN_ACCOUNTING.md)。

矩阵配置可设置非负 `inter_run_delay_seconds`。该值写入 manifest 并在每个 run 后显式等待，用于披露和控制免费端点的瞬时限流，不改变单个 session 内的模型、seed 或 context-manager 条件。
