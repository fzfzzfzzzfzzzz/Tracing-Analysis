# r10 三案例独立人工复核

请只依据 `cases.jsonl` 的公开事件和问题作答；不要查看模型答案、AI 标注或另一位审核者的结果。

每条都要确认：

1. 任务级失败从哪个公开事件开始；`failed_action` 应严格采用工具调用记录的工具名和参数，还是该问题必须允许内部概念动作。请在 rationale 解释。
2. 哪个直接结果承载稳定错误签名；错误签名应是能唯一识别该失败、但不依赖外层包装的最小文本。
3. 完整 audit-chain 的必需核心事件、允许作为有效支持的可选事件，以及虽相邻但不应视为证据的事件。
4. 特别注意：工具编辑后的 observation、最终验证后的 assistant 总结是否可作为有效额外引用，必须逐条判断，不能按事件类型一刀切。

将 `reviews.template.jsonl` 复制为 `reviews.completed.jsonl` 后填写。每条
`review_status` 改为 `completed`；事件 ID 必须逐字来自对应 case；不要删减或新增 prefix。
