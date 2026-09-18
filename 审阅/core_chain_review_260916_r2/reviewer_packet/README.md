# 失败链评分结构独立复核包

本包只复核 7 条已暴露开发案例的证据结构，不是冻结测试集，也不含模型答案、模型分数或自动生成的建议答案。请从干净副本分别交给两位复核者。

将 reviews.template.jsonl 复制为 reviews.completed.jsonl，逐条填写。review_status 只能是 reviewed 或 rejected。required_core_event_ids 应是回答失败链不可缺少的最小闭环，通常依次覆盖：失败动作、直接失败结果、替代动作、成功/解决证据，因此允许合法的 4 事件链。optional_support_event_ids 是有帮助但不应成为硬门槛的诊断、决策或前置上下文。alternative_evidence_sets 用于多个同样充分的证据组合；relevant_evidence_ids 是允许附带引用而不扣分的全部相关事件。causal_constraints 使用 [前事件ID, 后事件ID]；若有多个 alternative_evidence_sets，还需在 causal_paths 中为每个集合分别填写 evidence_ids 与 constraints。

failure_anchor_event_id 必须唯一指向题目所问的失败尝试；若当前问题无法唯一定位，question_unambiguous_for_anchor=false 并说明歧义。不得参考任何模型作答表现来改标。
