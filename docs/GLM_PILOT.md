# GLM 真实 τ³ Pilot（2026-07-16）

本页记录使用本地 GLM 凭据完成的真实调用与边界。`.env` 被 Git 忽略；所有已提交文件和实验摘要都不包含 API key。

## 固定环境

- τ³ 上游包：`tau2 1.0.0`
- LiteLLM：`1.81.11`
- Provider：原生 `zai/<model>`
- API base：`https://open.bigmodel.cn/api/paas/v4`
- 可用资源模型：`zai/glm-4.5-air`
- agent/user temperature：`0.0`
- GLM thinking：通过 `extra_body` 设为 `disabled`
- trial concurrency：`1`
- seed：`300`

认证后的 `/models` 查询成功。最小 Function Call 探针返回一个合法工具调用；关闭 thinking 后，输出从 78 tokens 降到 14 tokens，避免推理 token 占满短输出预算。

## 可复现运行

先在本地 `.env` 设置 `ZAI_API_KEY`，不得提交该文件：

```powershell
./scripts/run_glm_pilot.ps1 `
  -Domain mock `
  -TaskId create_task_1 `
  -Manager full_trajectory `
  -Budget none `
  -MaxSteps 8 `
  -SaveTo tracegraph_glm45air_mock_create_task1_full_trajectory_v2 `
  -TraceOutputDir outputs/tau3_live/mock_full_trajectory_v2 `
  -VerboseLogs
```

runner 会先确认 `.env` 被 Git 忽略，只向子进程加载变量，不显示密钥。它还固定单 trial、单并发、无自动重试，并传播非零退出码。

## Pilot A：mock 端到端成功

任务：`mock/create_task_1`，Full Trajectory，最多 8 步。

- reward：`1.0`
- DB check：`1.0`
- write action：`1/1`
- termination：`user_stop`
- duration：`6.98s`
- agent cost：`$0.000293`
- user cost：`$0.0001187`
- 轨迹：8 messages、10 nodes、6 edges
- trace schema 与 archive SHA-256：全部通过

这证明 GLM → τ³ user/agent → tool → evaluator → TraceGraph → archive 的完整链路可运行。

## Pilot B：retail 工具任务完成，但协议失败

任务：`retail/0`，Full Trajectory，最多 30 步。官方期望 5 个动作；运行中实际按顺序产生：

1. `find_user_id_by_name_zip`
2. `get_order_details`
3. `get_product_details`
4. `get_product_details`
5. `exchange_delivered_order_items`

5 个工具调用均无 error，写操作已返回成功。随后 agent 明确确认换货，user 也表示“不需要其他帮助”，但 `glm-4.5-air` 没有按 τ³ user simulator 协议输出 `###STOP###`，而是持续礼貌寒暄直到 `max_steps`。

- official reward：`0.0`（提前终止，不可重解释为成功）
- termination：`max_steps`
- duration：`59.10s`
- agent cost：`$0.0047908`
- user cost：`$0.0011083`
- 轨迹：31 messages、37 nodes、16 edges
- trace schema 与 archive SHA-256：全部通过

额外兼容探针：更强的 `glm-4.7` 被服务端以余额/资源包不足拒绝；对 `glm-4.5-air` 强化原有停止指令仍未产生 `###STOP###`。因此没有通过增加步数继续消耗额度，也没有用启发式后处理改写官方 reward。

## 两图离线结构 Pilot（2048 tokens）

将 mock 成功图与 retail 协议失败图放入同一离线套件，仅用于结构现象与管线验证：

- 生命周期：Active 18、Consumed 26、Audit-required 2、Critical Evidence 1；
- Oracle：两图平均结构压缩率 `0.9495`，硬保护移除数 `0`；
- retail Full Trajectory：`116,809` estimated tokens；
- retail Full Ours：`12,521` tokens、压缩率 `0.8928`、constraint/evidence retention `1.0`、unsafe removal `0`；
- retail Last-k：`39,785` tokens、constraint retention `0.0`、unsafe removal `9`；
- retail Summary-only proxy：`822` tokens、unsafe removal `13`。

样本量只有 2，且 mock/retail 轨迹长度高度异质；不能据此计算显著性或声称任务成功率提升。它支持的阶段性判断是：retail 中确有很大的结构压缩空间，但完整 policy constraint 本身会让 Full Ours 超过 2048 预算，正式实验需要可验证的 policy 摘要或更高预算。

## 对调研目标的当前结论

- H1/H2：真实轨迹出现 Active→Consumed 与 Active→Critical Evidence，但尚无人工 gold labels，只有现象证据。
- H3/H6：结构保留差异已经出现；没有多条件 live task success，尚不能验证性能假设。
- H4：本次轨迹没有工具 error，无法评估 repeated failure。
- H5：retail 轨迹足够长，存在明显 token 压缩空间；仍需正式多任务运行验证净收益。

下一阶段的硬前置条件：提供可遵守 `###STOP###` 协议且有额度的 user model（或预注册、跨条件固定并明确披露的 user-protocol adapter），然后冻结 retail/airline task IDs、3 trials、预算和模型，先跑 10-task Full Trajectory pilot，再启动全部 manager。
