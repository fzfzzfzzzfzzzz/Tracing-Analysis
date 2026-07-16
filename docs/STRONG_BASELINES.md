# 强 baseline 官方实现审计

本页记录 2026-07-16 对 AgentDiet、ACON 和 Agent-Omit 的论文/代码审计。目标是防止 deterministic proxy 被误报为论文官方方法。

## 当前结论

| Manager | 当前实现 | 官方来源 | 主结果资格 |
| --- | --- | --- | --- |
| `agentdiet_style` | 去重 + expired 状态过滤 proxy | [AgentDiet paper](https://arxiv.org/abs/2509.23586)；未识别到官方代码仓库 | 否 |
| `acon_style` | observation/history 截断 proxy | [ACON paper](https://arxiv.org/abs/2510.00615)、[Microsoft 官方代码](https://github.com/microsoft/acon) | 否 |
| `acon_official` | 外部加载、hash 固定的官方 optimizer 运行时适配器 | 同上，commit `d63f9ae18959dc7215ff62899c94c5e8c56847ae` | 逐次运行判定；必须无 fallback 且 usage 完整 |
| Agent-Omit | 未接入 | [paper](https://arxiv.org/abs/2602.04284)、[official code](https://github.com/usail-hkust/Agent-Omit) | 当前不作为 inference-only baseline |

`aggregate.json` 可用于结构管线检查，但 proxy 结果不能进入论文主表。`manifest.json` 现在为每个 manager 写入 `implementation_kind`、`main_result_eligible`、论文/代码来源和边界说明；未注册 manager 会 fail closed。

## 为什么当前 ACON proxy 不是官方 ACON

Microsoft 官方 ACON 是独立的 context-compression framework，支持 AppWorld、OfficeBench 和多目标 QA。其源码包含：

- `ObservationOptimizer.process(task, observation, history, raw_history, opt_args)`；
- `HistoryOptimizer.process(task, history, prev_history_summary, raw_history, opt_args)`；
- 单独 compressor LLM 或 distilled compressor；
- 自然语言 compression guideline、Jinja prompt、阈值与历史状态；
- guideline optimization：用 full-context 成功但 compressed-context 失败的 paired trajectories 更新 guideline。

当前 `acon_style` 只确定性截断 observation/tool history，没有调用官方 optimizer、模型、prompt 或 guideline，也没有 compressor cost，所以只能命名为 style proxy。

`acon_official` 是另一条独立的 live-only 路径，不会把 `acon_style` 重命名冒充官方结果。它调用外部官方类，不能用于 post-hoc 离线图选择。

## τ³ 官方 ACON 适配验收标准

正式接入必须同时满足：

1. 在 τ³ agent loop 中增加 observation hook 和 periodic history hook，调用官方 `ObservationOptimizer` / `HistoryOptimizer`，不能只在完整图上做 post-hoc 静态选择；
2. 固定并记录 `microsoft/acon` commit、MIT license、配置文件、prompt/guideline hash、compressor model 与 tokenizer；
3. 为 τ³ 的 policy、goal、tool call、observation 和 user turn 定义无损序列化，并用 fixture 验证往返；
4. compressor 输入/输出 tokens、延迟和费用计入总成本；失败、超时和空摘要必须有预注册 fallback；
5. 与 Full Trajectory 使用相同 agent/user model、task、trial、seed、step limit 和 stop adapter；
6. 只报告实际 live reward；offline view 的 task success 继续为空；
7. 先通过 Stage 1 模型适用性 gate，再进行 paired pilot 和统计检验。

## 已实现的官方适配边界

- 官方源码通过 GitHub codeload 获取，固定到 commit `d63f9ae18959dc7215ff62899c94c5e8c56847ae`；`LICENSE`、包入口、两个 optimizer、base class 和 3 个 AppWorld prompt 共 9 个文件逐一校验 SHA-256。
- 第三方源码、下载包和展开目录均位于 Git 忽略的 `vendor/`，本仓库只公开下载/校验脚本、hash manifest 和适配代码。运行 `scripts/setup_acon.ps1` 可重建该快照。
- `TraceGraphTauAgent` 在每轮 action 前调用官方 observation/history hook。完整 policy 与首条 task 留在压缩边界外；tool call ID、arguments、tool result ID/error 经过确定性 JSON 序列化。
- history hook 保存上一版 summary，只压缩尚未覆盖的旧消息，并保留最近一轮完整 action–observation。官方 optimizer 收到原签名中的 `task`、`history`、`raw_history`、`prev_history_summary` 和 `opt_args`。
- compressor 使用单独的 τ³ provider 调用，逐次记录 provider input/output tokens、cost、latency 与估算 tokens；记录写入被忽略的 `acon_calls.jsonl`，compressor cost 同时并入上游 assistant turn 的总 cost，并在 `raw_data.tracegraph_context_management` 保留拆分。
- 默认 `fallback=error`。只有配置显式改为 `raw` 才允许继续，而任何 fallback、缺失 provider usage 或源码 hash 不匹配都会令 `runtime_main_result_eligible=false`。
- 静态 provenance 仍将 `acon_official.main_result_eligible` 设为 false；必须读取每次 live run 的 runtime eligibility，不能只凭 manager 名称进入主表。

接口、序列化、状态推进、最近轮保留、失败路径、usage 计量和源码拒绝已通过契约测试；真实快照也已成功加载进 τ³ 隔离环境。免费 `glm-4.7-flash` 已通过 Stage 1，但本轮只执行 native/proxy 条件的 pipeline smoke；官方 ACON 仍需冻结 compressor model、prompt/guideline hash、限流与 usage/cost 规则后再运行。因此当前没有 ACON live reward，也不作效果声明。

## AgentDiet 与 Agent-Omit 边界

AgentDiet 论文提出 inference-time trajectory reduction，移除 useless、redundant 和 expired information；当前未找到论文作者提供的官方代码，因此不能声称复现一致。若未来代码发布，应冻结 commit 并替换 `agentdiet_style`。

Agent-Omit 是训练方法：通过 cold-start data、omit-aware agentic RL 和专门 reward，让模型学习省略 thought/observation。官方仓库依赖训练 checkpoint 和 AgentGym/Verl；它不是可直接塞入同一 API agent 的无训练 context manager。若做扩展实验，应使用作者 checkpoint 和原生评测，而不是把规则删除器命名为 Agent-Omit。
