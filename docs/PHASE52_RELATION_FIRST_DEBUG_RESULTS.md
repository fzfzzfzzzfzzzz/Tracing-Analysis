# Phase 5.2 relation-first 调试结果

> 日期：2026-08-10  
> 模型：阿里云百炼 `qwen3.7-plus`  
> 性质：小规模 development debugging，不是正式伪标注门禁

## 调试目的

e2 在一个包含 20 个工具 span 的 prefix 上连续把 `superseded` 错填进 `disposition`。本轮测试“模型识别生命周期关系，程序推导 disposition”能否减少结构错误。

旧 e2、GLM artifacts 和既有 No-Go 均保持不变。所有新尝试使用独立、create-only 输出目录。

## v1：准备预算中止

第一版关系 Schema 的说明文字使 370 个冻结请求预计输入达到 3,605,838 token，超过原 3,600,000 硬上限。准备过程在发送模型请求前停止，失败目录保留为 `outputs/phase5_2/e3_qwen37plus_relation_first_v1`，provider 请求为 0。

随后压缩说明文字，没有提高预算上限。

## v2：四值需要程度失败

v2 不再让模型输出 disposition，而是输出：

- `terminal_reason`
- `current_target_need = required | useful | not_needed | uncertain`
- relation targets、obligations、evidence 和 reactivation risk

程序再按固定规则推导 disposition。首先只复现 e2 失败的同一个 Pass A。模型两次都正确输出 `terminal_reason=superseded`，但同时把 `superseded` 错填进 `current_target_need`。第二次请求已经携带精确校验错误和允许枚举，错误仍完全复现。

- 请求尝试：2
- HTTP 200：2
- 有效请求：0
- usage：45,195 input / 2,825 output token
- pause report SHA-256：`b6deff7253e3e02ca1597676018f7ec6ad178ad454c973dd5908c22cbf2fb551`

这说明原样或带错误反馈的枚举重试仍不足以消除字段类别混淆。

## v3：布尔关系协议

v3 删除四值 `current_target_need`，改为两个布尔字段：

- `required_for_current_target`
- `requirement_uncertain`

`disposition` 完全由程序推导。只有生命周期终止原因明确、当前目标不需要、需求不确定性为 false、无 obligations 且无 reactivation risk 时，程序才输出 `safe_to_evict`。

首先复现原 20-span prefix 的 Pass A/B，两遍均一次合法完成；随后补到 10 个请求，即 5 个完整双遍 prefix。结果：

| 项目 | 调试结果 |
| --- | ---: |
| 请求 | 10 |
| 合法响应 | 10 |
| 无效响应/纠错请求 | 0 / 0 |
| 完整双遍 prefix | 5 |
| span 判断单元 | 55 |
| safe 二元一致率 | 0.800 |
| Cohen’s κ | 0.4954 |
| 全字段一致 | 12 / 55 |
| consensus safe | 8 |
| consensus uncertain | 43 |
| usage | 136,260 input / 8,610 output |
| 未折扣费用 | 0.047087 USD |

逐字段两遍一致率：

| 字段 | 一致率 |
| --- | ---: |
| reactivation risk | 1.000 |
| requirement uncertain | 1.000 |
| terminal reason | 0.891 |
| required for current target | 0.855 |
| obligations | 0.800 |
| relation target IDs | 0.418 |

原 20-span 困难 prefix 的 safe 二元一致率仅 0.60，全字段一致为 1/20。主要不稳定来源已经不是 JSON 或枚举格式，而是两遍对 relation target、obligations 和当前目标需要性的语义判断不同。

## 当前结论

v3 成功修复了已观察到的结构错误，因此支持“Qwen 能做这种任务，但需要更合适的接口设计”。不过语义稳定性仍不足：小样本 κ 低于预设 0.60，严格共识后 43/55 变成 uncertain。

这些数字只用于调试，不能视为正式门禁，因为 5 个 prefix 包含定向选择的历史失败样本，并不是完整 185-prefix 人口或预注册随机样本。不得据此报告总体生命周期机会率、状态机精度或剪枝有效性。

下一项合理调试是独立的 chunked-labeling 条件：保留完整 prefix 作为证据上下文，但每次只要求标注少量 span，从而测试长输出与 span 顺序是否造成 relation-target 不稳定。该改动会改变请求人口和成本，必须单独审批、核价和冻结，不能覆盖 v3。

机器标签仍不是人工 gold，不生成 hard-dead，不启动外部行为实验。Phase 4/5/5.1 的既有 No-Go 与历史门槛均未修改。
