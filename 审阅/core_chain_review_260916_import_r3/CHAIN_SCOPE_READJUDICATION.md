# 两条失败链范围裁决

这两条中，冻结金标选择了局部修复过程中的一次失败；新的核心链复核选择了任务级失败到最终成功的完整 episode。本包不含任何 Qwen 答案或分数。

请逐条比较 cases 中的 current_gold、completed_core_review 与完整公开事件。selected_chain_scope 填 local_subfailure 或 task_level_failure_episode；其余证据字段按最终选择填写。若题目文字不足以唯一限定范围，question_requires_explicit_anchor=true。若选择会改变失败动作、错误、替代动作或其他查询的事实金标，affects_other_query_gold=true。adjudicator_kind 只能填 human 或 ai。
