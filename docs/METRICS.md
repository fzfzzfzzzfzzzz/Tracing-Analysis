# 指标定义

调研报告第 8 节留空。本项目根据 H1–H6 将其补成以下可计算指标。

## 主指标

| 指标 | 定义 | 来源 |
| --- | --- | --- |
| Task success | benchmark reward / 成功率；多 trial 时报告 `Pass^k = mean_task[C(c_i,k)/C(n_i,k)]`，基础设施型中止从 `n_i` 排除 | τ evaluator / live run |
| Policy violation | 违反 domain policy 的 session 比例或次数 | τ evaluator / 独立标注 |
| Estimated selected-context tokens | 对 context manager 选中节点自身内容做 `content_estimate_v2` 确定性估算；不含 provider prompt history | 每个 context view |
| Agent provider input tokens | 每条 assistant 消息记录的 prompt/input usage 之和；用于真实 API 输入比较 | τ results |
| Compression ratio | `1 - estimated_selected_tokens / estimated_full_tokens` | 每个 context view |
| Cost | agent/user provider cost；只在上游提供时记录 | τ results |
| Runtime overhead | context 选择与图维护的毫秒数 | 本地计时 |

## 结构可靠性指标

| 指标 | 定义 |
| --- | --- |
| Evidence retention | 被选中或被可恢复摘要覆盖的 critical/final evidence 比例 |
| Unresolved failure retention | 未被 `resolves` 的 Error 是否由 active raw item 或可验证来源覆盖的 Failure Card 逻辑保留；不要求原始 tool result 每轮重放 |
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

在线 paired 分析以相同 domain、task、trial 为配对键，分别报告成功率差、估算 selected-context token 差和真实 agent provider input token 差的 bootstrap 95% CI；二元成功差使用 exact McNemar，并对同一参考条件下的多 comparator p 值执行 Holm step-down 校正。计量版本与修复边界见 [Token 计量修正](TOKEN_ACCOUNTING.md)。

## Phase 4：failure 后固定动作窗口

整段 session token 会被失败前轨迹、停止时点和 action 数混杂。Phase 4 对每个 eligible negative failure（actionable、policy-denied、malformed）固定观察其后最多 3 个 agent tool actions：

| 指标 | 定义 |
| --- | --- |
| Repeated same invalid | 原失败 producer 到窗口 action 存在 `retried_by(match_type=exact_signature)`，且该 action 的结果再次为 negative |
| Corrective retry | `match_type` 为 `structural_operation` 或 `argument_completion` |
| Admissible correction | corrective retry 产生至少一个结果，且结果不为 negative |
| Resolved within window | 原 failure 的 `resolved_by` target 是窗口 action 或其 result |
| Recovery action index | 首个 resolution action 在窗口中的 1-based 位置 |
| Post-failure provider tokens | 窗口 action 对应的唯一 assistant provider messages 的真实 input/output usage 之和 |
| Provider tokens/action | post-failure provider tokens 除以窗口内 agent tool action 数 |
| Target Card exposure | context-view Failure Card 的 `operation_scope` 与原 producer `operation_key` 一致的 action prompt 数与 token |
| Raw failure replay | 只在 view 显式提供 `raw_failure_messages_selected` 时判断；未提供时为 missing，不补 0 |

少于 3 actions 的事件标为 censored。多个 action 共享一个 assistant message 时，该 message usage 只汇总一次。旧自然矩阵上的窗口只作 post-hoc diagnostic；正式 H4 estimand 必须比较相同 conversation/environment hash 的 Card 与 Remove 分支。
