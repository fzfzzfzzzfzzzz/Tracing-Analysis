# AppWorld TraceGraph 正式接入与五题 Canary 结果（2026-09-18）

## 结论先行

AppWorld 的正式在线 TraceGraph 接入已经走通。Qwen3.8-27B 在五个此前已暴露的开发 canary 上，TraceGraph 为 **5/5**，Full History 为 **4/5**；但 TraceGraph 总 token 反而高 **11.5%**。因此这批结果支持“TraceGraph 能在 AppWorld 在线 agent 中工作且没有显著破坏正确性”的可行性判断，不支持“成本更低”或“性能显著更好”的论文结论。

全部五题均使用同一固定 ACON/AppWorld、同一模型、thinking、50 步上限和官方 evaluator。唯一实验变量是 `MemoryManager.get_conversation_history()` 返回给模型的历史视图。

## 1. 接入与修复

正式接入位于 `src/tracegraph/integrations/appworld_memory.py`，服务器 runner 为 `scripts/run_qwen38_appworld_canary_server_260918.py`。每轮执行以下流程：

1. 将 ACON 的 `user / assistant / user observation` 历史转换为 TraceGraph；
2. 用正式 `GraphLifecycleManager` 在 8192 内容估算预算下选择节点；
3. 对 AppWorld 的 assistant-action / user-observation 对执行协议闭包；
4. 将 failure card 等紧凑表示注入任务锚点；
5. 保存完整图、context view、所选消息序号、闭包补项、投影哈希和 provider 请求哈希。

第一次在线诊断运行 `tracegraph_b8192_qwen38_v1` 暴露了一个适配错误：相同 API 的重复成功读取被错误推断为 supersession，导致接口规范被截短并诱发重复查询。该运行在完成前停止，永久作为无效诊断，不进入任何成绩。修复后的 `v2` 不再从“同 API”自动推断成功结果互相取代，并增加了协议闭包后的二次预算收敛。

本地门禁为 17 个相关测试通过，包括真实 20 轮 AppWorld 历史的逐轮无模型重放、失败—重试—解决链、协议配对、重复成功读取和最终投影预算测试。

## 2. 五题结果

| 任务 | Full History | TraceGraph | FH 轮次 | TG 轮次 | FH total token | TG total token | TG 相对变化 | TG 压缩轮次 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `23cf851_1` | 1 | 1 | 7 | 7 | 40,104 | 41,142 | +2.6% | 0/7 |
| `6c2c621_1` | 1 | 1 | 20 | 11 | 171,027 | 81,539 | −52.3% | 3/11 |
| `3ab5b8b_2` | 0 | 1 | 9 | 11 | 79,120 | 124,710 | +57.6% | 3/11 |
| `383cbac_1` | 1 | 1 | 20 | 20 | 219,016 | 217,132 | −0.9% | 17/20 |
| `50e1ac9_1` | 1 | 1 | 12 | 18 | 96,581 | 211,070 | +118.5% | 12/18 |

聚合结果：

| 方法 | 官方通过 | Provider 请求 | Prompt token | Completion token | Total token |
|---|---:|---:|---:|---:|---:|
| Full History | 4/5 | 68 | 491,416 | 114,432 | 605,848 |
| TraceGraph | 5/5 | 67 | 541,199 | 134,394 | 675,593 |
| 相对变化 | +1 题 | −1.5% | +10.1% | +17.4% | +11.5% |

配对结果为双方都成功 4 题、仅 TraceGraph 成功 1 题、仅 Full History 成功 0 题、双方都失败 0 题。

## 3. 审计结果

- 五题共 67 个 context view 与 67 个 provider receipt 一一对应。
- 所有 67 个投影哈希和最终 provider 请求哈希均可由保存的原始历史逐轮重建。
- 图结构校验错误为 0，协议投影预算不可行为 0。
- `383cbac_1` 有 17/20 轮发生实际压缩，最强时只保留约 51.4% 的原始消息，官方成绩仍为 1。
- `23cf851_1` 全程没有触发压缩，因此它只是接入等价性对照，不能作为压缩收益证据。
- `383cbac_1` 的一次双空响应被保留为 16,384 completion token；随后 failure card 支持下一轮直接恢复到联系人查询。

机器可读结果与逐题哈希检查位于 `outputs/appworld_external_canary_260918/tracegraph_b8192_qwen38_v2/aggregate_summary.json`。

## 4. 如何解释

正确性方向是积极信号：当前方法在五题中没有丢掉 Full History 已通过的任务，并把 Full History 唯一失败题跑通。但样本只有五题、每题单次运行、模型采样和长 thinking 存在明显方差，因此不能把 5/5 对 4/5 解释为显著提升或因果收益。

成本方向是明确的警告：总体 total token 上升 11.5%，且逐题从减少 52.3% 到增加 118.5%。主要原因不是图计算开销，而是压缩改变决策轨迹后，交互轮数、重复读取、长 thinking 和空响应重试发生变化。仅比较单轮 prompt 长度会掩盖这种在线反馈效应。

此外，8192 是项目通用内容估算器的预算，不是 Qwen provider 的精确 token 上限。加入系统提示、聊天模板，并考虑估算器偏差后，单次实际 prompt 最高达到 12,304 token。正式 20 题实验前必须冻结模型精确 tokenizer 口径或经无任务标签校准的安全余量。

## 5. 下一步门禁

1. 用已保存历史做零模型调用的 Qwen tokenizer 校准，冻结 AppWorld 的 provider-capped 预算口径；不得根据任务成败单独调预算。
2. 对 `50e1ac9_1`（+118.5%）和 `3ab5b8b_2`（+57.6%）做动作链诊断，对照 `6c2c621_1`（−52.3%），确定成本分化来自遗漏、重查还是 thinking 方差。
3. 在打开 AppWorld 新冻结 20 题前，冻结该适配器版本、精确预算、空响应记账和重跑规则。
4. 新 20 题必须同时运行 Full History、Recent Masking、BM25、Rolling Summary 与 TraceGraph；五题 canary 不用于最终效应估计。

这些结果仍是开发 canary，不是论文主结果。
