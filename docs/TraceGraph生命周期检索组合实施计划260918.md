# TraceGraph 生命周期记忆与因果检索组合实施计划（2026-09-18）

## 目标

把原先分离的两种机制作为三个独立方法比较，不覆盖历史结果：

| 方法 ID | 含义 | 查询后归档读取 |
|---|---|---|
| `tracegraph_causal_retrieval` | 原 `GraphConstrainedPolicy` 两阶段因果检索的明确名称 | 是 |
| `tracegraph_lifecycle_cards` | `GraphLifecycleManager` 的常驻上下文与 FailureCard | 否 |
| `tracegraph_lifecycle_retrieval` | 生命周期常驻记忆 + 按需恢复完整原始因果闭包 | 有固定触发时才读取 |

旧 `tracegraph_0_4` 保留为兼容方法，不改写、不覆盖已有运行。

## 冻结的第一版组合规则

1. ingestion 阶段只读取公开 prefix，不接收未来问题、query type、rubric、gold 或因果角色标签。
2. `GraphLifecycleManager` 负责常驻上下文；未解决失败可表示为受 12.5% 子预算约束的 FailureCard。
3. 原始事件另外进入可审计档案；常驻卡片不能冒充原始事件可见性。
4. materialize 阶段只根据公开问题文本决定是否读取档案。
5. 第一版触发器为 `public_lexical_gate_v1`：当前状态且明确排除无关历史的问题禁止读取；包含预先固定的失败、原因、替代、证据或重建词时允许读取。
6. 触发后使用 `GraphConstrainedPolicy` 恢复完整 span/因果闭包；闭包超过 provider 上限时整题标记不可发送，不拆链、不静默截断。
7. 最终可发送记录重新使用 benchmark tokenizer 计数；LifecycleManager 内部 payload 计数不能代替最终包装后的预算检查。
8. 构建、常驻、读取、最终上下文、来源、触发词、丢弃的常驻记录和安全失败分别记账。

第一版词法触发器只用于暴露开发集上的机制验证。它不是跨语言或外部在线任务的最终触发器；正式外部接入前必须冻结更一般的、与任务输赢无关的充分性/恢复协议。

## 实施阶段

### P0：离线组合核心

状态：已完成。

- 新增独立 `LifecycleRetrievalAdapter`；
- 新增三个明确方法名并保留旧 alias；
- server-eval 配置与两份 schema 接受新方法；
- 当前状态题不读取档案；
- 历史失败链题恢复原始闭包；
- 小预算下安全拒绝不可分割链；
- 未解决失败使用 FailureCard，原始失败消息不进入常驻上下文；
- 新增 6 项定向测试，连同 server-eval 回归共 77 项通过。

零模型调用的 24 个开发长前缀结构检查结果：

| 方法 | 失败原因证据覆盖 | 完整链覆盖 | 当前状态覆盖 | 可发送 |
|---|---:|---:|---:|---:|
| Lifecycle Cards | 0/24 | 0/24 | 24/24 | 72/72 |
| Lifecycle + Retrieval | 24/24 | 24/24 | 24/24 | 72/72 |

这些受控历史中的失败已经解决，因此 LifecycleManager 按设计让旧 FailureCard 失效；
这解释了 Cards 条件没有历史审计证据，也直接说明按需归档恢复不是重复机制。
Hybrid 在两类历史问题上均触发读取，在当前状态题上 0/24 触发读取。

### P1：自建暴露开发集

状态：已完成模型运行与零调用评分口径修正。

只新增运行：

- `tracegraph_lifecycle_cards`；
- `tracegraph_lifecycle_retrieval`。

`tracegraph_causal_retrieval` 可以从同配置的旧 `tracegraph_0_4` 结果零调用重标识，但报告必须同时保留旧 method ID、源码哈希和迁移说明。若数据、模型、tokenizer、答题协议、评分器、公共构图或 provider transport 任一改变，则不得复用旧结果。

主要报告：

- 三类题分别的原始事件召回、语义分、溯源分和严格审计；
- 常驻 token、归档读取 token、最终上下文 token；
- 触发率、误触发率、不可发送率；
- Lifecycle Cards → Hybrid 的逐题增益，而不是只报告排行榜总分。

Qwen3.8-27B、1024-token、8 个主前缀的 136-episode 开发矩阵已于
2026-09-18 完成，362 次 provider 请求无错误。原始运行保留在
`/data/fangc/qwen38_27b_tracegraph_lifecycle_retrieval_b1024_native_260918_r1`。

随后以 `v0.2-development-multiscore-r11-soft-irrelevant-citation` 做零模型调用
复评分。可见但与问题无关的真实引用只降低 evidence precision 和连续分数；
缺少必要证据、伪造/不可验证引用、因果错误、事实错误、范围错误与安全错误仍是
实质失败。原有 `hard_pass` 保留为严格引用门禁，主要二元任务指标新增为
`substantive_pass`，上下文找回单列为 `retrieval_success`。

| 方法 | 问题 | 找回成功 | 实质成功 | 严格引用 | 总分 |
|---|---|---:|---:|---:|---:|
| Causal Retrieval | Audit chain | 8/8 | 8/8 | 8/8 | 100.00 |
| Causal Retrieval | Failure cause | 8/8 | 8/8 | 8/8 | 100.00 |
| Causal Retrieval | Current | 8/8 | 8/8 | 8/8 | 100.00 |
| Lifecycle Cards | Audit chain | 0/8 | 0/8 | 0/8 | 31.88 |
| Lifecycle Cards | Failure cause | 0/8 | 0/8 | 0/8 | 23.75 |
| Lifecycle Cards | Current | 8/8 | 8/8 | 8/8 | 100.00 |
| Lifecycle + Retrieval | Audit chain | 8/8 | 8/8 | 4/8 | 99.62 |
| Lifecycle + Retrieval | Failure cause | 8/8 | 8/8 | 8/8 | 100.00 |
| Lifecycle + Retrieval | Current | 8/8 | 8/8 | 8/8 | 100.00 |

复评分输出位于
`/data/fangc/qwen38_27b_tracegraph_lifecycle_retrieval_b1024_native_260918_rescore_r1`；
`rescore_identity.json` 记录 `new_provider_requests=0`、源报告/episode 哈希、评分代码
哈希与复评分 episode 哈希。原始运行文件未覆盖。

### P2：已暴露外部 canary

外部正式适配完成后，在已暴露任务上配对运行：

- Full History；
- `tracegraph_lifecycle_cards`；
- `tracegraph_lifecycle_retrieval`。

τ³、AppWorld 等在线任务必须在同一模型、服务配置、种子策略、超时和 evaluator 下重跑三者。旧在线结果只作历史诊断，不与新 Hybrid 跨日期直接组成优劣结论。

### P3：冻结

在打开未触碰数据前冻结：

- 方法名和源码哈希；
- 恢复触发协议；
- 常驻/检索/总预算；
- tokenizer 与 provider transport；
- 超时、重试、空响应和停止规则；
- 成功率、重复失败率、轮数、prompt/completion/total token 的主指标；
- 失败链审计只作为机制指标，不替代官方任务成功率。

### P4：未触碰外部集

只运行 P3 预注册矩阵。看到结果后不得修改触发词、预算或恢复规则并在同一批样本上重新声称确认性结果。

## 哪些结果必须重跑

| 情况 | 是否重跑 |
|---|---|
| 新 Hybrid 自身 | 必须 |
| 新 Lifecycle Cards 自建条件 | 必须 |
| 旧自建基线且所有输入与代码哈希不变 | 可复用并重新汇总 |
| 公共构图、评分器、prompt、transport 或 tokenizer 改变 | 所有受影响方法必须重跑 |
| 外部在线任务的 Hybrid 与旧方法比较 | 至少 Full、Lifecycle Cards、Hybrid 同条件配对重跑 |
| 未触碰确认集 | 冻结后按预注册矩阵首次运行 |

## 当前边界

当前已完成自建 server-eval 的组合核心、真实 Qwen3.8-27B 开发矩阵和零调用复评分；
尚未把 Hybrid 设为 AppWorld、τ³ 或 AMA 的默认方法，也尚未启动外部 canary。
现有外部结果和旧 `tracegraph_0_4` 结果均保持原样。
