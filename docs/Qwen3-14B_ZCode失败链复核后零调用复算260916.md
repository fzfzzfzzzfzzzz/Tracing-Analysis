# Qwen3-14B ZCode 失败链复核后零调用复算（260916）

## 复核门禁

`reviews.completed.jsonl` 共 7 条，prefix/query 覆盖、事件存在性、去重、公开时间顺序、最小四事件链、替代证据集合、相关证据超集、因果路径覆盖和无环性均通过程序化校验。

复核者为 ZCode，`reviewer_kind=ai`。因此本轮仍是开发用途，不构成人类验证、双人独立标注或正式 v1。

5 条复核与冻结失败锚点兼容，可以只更新 audit-chain rubric。另 2 条改变了失败锚点，必须与普通评分修复分开：

- `real:swe_gym:f31d17c3c14ddd51`：冻结金标从 E025 的修复过程缩进错误开始；复核从 E017 的任务级 `loss.requires_grad=False` 开始。
- `real:swe_gym:9b9e94e63c2c9e54`：冻结金标从 E051 的最后一次缺少 `strides` 开始；复核从 E041 开始，把 `spatial_dims → channels → strides → 成功` 作为一个阶梯链。

这两条不能通过增加“允许引用事件”静默覆盖；它们涉及失败 episode 的范围定义和其他查询金标。

## 零调用复算

以下为与冻结事实兼容的 5 条。复算只重用已保存结果，没有新模型请求。

| 条件 | n | 原引用通过 | 复核后引用通过 | 复核后 hard pass | 核心因果顺序通过 | 内容裁判通过 | 协议通过 |
|---|---:|---:|---:|---:|---:|---:|---:|
| thinking off / Full History | 5 | 0 | 3 | 2 | 3 | 3 | 5 |
| thinking off / Oracle | 5 | 0 | 4 | 2 | 5 | 4 | 5 |
| thinking on / Full History | 5 | 0 | 3 | 2 | 3 | 3 | 3 |
| thinking on / Oracle | 5 | 0 | 3 | 2 | 4 | 4 | 4 |

Oracle/thinking off 将引用通过从 3/5 提高到 4/5、核心因果顺序从 3/5 提高到 5/5，但 hard pass 仍为 2/5。未通过的一个 Oracle 引用案例是 `918561...`：模型把后续重复调用 E011–E015 一并引用，复核明确判为与目标失败 episode 无关；它同时未通过内容裁判。因此这不是缺一条金标引用，而是过度引用与语义混淆。

把 2 条锚点漂移案例也按单 AI 复核直接覆盖，只能作为诊断上界：四个条件的 hard pass 仍均为 2/7，不能改变结论。

## 当前结论

原来的全 0/7 确有评分结构问题，因为合理的相关证据边界可以恢复部分引用通过和 2 个端到端通过案例；不能再写成“模型完全没有能力”。

但检索或上下文缺失也不是唯一瓶颈。Oracle 已能让稳定 5 条的核心因果证据达到 5/5，最终 hard pass 仍只有 2/5，剩余失败主要来自语义内容、过度引用和 thinking-on 的结构化输出协议。thinking on 没有显示收益。

因此当前最稳妥的论文表述是：Oracle 提升证据可得性和链顺序恢复，但 Qwen3-14B 的答案选择与协议遵循限制了端到端收益；样本量仅 5–7 条且为 AI 标注开发集，不支持方法优劣或总体性能结论。

## 下一步

先独立裁决 2 条失败链范围。若裁决选择任务级 episode，需要同步更新失败链 schema、其他查询金标和问题锚点，再做零调用复算。正式 v1 仍需两位独立人类复核并冻结新测试集；这些已暴露案例只能留在开发集。

在这一步完成前，不需要重新启动 151 模型，也不应扩大主矩阵。

## 资产

- 已校验导入：`审阅/core_chain_review_260916_import_r3/import_report.json`
- 两条范围裁决包：`审阅/core_chain_review_260916_import_r3/chain_scope_readjudication_packet.zip`
- 冻结兼容零调用复算：`outputs/server_eval/qwen3_14b_adjudicated_oracle4096_260916_core_review_compatible_r1`
- 全 7 条单 AI 诊断复算：`outputs/server_eval/qwen3_14b_adjudicated_oracle4096_260916_core_review_all_ai_r1`
