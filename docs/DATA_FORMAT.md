# 数据与结果格式

## Phase 5.2 artifacts

Phase 5.2 的 create-only 根为 `outputs/phase5_2/e0_glm_pseudolabel_v1`：

- `frozen_config.json` / `pricing_snapshot.json`：固定模型、价格核验、预算、划分、门禁和 15 个 `ToolEffectSpec`；
- `requests/*.json`：370 个无凭据的冻结 provider request；
- `mappings/*.json`：pass-specific opaque event/span ID 与原 ID 的隔离映射；
- `request_index.jsonl`：request/prefix/pass/split/hash/估算 token 索引；
- `no_opportunity_prefixes.jsonl`：76 个无历史完整工具 span、provider requests=0 的 prefix；
- `raw_responses/*.json`：每次 HTTP 尝试的脱敏原始响应，文件名含 attempt；
- `usage_ledger.jsonl`：append-only HTTP status、usage、验证状态和响应哈希；
- `labels/*.json`：通过 schema/ID 校验的 opaque 与原 ID 双视图标签；
- `parsed_machine_labels.jsonl` / `machine_consensus.jsonl`：只在 370 请求完成后生成；
- `pseudolabel_summary.json` / `pseudolabel_gate.json`：机器稳定性前门；
- `state_machine/predictions.jsonl`、`prefix_rows.jsonl`、`summary.json`、`gate.json`：只在伪标签前门通过后生成；
- `manifest.json`：完成或门禁停止时的最终逐文件哈希清单；收集暂停时不存在，避免把部分结果伪装成完成 artifact。

机器标签字段为 `disposition`、`terminal_reason`、`relation_target_ids`、`obligations`、
`evidence_event_ids`、`reactivation_risk`。`machine_consensus` 只比较前四项；证据事件取两遍
并集，任一核心字段分歧时 disposition/reason 退化为 `uncertain/unknown`。

`LifecyclePrediction` 额外保存 source/target event、entity key、field scope、verifier、
confidence、obligations 和 provenance。所有预测与离线投影属于 development evidence，
不得写回原 EventGraph 或发送给 provider。

## Phase 5 lifecycle artifacts

Phase 5 使用 `phase5_liveness_config_v1`，manager 为
`lifecycle_graph_context/lifecycle_graph_context_v1`。所有新输出只写入
`outputs/phase5/`；`outputs/gdsc_r0_audit`、`outputs/gdsc_r2_1` 和
`outputs/phase4` 是只读保护根。

### `DecisionLifecycleGraph`

- `schema_version=decision_lifecycle_graph_v1`；
- prefix-only `DecisionStateGraph`；
- neutral `event_graph_hash`，排除可被旧 lifecycle engine 更新的派生状态；
- `event_records[]`：event ID、terminal/nonterminal status、确定性 reason、confidence、
  source atom IDs；
- `lifecycle_hash`：canonical JSON digest。

### `LivenessRoots`

- `schema_version=liveness_roots_v1`；
- lifecycle/query hash；
- roots：atom/event provenance、root reason、hard flag；
- uncertainty reasons；
- root atom/event IDs 与 `roots_hash`。

### `LiveSubgraph`

- `schema_version=live_subgraph_v1`；
- live/evicted atom、node 和 span IDs；
- `EventSpan` 的 message ordinals、parallel call IDs、raw refs；
- root/closure provenance；
- 每个 event 的 lifecycle reason；
- archive/protocol 不确定性记录；
- `live_subgraph_hash`。

### `LifecycleContextView`

- `schema_version=lifecycle_context_view_v1`；
- final messages/tools 与保留的原始 message ordinals；
- live/evicted node/span IDs；
- root、closure、lifecycle、fallback 和 uncertainty 记录；
- `projection_strategy=gdsc_prune_v1`；
- 五层 `PromptCost`、soft/hard budget 状态；
- protocol validity、`send_eligible` 与 request hash；
- provider actual usage 只能以同一 request hash append/join。

所有 dataclass artifact 均支持 canonical `to_dict()`、hash 校验和 fail-closed
round trip。空的 Phase 5 query-reactivation 扩展字段不会进入历史
`decision_query_v1` JSON，因此不改变旧 query hash。

### F5-E0 冻结与回放包

`outputs/phase5/e0_development_v1/` 是 append-only Phase 5 离线结果根：

- `tool_schemas.json`：本地 τ³ retail/airline native OpenAI tool schemas 和
  `artifact_sha256`；
- `development_prefix_manifest.json`：全部 261 个 outcome-blind prefixes、源文件与
  prefix/policy/schema hashes、结构特征、预冻结 F5-G1 阈值和 `manifest_sha256`；
- `prune_replay_v1/`：保留的首次审计尝试；其 archive equality 口径错误，不作权威
  F5-G1 裁决；
- `prune_replay_v2/prefix_rows.{jsonl,csv}`：逐 prefix FullRaw/Prune 请求 hash、
  lifecycle artifacts、协议/召回/reactivation 和完整 serialized cost；
- `prune_replay_v2/summary.json`、`f5_g1_gate.json`、`run_manifest.json`：聚合指标、
  冻结阈值逐项裁决与逐文件 hash。

所有 builder/evaluator 均拒绝已存在的 output root，不覆盖旧尝试。归档恢复比较的是
content-addressed raw provider payload 与其 SHA-256 handle；原始 provider envelope
不要求等于图中的规范化 node content。

## GDSC canonical artifacts

GDSC 新产物使用 canonical JSON（UTF-8、稳定 key 排序、无随机 UUID）计算 ID、state hash 和 request hash。旧 TraceGraph JSON 不迁移、不改 hash。

### `DecisionStateGraph`

- `schema_version`、`session_id`、`cutoff`、`event_graph_hash`；
- `atoms[]`：`atom_id`、kind、value、verification status、confidence、lifecycle、provenance event IDs；
- `edges[]`：稳定 edge ID、source、target、relation、provenance；
- `state_hash`：排除运行时间等非语义字段后的 canonical digest；
- `reducer_version` 与 parser provenance。LLM parser 产物只能标为低置信 provisional，不能直接成为 hard fact。

### `DecisionQuery`

- pending operation、retry context、candidate tools、required slots、policy scope；
- query hash 与所依赖 state/event IDs；
- 不确定时保留候选工具和扩大上下文的理由，不记录“猜定的单工具”。

### `PromptBundle`

- 最终 `messages` 与 `tools`；
- `representation_manifest[]`：source IDs、representation kind、payload hash、verification 与 omission-risk；
- `closure_provenance`、`compiler_decision_log`、`request_hash`；
- `costs`：`graph_selected`、`compiled`、`protocol_closed`、`serialized_request`、`provider_actual`；
- `budget`：soft budget、provider hard limit、`conservative_over_budget`、`matched_budget_eligible`；
- task、policy、tool schema、provider protocol、serializer、tokenizer、manager/risk artifact provenance。

`serialized_request` 对完整 system、messages、tool schemas 和协议格式开销计量。`provider_actual` 只在真实返回 usage 后回填，禁止用本地估算冒充。

### R0–R4 结果包

- `prompt_cost_profile.json`：逐 turn 五层成本及 192-view 错位复现；
- `benchmark_eligibility.json`：按域的 task/action/dynamic-history/headroom/lifecycle/snapshot/evaluator 检查；
- `risk_model.json`：冻结特征、task-held-out split、校准参数、指标和训练输入 hash；
- `prefixes/<prefix_id>/`：conversation、environment snapshot、EventGraph、DecisionStateGraph、DecisionQuery、tool schema、representation payload 与全部 hash；
- `gdsc_gate_report.json`：逐 gate 布尔值、观测值、阈值、输入 artifact hash 与停止原因；
- `request_artifacts/`：发送前冻结的 request；provider usage 以 append-only completion record 关联 request hash。

R2.1 诊断包位于 `outputs/gdsc_r2_1/`：

- `baseline_manifest.json`：历史输入文件 hash 与 embedded-hash 验证；
- `cost_attribution.json`：总体/分域中位数、发送对象一致率与可达性裁决；
- `cost_attribution_rows.csv`：261 个 decision points 的完整成本、leave-one-component-out marginal、fixed floor 与 constructive floor；
- `attainability_report.json`：互斥分支、blockers 与下一动作；
- `cost_attribution_component_medians.csv`、`fixed_cost_reachability.svg`：非可加组件中位数和固定成本可达性图。

`provider_requests.jsonl` 从 serializer v2 起同时记录 `prompt_request`/`prompt_request_sha256` 与 `invocation_request`/`invocation_request_sha256`。provider input token 与 PromptBundle request hash 只使用前者的 model/messages/tools；生成控制参数不混入 prompt token 口径。

完整 schema 与阶段状态见 [GDSC 预注册](GDSC_PREREGISTRATION.md)和 [GDSC 结果账本](PHASE4_GDSC_RESULTS.md)。

## 历史 TraceGraph 格式

### TraceGraph JSON

顶层字段：

- `schema_version`
- `session_id`
- `metadata`：benchmark、task、trial、reward、provenance 等
- `nodes[]`：类型、内容、step、生命周期、token、raw_ref、side_effect
- `edges[]`：source、target、类型、confidence、metadata

`tracegraph validate-trace <path>` 同时检查端点、边签名、side-effect 原始引用和 archived 节点可恢复性。

### Archive

归档路径为 `objects/<sha-prefix>/<sha>.json`，handle 格式为 `sha256:<digest>`。对象 envelope 包含 payload、metadata、写入时间和 digest。`verify-archive` 会重新计算所有 hash。

### 历史实验输出

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

## Phase 5.1 evidence-ceiling artifacts

`outputs/phase5_1/e0_evidence_ceiling_v1/` 是 create-only 目录：

- `prefix_rows.jsonl` / `prefix_rows.csv`：每个冻结 prefix 的 Grade A/B 关系、verifier、
  evidence/report hash、三种序列化成本和协议/安全检查；值本身只保存类型绑定的 SHA-256；
- `summary.json`：261/185 总体计数、Raw/F5/Grade-A/ceiling 成本和无 outcome/provider 访问声明；
- `p51_g0_gate.json`：预冻结 93-prefix 与 paired-median 门禁及 blockers；
- `run_manifest.json`：上述文件的逐文件大小和 SHA-256。

`LifecycleEvidenceRecord.may_generate_hard_dead=true` 只允许 Grade A 且
`confidence=1.0`。Grade B 记录必须为 false。`LifecycleEvidenceReport.report_hash` 覆盖 cutoff、
prefix graph hash、配置 hash 和排序后的全部证据记录。
