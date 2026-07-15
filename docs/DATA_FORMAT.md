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

原始 benchmark 数据、archive 和正式 outputs 默认不入 Git，避免泄露数据或把昂贵实验产物混入源码。
