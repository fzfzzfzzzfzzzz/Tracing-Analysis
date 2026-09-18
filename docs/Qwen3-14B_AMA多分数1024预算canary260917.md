# Qwen3-14B AMA 多分数 1024-token canary（260917）

## 结论

有效结果以 `qwen3_14b_ama_scorecard_b1024_judge_repair_260917_r5` 为准：46/46 episode、177 次 provider 请求，`stop_reason=null`。它是单个已暴露受控前缀上的开发 canary，不是独立测试，也不能支持总体方法排序。旧 `r4` 含一条裁判输出协议故障，只保留作工程诊断，不进入方法结果表。

AMA 的官方普通状态记忆路径在 1024-token 外层预算下首次通过构建预算：完整输入经 tokenizer 验证后采用单 session，构建 prompt 为 5,883 token；最终状态记忆为 497 token，不超过 512-token ingest 子预算。此前 768-token 运行的 496-token 状态超过 384-token ingest 子预算，因此 768 的失败是冻结预算限制；本轮说明 1024 是该样本上的第一个可行预算点。

新多分数避免了旧二值评分把所有结果压成 0：

| 问题 | run validity | 总分 | 事实 | 语义因果 | 溯源 | 范围 | 安全 | 严格 audit pass |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 失败原因 | valid | 90.00 | 100 | 100 | 0 | 100 | 100 | 0 |
| 完整失败链 | valid | 72.00 | 60 | 66.67 | 80 | 100 | 100 | 0 |
| 当前状态 | valid | 33.33 | N/A | 0 | 0 | 100 | 100 | 0 |

`r4` 的完整失败链不是 AMA 构建或检索崩溃，而是 judge 把参考事实写进了 `contradictions`，该文本并非答案原文的精确子串，因此被本地协议校验拒绝。`r5` 增加了冻结的一次性裁判协议修复：第一次无效输出完整保留，第二次只重做同一答案、同一 rubric、同一 records 上的裁判，不重跑 AMA 构建、检索或回答，也不给裁判新证据。修复请求明确不携带第一次 verdict；若第二次仍无效，该 episode 仍记为 `integration_invalid`。

本轮恰有一条触发该机制，即 AMA 的完整失败链。首次错误仍是 `contradiction_not_exact_answer_substring`；一次修复成功，最终合法 verdict 将诊断和切换判为 supported、将成功结果判为 missing，并返回空 contradictions。因此该条现在是可写入的 72 分，而不是人工补分：事实 60、语义因果 66.67、溯源 80、范围 100、安全 100。结果仍显示 AMA 没有恢复完整成功结果，且没有通过严格 audit。

当前状态题把历史成功结果当成当前事实且来源指针不可解析，因此语义与溯源为 0；失败原因题恢复了错误签名和诊断语义，但没有覆盖评分所需的必要原始证据来源，因此溯源为 0。严格 `audit_pass` 仍是 0/2 valid episode；多分数显示的是部分能力差异，不把部分正确解释成完整可审计。

## 门禁与有效性边界

- Judge 规则样例门禁为 38/40、critical false positive 0，判定通过。
- 模型原始 calibration 为 Full History 22/24、Oracle 12/16，未通过原始模型门禁；本轮从启动时显式使用 development-only gate override，原始失败保持权威。
- 全运行有效性为 45 valid、0 integration_invalid、1 method_failure；method failure 来自无关内容诊断条件的回答协议失败，不属于 AMA 主条件。
- 裁判协议修复实际调用 1 次，1,523 input / 50 output token；它单列为 evaluator overhead，不计入 AMA 的构建、检索或回答成本。
- 本轮只证明 AMA 适配器在该样本和预算点能够真实构建、检索和回答，并证明新版分数卡能够区分部分正确结果；不证明 AMA 总体能力，也不证明 TraceGraph 优于 AMA。

## 成本记录

AMA 主条件的构建为 1 次调用，5,895 input / 443 output token；检索为 3 次调用，2,698 / 352 token；回答为 3 次调用，3,512 / 387 token。构建、检索和回答必须共同进入部署成本，不能只报告最终 402–742 token 的发送上下文。裁判及其协议修复属于评测器成本，和所有方法一样另列，不混入方法部署成本。

## 产物

- config：`configs/server_eval_qwen3_14b_ama_scorecard_b1024_judge_repair_260917_r5.json`
- prepared：`outputs/server_eval/prepared_qwen3_14b_ama_scorecard_b1024_judge_repair_260917_r5`
- run：`outputs/server_eval/qwen3_14b_ama_scorecard_b1024_judge_repair_260917_r5`
- 服务器与本地已递归核对 prepared 的 10 个文件和 run 的 14 个文件，SHA-256 全部一致。
- 实验结束后只停止了本轮命名的 Qwen3-14B tmux/vLLM 服务，GPU 4–5 对应进程已退出；GPU 0–3 上原有 Qwen3.8-27B 服务未触碰。

后续 Harness 构念效度、canonical/native 双输入、交叉 Harness 与结构消融计划统一记录在 [失败历史压缩 Benchmark 说明](失败历史压缩Benchmark说明.md#后续-harness-构念效度与方法中立性验证)。
