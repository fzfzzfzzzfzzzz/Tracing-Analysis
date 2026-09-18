# compression_audit_v1 独立轨迹标注包

两位标注者必须分别工作，不交换答案。将 `reviews.template.jsonl` 复制为 `reviews.completed.jsonl`，逐条填写 failure_chain、annotator，并把 annotation_status 改为 `annotated`。不得使用 candidates.jsonl 中的启发式提示； reviewer packet 已移除这些提示。测试 split 标签是预先冻结的分组，不代表答案。

每个 evidence ID 必须来自该条 prefix.events 的 source_event_id。完整链至少包含：失败动作、失败结果、诊断证据、切换决定、替代动作、成功证据。如果轨迹不能支持完整链，不要猜测；将 annotation_status 设为 `rejected` 并说明原因。
SWE-Gym 轨迹数据卡未声明数据许可；此包仅供受控研究标注，不得公开转发。
