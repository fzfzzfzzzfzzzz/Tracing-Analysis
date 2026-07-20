# 数据与结果格式

## TraceGraph JSON

顶层字段：

- `schema_version`
- `session_id`
- `metadata`：benchmark、task、trial、reward、provenance 等
- `nodes[]`：类型、内容、step、生命周期、token、raw_ref、side_effect
- `edges[]`：source、target、类型、confidence、metadata

`tracegraph validate-trace <path>` 同时检查端点、边签名、side-effect 原始引用和 archived 节点可恢复性。

## Archive

归档路径为 `objects/<sha-prefix>/<sha>.json`，handle 格式为 `sha256:<digest>`。对象 envelope 包含 payload、metadata、写入时间和 digest。`verify-archive` 会重新计算所有 hash。

## 实验输出

- `manifest.json`：provenance、预算、manager、synthetic 标记、文件清单。
- `per_session.jsonl/csv`：每个 session × manager 的指标。
- `aggregate.json`：按 manager 聚合。
- `context_views.jsonl`：选中内容、理由和 source node。
- `lifecycle_analysis.json`：状态与转移频数。
- `oracle_upper_bound.json`：post-hoc 结构 Oracle。
- `online_replay.jsonl`：按 step 前缀回放，保证不读取未来节点。

Live paired matrix 聚合额外输出：

- `live_matrix_report.json`：完整性、每个 manager 的官方 success/stop/infra、selected-context tokens、task+trial 配对、exact McNemar 与 paired bootstrap。
- `live_matrix_sessions.csv`：每个真实 session 的 reward、termination、manager、budget、轨迹和 context-view 统计。

Phase 4 额外输出：

- `failure_chain_v2/annotation_key.json`：状态/原因拆分后的隐藏 prediction key；
- `failure_chain_v2/human_annotator_{a,b}.csv`：不含 Codex 标签泄漏的人工盲表；
- `failure_chain_v2/migration_audit.json`：v1 输入 hash、逐包行数和 lossy 映射计数；
- `post_failure_phase3_diagnostic/events.jsonl`：failure × next-3-action 的动作级记录；
- `post_failure_phase3_diagnostic/sessions.csv`：session 级窗口聚合；
- `post_failure_phase3_diagnostic/report.json`：完整性、条件聚合、输入文件 hash 与解释警告；
- trajectory store 的 `generation.json` / `generation_complete.json`：evaluator 前冻结的完整生成；
- `evaluation_attempts/attempt_NNNN/`：append-only manifest、raw responses 和成功/错误 result；
- `merged.json`：由 simulation id、generation hash 和 evaluation hash 约束的 reward 合并结果。

原始 benchmark 数据、archive 和正式 outputs 默认不入 Git，避免泄露数据或把昂贵实验产物混入源码。
