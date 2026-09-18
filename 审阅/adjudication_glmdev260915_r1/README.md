# compression_audit_v1 开发集 A/B 分歧裁决包

本包只包含已暴露的 7 个开发前缀，不是冻结测试集，也不能产生正式 v1 结论。逐条阅读 candidate.prefix.events，再比较 A/B；不得按多数或按模型成绩选答案。

请将 adjudication.template.jsonl 复制为 adjudication.completed.jsonl。若轨迹不能唯一支持完整的失败动作→失败结果→诊断→切换→替代动作→成功结果链，设 adjudication_status=rejected 并填写 reject_reason；否则设为 adjudicated。

failed/replacement 的动作名和 arguments 不再手写：只填写唯一的 tool_call source_event_id，导入器将从该事件自动复制 tool_name 和 arguments。error_signature 必须是失败结果中的原文；ordered_source_event_ids 必须按因果顺序、无重复，并包含每个独立必要事件。recoverability 必须根据公开历史能否恢复证据判定。
