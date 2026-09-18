# failure_episode_gold_v1 独立人工标注包

本包用于任务级失败 episode 的正式 A/B 独立标注。包内没有模型答案，也没有候选挖掘提示。

请复制 `reviews.template.jsonl` 为 `reviews.completed.jsonl` 后填写。A、B 两位标注者必须由不同的人独立完成，期间不得交换答案；不要参考旧版 GLM/ZCode 标注或另一份包。

每条 `source_event_id` 必须逐字来自该案例 `prefix.events`：

1. 先确定任务级初始失败动作与直接结果，不要用后续修复过程中产生的局部子失败替代它。
2. `repair_steps` 按时间填写。`action_source_event_id` 必须是 tool_call；每步结果必须晚于动作。只有最后一步可标为 `resolved`。
3. `error_signature` 必须逐字来自初始失败结果；动作名称和参数将在导入时从 tool_call 自动提取，不要手抄。
4. `required_core_source_event_ids` 是首选最小闭环；`optional_support_source_event_ids` 仅放有帮助但非必需的记录。
5. `chain_policy`、`audit_recovery` 和 `interactive_reacquisition` 均需列出合法最小证据集合、全部相关证据以及各集合内部的因果边。每条因果边必须遵守公开时间顺序。
6. `recovery_sequence` 要覆盖所有必要修复阶段，`resolution_evidence` 要说明最终成功是如何被观察到的。

能完整标注时设置 `annotation_status=annotated`，填写非空 `annotator`。若轨迹无法支持完整 episode，设置为 `rejected` 并填写 `rejection_reason`，不要猜测。

SWE-Gym 轨迹数据卡未声明数据许可；本包只用于受控研究，不得公开转发。
