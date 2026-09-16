# Qwen3-14B TraceGraph 原子链修复验证（260914）

## 结论

原子因果链修复通过了预注册的全部开发回归标准。Qwen3-14B 在 512 和 768
token 两个原失败预算上，TraceGraph 0.4 均达到 24/24 hard pass、24/24 audit
pass、24/24 必要证据可见；其中完整失败链题均为 8/8，回答引用严格为
E002→E003→E004→E005→E006→E007。修复前两个预算均为 16/24、链题 0/8。

这说明先前的 16/24 不是题目无效，也不是 Qwen3-14B 能力不足，而是 TraceGraph
低预算选择把完整失败链拆开。该结论仅适用于八个已暴露开发前缀，不能解释为独立
测试或正式泛化结论。

## 冻结设计

- 模型：`Qwen3-14B-rev-40c0698`，BF16，vLLM 0.8.5，tensor parallel 2，
  non-thinking，seed 20260972。
- 主方法：正式 `tracegraph_0_4`；预算 512、768；每预算 8 个前缀 × 3 类问题
  = 24 条主测试，没有事后排除。
- 对照不重跑，使用冻结的 BM25 结果；512 与 768 的回答输入上限分别预注册为
  22955 和 30677 tokens。
- 当前状态题必须显示 E023，且不得携带历史恢复链 E004–E007；完整链题必须在
  上下文中显示全部 E002–E007，不能只看模型答案是否碰巧正确。

## 修复内容

1. 从公开轨迹保守识别同一目标上的失败调用、显式错误、连续决定、替代调用和成功
   observation；目标不一致时拒绝建立恢复链，不读取 query、gold 或 causal role。
2. snapshot 和 materialize 均把因果依赖闭包作为原子选择单位，避免在有剩余预算时
   只加入部分链。
3. 明确的 causal/chain/reconstruct/sequence 问题关闭已选证据的依赖；明确的当前
   状态问题移除无关历史链并检索当前事实。
4. 通用词法检索至少需要两个命中；结构化字段、显式 ID 和当前状态事实保留窄例外。

相关自动化验证为 34 项定向测试通过、Ruff 通过、全仓 426 项测试及 11 项子测试
通过。服务端运行前还用精确 Qwen tokenizer 对 8×2×3 个主测试上下文做了零调用
回归，未发现失败。

## 主结果

|预算|hard / audit / evidence|完整链|平均上下文 tokens|回答输入 tokens|相对同预算 BM25 回答输入|
|---:|---:|---:|---:|---:|---:|
|512|24/24 / 24/24 / 24/24|8/8|205.42|19266|减少 16.07%|
|768|24/24 / 24/24 / 24/24|8/8|242.08|20146|减少 34.33%|

相对冻结 BM25，TraceGraph 在 512 的平均上下文由 359.12 降至 205.42
（减少 42.80%），在 768 由 680.88 降至 242.08（减少 64.45%），同时保持
相同的 24/24 质量。相对修复前 TraceGraph，512/768 均净恢复 8 条 hard pass；
回答输入还分别减少 3.65% 和 4.75%。

所有 16 条完整链答案都只引用 E002–E007，顺序一致，没有再引用当前状态 E023。
所有 16 条当前状态题均显示 E023，并排除 E004–E007。首次格式合法、judge 辅助
判断和安全检查在两个预算的主测试上也均为 24/24。

## 运行完整性

- 服务端完成 176/176 episodes，共 432 次 provider 请求；没有未知状态或重放。
- 规则裁判门禁 38/40、关键假阳性 0，门禁通过；每预算模型校准 40/40 完成。
- 离线独立执行 `score` 后，报告与服务端报告除
  `stop_reason=offline_rescore_no_new_calls` 外完全一致。
- 本地与服务器的 `report.json`、`episodes.jsonl`、`provider_ledger.jsonl`、
  `completed_jobs.jsonl` 和准备清单 SHA-256 全部一致。
- 结束后无 tmux、评测、vLLM 或 engine 进程；GPU 0/1 各 10 MiB，利用率 0%。

## 研究边界与下一步

该运行是机制回归，不是新数据验证。八个前缀已参与定位和修复，因此不能据此宣称
TraceGraph 已在未见数据上优于平面检索。按照 `pro建议260912.md`，下一步应冻结一轮
单模型强对照：recent masking、rolling summary、同档案权限 BM25、官方 ACON、官方
AMA BM25 profile 与修复后的 TraceGraph；先在开发集确定运行和成本口径，再用未参与
本轮修复的新真实轨迹做独立验证。不要直接启动 13 方法 × 双模型的 6624 条总矩阵。

## 产物

- 预注册：`outputs/server_eval/qwen3_14b_tracegraph_atomic_chain_repair_260914_r1.preregistration.json`
- 服务端原始运行：`outputs/server_eval/qwen3_14b_tracegraph_atomic_chain_repair_260914_r1/`
- 零调用复评分：`outputs/server_eval/qwen3_14b_tracegraph_atomic_chain_repair_260914_rescore_r1/`
- 运行配置：`outputs/server_eval/qwen3_14b_tracegraph_atomic_chain_repair_260914_r1.config.json`
- 模型启动 argv：`outputs/server_eval/qwen3_14b_atomic_chain_repair_launch_260914_r2.argv.json`
- 清理回执：`outputs/server_eval/qwen3_14b_tracegraph_atomic_chain_repair_260914_r1.cleanup.json`

核心哈希：`report.json` 为
`ac4d056b49ccdb63ff672752aca7f70302da4fc75c3f9297515fc64996d5af91`，
`episodes.jsonl` 为
`a1e7ed4ebc539773a73e57dc9f678dda7faa527aea8454cf3709e370e5570c88`。
