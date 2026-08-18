# 指标定义

## Phase 5.2 伪标签与状态机门禁

同模型双遍只能报告 machine stability，不能称作独立标注者一致性。`safe_to_evict`
先二值化为 safe/not-safe，报告 observed agreement 与 Cohen’s κ；共识还要求 disposition、
terminal reason、relation targets、obligations 全部一致。伪标签前门为：185/185 prefixes
双遍合法、safe agreement≥0.80、κ≥0.60、consensus safe≥20、protected consensus safe=0。

task 4 held-out 只在前门通过后相对 machine consensus 报告：

- safe-to-evict precision = consensus safe 且预测 safe / 全部预测 safe；
- live-critical recall = 预测 critical 且 consensus critical / 全部 consensus critical；
- severe false-dead = 预测 safe 且 consensus critical 的单元数；
- consensus safe identified，以及同测试集 Phase 5.1 Grade A evicted span 数；
- determinism、future-suffix independence、archive、protocol、projection-send-forbidden、hash rate。

成本不使用 93 作为生命周期门禁。分别报告 machine-consensus opportunity prevalence、
opportunity-positive prefixes 的 paired token delta、全部 261 prefixes 的 mean/total/median/
benefit ratio，以及固定 policy/tool-schema token 和状态机运行/产物维护开销。provider 价格为
free 也不能写成 token cost 为零。

## Phase 5 LiveSubgraph / Prune 指标

Phase 5 继续使用下文五层成本，但主方法先拆成选择与投影两个可审计对象：

| 指标 | 定义 |
| --- | --- |
| Root determinism | 相同 prefix/query 的 root IDs、provenance 与 roots hash 完全一致 |
| Live-closure determinism | 相同 state/roots 的 live atom/node/span set 与 hash 完全一致 |
| Future-suffix independence | cutoff 后新增节点、边或旧派生 lifecycle 标记不改变旧 cutoff hash |
| Safe-to-evict precision | 被判 evictable 的 span 中人工/可执行 gold 为 dead-safe 的比例 |
| Live-critical recall | gold live-critical spans 被 live closure 保留的比例 |
| False-dead | policy、confirmation、receipt、pending/retry 或其他 critical span 被错误回收 |
| Protocol closure | final request 中 call/result 一一对应、方向正确且 parallel span 完整的比例 |
| Raw preservation | 保留 message 与 FullRaw 同 ordinal message 的逐字段完全相等比例 |
| Archive reactivation | query 改变后被 evict span 以相同 raw refs/payload hash 恢复的比例 |
| Treatment integrity | ContextView request hash、实际发送对象与 provider usage join hash 一致 |

`GDSC-Prune` 的成本下降只允许用完整 serialized request 和 provider-actual
input 报告；graph-selected 或“被删 span”大小不是 provider 节省。软预算不可行时不再
删除 live evidence，而是设置 `budget_infeasible=true` 与
`matched_budget_eligible=false`。源协议缺失/重复/逆序 result 或超过 provider hard
limit 时设置 `send_eligible=false`，不计为 agent task failure。

当前 deterministic fixtures 只支持工程 invariant，不支持任务成功、安全非劣性或
ContextSafetyBench validity 主张。`GDSC-Structured` 的额外降幅与额外伤害在 F5-G2
之后相对 Prune 单独计量。

F5-E0 的成本层在 outcome 前冻结为
`archived_complete_tool_span_count>=1 and message_count>=1`，共 185 个 prefixes。
F5-G1 要求该层至少一个 prefix 降低完整 serialized request，且 paired
`Prune−Raw` token delta 的中位数严格 `<0`。实际仅 4/185 下降，中位数为 `0`，因此
F5-G1 No-Go；不能改用“有 eviction 的四个 prefix”作为事后成本 estimand。

## Phase 5.1 evidence-ceiling 指标

P51-G0 在运行前利用 185 为奇数这一事实冻结覆盖门槛：若每个投影的 token delta 只能为
非正，要使配对中位数严格 `<0`，至少 `ceil(185/2)=93` 个 eligible prefixes 必须下降。

| 指标 | 冻结定义 | 观察值 |
| --- | --- | ---: |
| Grade A reduced prefixes | 只应用完整标量消费 overlay 后，serialized request 低于 Raw 的 eligible 数 | `10/185` |
| Grade B ceiling reduced prefixes | 把精确实体流/写入失效候选的来源 span 乐观假定可删后的 eligible 数；不能发送 | `36/185` |
| Grade A paired median delta | Grade-A−Raw 的逐 prefix serialized token 差中位数 | `0` |
| Grade B ceiling paired median delta | Ceiling−Raw 的逐 prefix serialized token 差中位数 | `0` |
| Grade A false-dead | root/policy/confirmation/receipt 在 Grade A view 中被删除的总数 | `0` |

因此 P51-G0 的两个覆盖/成本条件均失败。Grade B 的 1,513 条记录是跨 prefix 重复出现的
候选关系计数，不是独立样本，也不能用来替代 36 个实际缩减 prefix 的 estimand。

## GDSC 五层成本口径

以下五层不得混写；每层均报告逐 turn 与 session 累计值，并保存计量器版本。

| 层 | 定义 | 主要用途 |
| --- | --- | --- |
| Graph-selected | EventGraph 中被候选方案选择的原始/逻辑内容成本 | 解释图选择，不作 provider 节省主张 |
| Compiled | 表示变换后 payload 的成本 | 比较 raw、state、summary、guard 等表示 |
| Protocol-closed | 补齐 tool-call/result、user anchor 等协议闭包后的消息成本 | 检查闭包放大 |
| Serialized request | system、messages、tool schemas 与协议格式开销的完整请求成本 | 离线预算和 matched-cost 主口径 |
| Provider-actual | provider 返回的 input/output tokens 与 cost | live 效果主口径；缺失时不得估补为 actual |

`input/action` 是 provider input tokens 除以 agent tool actions；`session total` 同时报告 agent、user simulator、compressor 的输入/输出与净 token cost。软预算超限 turn 从 matched-budget estimand 排除但单独计数；provider hard limit 中止属于基础设施/协议失败，不当作 agent task failure。

## GDSC 可靠性与门禁指标

| 指标 | 冻结定义 |
| --- | --- |
| Hard coverage | query 要求的 hard atoms 在可见、验证过的表示中被覆盖的比例；archive-only 不计关键证据覆盖 |
| Representation equivalence | structured representation 的关键字段与 verified source atoms 完全一致的比例 |
| Provisional decision sufficiency | 无未来信息的 prefix reviewer/规则检查认为 bundle 足以支持下一动作的比例 |
| Conservative fallback | 因软预算不可行而返回 over-budget bundle 的 turn 比例 |
| Omission harm | 相同 prefix 下 Compiled/Drop 相对 Raw 新增 policy violation、不可逆副作用或关键决策错误 |
| Treatment integrity | 发送 request hash 与冻结 treatment artifact 一致，且 snapshot/state/query/hash 均可重放 |
| Risk calibration | task-held-out high-risk recall、ECE、Brier score 与 harm-positive 数 |

R2 gate 要求：关键字段等价 100%、provisional sufficiency ≥95%、相对 Raw 的 median serialized marginal cost 至少下降 30%、hard coverage 100%、conservative fallback ≤5%。统计风险模型还必须有至少 20 个 harm positives、high-risk recall ≥0.90、ECE ≤0.10 且 Brier 优于常数基线，否则 R4 使用 deterministic safety mask。

R3 要求 Compiled 相对 Raw 的 median provider input 至少下降 15%，不得出现 Compiled 独有 policy violation/不可逆副作用，representation-induced harm 最多 1/30 prefix，并至少有 5 个 Compiled/Drop discordant prefixes且多数支持 Compiled。

R4 的“τ³ 跨域 development positive evidence”是合取判定：GDSC-Core input/action 至少下降 15%且 bootstrap 95% CI 上界 `< 0`；session total 与净 token cost下降；success risk-difference 95% CI 下界 `≥ -0.05`；policy/collateral、action、repeat、recovery均不恶化；相对 ACON 存在 matched-cost reliability 或 matched-reliability token 优势；retail 与 airline 方向一致。任何一项未满足都不能写成正向结果。

## 历史 H1–H6 指标

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
