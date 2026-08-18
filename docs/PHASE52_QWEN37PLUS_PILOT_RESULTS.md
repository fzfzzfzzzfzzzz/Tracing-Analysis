# Phase 5.2 Qwen3.7-Plus 伪标注试跑结果

> 执行日期：2026-08-10  
> 条件：`e2_qwen37plus_pseudolabel_v1`  
> 模型：阿里云百炼 `qwen3.7-plus`，北京区，thinking disabled  
> 结论级别：development evidence；不是人工 gold，不授权 hard-dead 或外部行为实验

## 1. 本轮要回答什么

本轮先检查 Qwen3.7-Plus 能否稳定完成 Phase 5.2 的双遍机器伪标注。只有 185 个有历史工具 span 的 prefix 均完成两遍合法输出，才允许计算双遍一致性、生命周期机会率，并进一步在 held-out task 4 上比较符号生命周期状态机。

261 个冻结 prefix 中，185 个有历史工具 span，需要 370 个模型请求；另外 76 个被结构化记为“无机会”，没有发送给模型。请求只包含 cutoff 前的当前目标、policy、tool schema、工具调用/结果和事件关系；不包含 reward、未来后缀、F5/5.1 标签、剪枝结果或 token 收益。

## 2. 本地准备结果

- 冻结请求：370 个；每遍 1,092 个 `prefix × span` 判断单元。
- 估算输入：3,527,768 token，低于 3,600,000 硬上限。
- 单请求估算输入范围：6,141–23,976 token，均位于本轮记录的最低价格区间内。
- 状态机预演：261 个 prefix、1,092 个预测。
- determinism、future-suffix independence、EventGraph unchanged、archive、protocol、projection-send-forbidden：均为 100%。
- Qwen 请求固定为 `temperature=0`、`enable_thinking=false`、`max_tokens=4096`，且无模型 fallback。

上述结果只说明冻结输入和离线实现符合工程合同，不说明生命周期标签正确。

## 3. 真实执行结果

前 10 个请求全部 HTTP 200，且全部通过 JSON、枚举、opaque ID 和 prefix-only 映射校验，因此按授权继续全量。

续跑中出现两类结构化响应情况：

1. 请求 `pass_a_a85dec5e57e977e58ac2` 首次把非法值写入枚举字段；按预注册协议重试一次后通过。
2. 请求 `pass_a_842b87d6eaf6a211bf97` 在首次和唯一一次重试中都把 `superseded` 填入 `disposition`。`superseded` 是合法的 `terminal_reason`，但不是合法的 disposition；两次响应均在 S008–S019 重复该错误。

第二个请求因此耗尽单请求重试预算，采集器按计划停止，没有放宽枚举、手工修复标签或继续发送剩余请求。

| 项目 | 结果 |
| --- | ---: |
| provider 尝试 | 75 |
| HTTP 200 | 75 |
| 有效请求 | 72 / 370 |
| 完整双遍 prefix | 36 / 185 |
| 无效响应尝试 | 3 |
| 输入 token | 816,109 |
| 输出 token | 37,047 |
| 总 token | 853,156 |
| 未折扣估算费用 | 0.266035 USD |
| 外部行为模型会话 | 0 |

停止报告为 `outputs/phase5_2/e2_qwen37plus_pseudolabel_v1/pause_reports/pause_0001.json`，报告 SHA-256 为 `308307a3e61706185edaa18c6dda2093c374bd0afb05d8b01e6a2990c0d9d04e`。

## 4. 成功了什么，失败了什么

成功的是工程可行性：阿里云端点、鉴权、关闭 thinking、函数调用、usage 记录、脱敏响应保存和 opaque ID 回映射均能工作；前 72 个冻结请求形成了合法、可核验的标签 artifact，且没有 HTTP 限流。

失败的是本条件要求的“全量合法完成”。Qwen 在较长的 20-span calibration prefix 上连续两次混淆 `disposition` 与 `terminal_reason`，所以 185/185 双遍完成条件不成立。这是 e2 收集协议的质量 No-Go，不是网络失败，也不是生命周期方法本身的效果判定。

## 5. 目前能得出的结论

可以得出：

- `qwen3.7-plus` 能以较低成本完成大多数已运行的结构化伪标注请求；
- 当前提示词与函数 Schema 的组合不能保证全量 100% 枚举合规；
- 在 `temperature=0` 下对完全相同请求做一次原样重试，可能稳定复现同一种字段混淆，因此“原样重试一次”不足以解决这类系统性格式错误；
- e2 必须停在收集阶段，不能启动 Scheme B 或外部行为实验。

不能得出：

- 生命周期机会率是多少；
- 双遍一致率或 Cohen’s κ 是否达标；
- 符号生命周期状态机的 precision、recall 或 severe false-dead 是否达标；
- 生命周期建模方向有效或无效；
- 现有剪枝可以更激进，或可将任何机器标签转成 hard-dead。

原因是当前仅有按冻结顺序得到的 36 个完整双遍 prefix，它不是完整的 185-prefix 人口，也不是为中途分析抽取的随机样本。对它计算并外推总体机会率会产生选择偏差。

## 6. 后续边界

若继续，需要新建独立条件，而不是修改或覆盖 e2。可审批的改动包括：加强字段语义约束和反例、使用 provider 明确支持的严格结构化输出能力，或预注册“校验失败后的纠错请求”协议。不得把 `superseded` 自动改写为某个 disposition，也不得混合 e0/e1/e2 标签来补齐门禁。

Phase 4 R2/E0、Phase 5 和 Phase 5.1 的既有 No-Go 保持不变；历史 93 和 30% 门槛均未被追溯修改。
