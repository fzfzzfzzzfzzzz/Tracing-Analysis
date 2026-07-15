# 指标定义

调研报告第 8 节留空。本项目根据 H1–H6 将其补成以下可计算指标。

## 主指标

| 指标 | 定义 | 来源 |
| --- | --- | --- |
| Task success | benchmark reward / 成功率；多 trial 时报告均值与 Pass^k | τ evaluator / live run |
| Policy violation | 违反 domain policy 的 session 比例或次数 | τ evaluator / 独立标注 |
| Input tokens | context manager 选入下一轮的 token 数 | provider usage，缺失时用确定性估计 |
| Compression ratio | `1 - selected_tokens / full_tokens` | 每个 context view |
| Cost | agent/user provider cost；只在上游提供时记录 | τ results |
| Runtime overhead | context 选择与图维护的毫秒数 | 本地计时 |

## 结构可靠性指标

| 指标 | 定义 |
| --- | --- |
| Evidence retention | 被选中或被可恢复摘要覆盖的 critical/final evidence 比例 |
| Unresolved failure retention | 未被 `resolves` 的 Error 在 active context 中的保留率 |
| Constraint retention | 当前有效 Constraint 的保留率 |
| Evidence path preservation | 每个 final Decision 是否至少保留一条 `supports` 路径 |
| Archive recoverability | 具备 raw reference 的工具节点中，可通过 SHA-256 校验恢复的比例 |
| Repeated failed tool calls | 源轨迹中指向失败调用的 `retries` 边数；离线不伪造反事实值 |
| Unsafe removal count | 被 manager 排除、但硬约束判定不可安全移除的节点数 |

## H1–H6 对应关系

- H1：生命周期状态/转移计数。
- H2：状态对 safe removal 标签的预测；当前输出提供节点状态与硬约束标签，后续可直接训练 probe。
- H3：Evidence、Unresolved Failure、Evidence Path、Unsafe Removal。
- H4：live run 的 repeated failed tool calls；离线只报告观察值。
- H5：Compression ratio、input tokens、context/graph overhead。
- H6：Task success、policy violation 与相近 compression ratio 下的结构指标。

所有聚合项同时输出 `n`、mean 和 sample standard deviation。只有真实 evaluator 提供的 task/policy 值才能进入论文主表。
