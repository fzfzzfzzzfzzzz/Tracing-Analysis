# GLM 开发期错误签名边界复核

本包只复核 2 个 `error_signature` 粒度分歧。请只把 `cases.jsonl` 和
`reviews.template.jsonl` 交给 GLM，不要提供 `blind_mapping.private.jsonl`、模型总成绩或
当前金标来源。

逐行填写模板并另存为 `reviews.completed.jsonl`：

- `status` 改为 `reviewed`；
- 判断 A/B 是否在失败记录内无歧义地标识同一失败；
- `canonical_stable_signature` 必须逐字来自失败记录；
- `volatile_segments` 只列环境路径、地址等不稳定片段；
- `recommended_policy` 只能填写 `keep_exact_current`、
  `normalize_gold_then_one_way` 或 `semantic_signature_match`；
- 保持 `reviewer_kind=ai`、`human_reviewed=false`。

这是同一研究流程内的 GLM 开发审计，不是独立人工复核，不能用于正式 v1 声明。
