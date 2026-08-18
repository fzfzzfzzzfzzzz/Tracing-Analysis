# Phase 4/5 GDSC 主张—证据矩阵

> 版本：`phase5_claim_evidence_v1`（2026-07-28）

本表是写作与发布的 fail-closed 规则。计划、接口、单元测试和 adapter 存在只支持工程性主张；效果主张必须由冻结实验 artifact、原生 evaluator、完整成本和预注册 gate共同支持。

## Phase 5 主张

| ID | 允许的主张 | 最低证据 | 当前状态 |
| --- | --- | --- | --- |
| F5-C0 | Phase 5 在未改写 Phase 4 工作树和 artifacts 的 checkpoint 上开始 | dirty diff、可恢复 patch/untracked 包、逐文件 artifact hash、pytest、ruff、diff check | **允许工程性主张**；F5-WP0 checkpoint 已生成，147 tests 与 hash audit 通过，外部 sessions 为 0 |
| F5-C1 | LiveSubgraph roots/closure 是确定性、prefix-safe 且可审计的 | same-prefix/hash、future-suffix independence、edge direction/whitelist、closure provenance tests | **允许 fixture 范围工程主张**；新 deterministic tests 通过，尚无人工构念证据 |
| F5-C2 | `GDSC-Prune` 只删除 lifecycle-dead spans，逐字保留 live raw spans 并保持协议闭包 | raw byte/content equality、完整 span eviction、parallel/missing/out-of-order tool protocol fixtures、逐 span reason | **允许离线工程主张**；261/261 prefix 的 determinism、future-suffix、protocol、root/critical recall 与 request hash 均通过；不构成在线效果证据 |
| F5-C3 | 被 evict 的证据可从 archive 恢复，并能随 query 变化重新激活 | payload/hash round trip、query-change reactivation、tamper fail-closed | **允许离线工程主张**；权威 replay-v2 的实际 evicted spans 为 4/4 reactivation/hash 通过；不构成模型能自主恢复的效果证据 |
| F5-C4 | `GDSC-Prune` 降低完整 provider input | F5-E0/E2 同 prefix Raw/Prune；serialized 与 provider-actual paired CI；完整固定成本与净开销 | **F5-G1 No-Go**；185 个预冻结 eligible prefixes 仅 4 个下降，paired median Prune−Raw token delta=`0`，未达到冻结的 `<0` 门槛；禁止筛选有利 prefix、放宽门槛或改写为正向结果 |
| F5-C5 | lifecycle dead/live 构念达到预注册可靠性 | 两位独立人工标注、live-critical recall、safe-to-evict precision、severe evidence false-dead 为 0 | 未运行；机器 fixture 不能替代正式人工构念证据 |
| F5-C6 | ContextSafetyBench 能识别 representation-induced harm | snapshot 100%、SeededViolation、CriticalDrop/NoncriticalDrop、native state-diff evaluator、无 outcome leakage | 未实现/未判定 |
| F5-C7 | Prune 相对 Raw 非劣且优于 matched unstructured deletion | F5-G3/G4 的 common-prefix discordance、success/harm margins、双环境与完整 usage/cost | 未运行；无外部调用授权 |
| F5-C8 | `GDSC-Structured` 在 Prune 之上有独立增量价值 | F5-G2 后单独实现；equivalence/provenance 100%；相对 Prune 的额外成本与伤害门禁 | **被 F5-G1 No-Go 阻止**；当前不得实现 Structured 条件或与 Prune 合并报告 |
| F5-C9 | Benchmark 可作为 publication-level 独立贡献 | 两环境、多模型、两位人工、正负控制、功效充分 confirmatory set、可独立接入 | 本轮早期范围外；禁止声称 |
| P51-C1 | 纯本地结构化 lifecycle evidence 能增加旧前缀上的安全剪枝覆盖 | outcome-blind 全 261 prefixes、Grade A 完整性 verifier、determinism/future-suffix/protocol/request-hash 100%、critical false-dead=0 | **允许离线工程主张**；Grade A 使 eligible reduced prefixes 从 4 增至 10，不能外推为在线效果 |
| P51-C2 | 旧 traces 中当前可精确匹配的候选关系足以改变旧全体 paired median | 审计前冻结的“当前精确匹配候选集上限”；旧全体中位数改变在数学上至少需要 93 个 eligible prefix 下降 | **不允许/门禁失败**；候选集上限仅 36/185，中位数仍为 `0`，裁决 `stop_old_corpus_path`。93 只解释旧成本统计，不再是生命周期模型门禁 |
| P51-C3 | 新运行时 `effect_scope`、entity version、receipt/consumption 遥测值得另立研究 | 独立预注册、生命周期机会率、held-out 安全精度和后续人工构念复核 | **Phase 5.2 已授权方案 A 的低成本伪标注与纯离线建模**；不授权新运行时遥测采集、Scheme B 或行为实验 |
| P52-C1 | 同一 GLM 双遍伪标签具有足够机器稳定性，可用于快速机会率估计 | 185/185 双遍合法；safe agreement≥0.80；同模型 κ≥0.60；consensus safe≥20；protected safe=0 | **尚不可主张**；370 个冻结请求仅 1 个有效，随后 Z.AI 1305 rate limit，质量门禁不可计算。这是外部可用性暂停，不是质量 No-Go |
| P52-C2 | 方案 A 状态机满足 prefix-only、确定性、archive/protocol 和禁止发送合同 | 261 prefixes、1,092 predictions；determinism/future-suffix/EventGraph unchanged/archive/protocol/send-forbidden 全部 100% | **允许离线工程主张**；无标签全量预演全部通过，但不能据此声称生命周期语义正确 |
| P52-C3 | 方案 A 在 task 4 held-out 上可靠识别机器共识生命周期机会 | safe precision≥0.90；live-critical recall≥0.95；severe false-dead=0；识别≥5 且超过 Phase 5.1 Grade A | **尚不可主张**；伪标签前门未完成，held-out evaluator 不得运行，不得生成 Scheme B 或行为实验 |

## Phase 4 冻结主张

下表保持 `gdsc_claim_evidence_v1` 的历史门槛和裁决，不因 Phase 5 新问题而追溯修改。

| ID | 允许的主张 | 最低证据 | 当前状态 |
| --- | --- | --- | --- |
| C0 | 旧 Phase 1–4 与 `full_ours` 可复现且未被 GDSC 追溯修改 | 旧 artifact hash 清单、兼容回归、Git diff audit | 工程兼容回归通过；历史结果保留 |
| C1 | GDSC 具有确定性、无未来泄漏的 DecisionStateGraph | stable hash、same-prefix、future-suffix、neutral reducer tests | 允许；相关 tests 通过 |
| C2 | PromptBundle 对最终可发送请求成本负责 | 五层 profiler、tool schema/协议 fixture、request hash 与实际发送对象一致 | 允许工程性主张；R2.1 的冻结 baseline 与 τ³/LiteLLM runtime prompt hash 均为 261/261；prompt 与 invocation envelope 已分开，旧 192 views 的 provider-actual 保持 null |
| C3 | 多表示编译保持 hard facts 与关键字段 | 100% hard coverage/equivalence、provenance、tamper/illegal-guard negative tests | 允许；261 points hard coverage 100%，structured equivalence 5372/5372 |
| C4 | 编译表示离线成本优于 Raw | E1/E4 冻结样本；median serialized marginal cost ≥30%下降 | **未达到且在冻结约束下不可达**；历史 R2 为 14.956%，R2.1 runtime 口径为 15.723%；完整 policy + native schemas 的乐观上界仅 28.451%，禁止声称 |
| C5 | omission-risk artifact 可用于 R4 | task-held-out、≥20 harm positives、recall/ECE/Brier 全部门禁 | 未达到；无合格 harm gold，保持 deterministic risk |
| C6 | Compiled 的局部因果表现优于 Drop 且不劣于 Raw | 30-prefix R3，treatment/snapshot完整，安全门禁通过，≥5 discordant且多数支持 Compiled | 被 R2/E0 阻止，未运行 |
| C7 | GDSC 在 τ³ 降低 provider input 且成功率非劣 | 160-session R4、paired CI、完整 usage/cost、原生 evaluator、双域一致 | 被 R2/E0 阻止，未运行 |
| C8 | GDSC 优于 official ACON 的 Pareto 点 | hash-pinned ACON、无 fallback、compressor成本完整、matched-cost/reliability比较 | 被 R2/E0 阻止，未运行 |
| C9 | τ³ 跨域 development positive evidence | C7、C8及全部 R4 合取安全/恢复门禁 | 未达到 |
| C10 | 最终 AAAI 双 benchmark/正式 construct validity | 第二 primary benchmark + 两位独立人工 gold + C9 | 本轮范围外；禁止声称 |
| C11 | archive handle 可被模型可靠恢复并改善结果 | 模型可用恢复工具、干预实验、安全与成本评估 | 本轮未实现；禁止声称 |

## 允许沿用的历史主张

- Phase 3 P1 的确定性 Failure Card 干预只能作为受控机制证据；其中的本地 serialized token 不是外部 provider usage。
- Phase 3/P3b-A 的 trajectory/evaluator 解耦、Failure-chain v2 与 next-3-action 回放是已验证基础设施。
- 旧 Card-only P4 为 No-Go：token CI、success non-inferiority、Remove 机制和人工 construct gate 未通过。
- 这些历史结论不否定 GDSC，也不能当作 GDSC 的正向证据。

## 禁止性解释

- 不把 `117 passed` 写成 R0/R1/R2 实验通过；它只是改造前基线。
- 不把 graph-selected、compiled 或 protocol-closed token 降幅写成 provider-actual 节省。
- 不把 provider 单价为零写成 token cost 为零。
- 不用 task selection、阈值下调、追加样本或自动补跑挽救未通过门禁。
- 不从 offline view 推断 task success、policy compliance 或 collateral damage。
- 不把同模型同会话 Codex A/B 写成两位独立人工 gold。
- 不把 τ³ retail/airline 两域写成两个独立 benchmark。

## 写作模板

通过某一工程 gate 时，可写：

> 在冻结 fixture 上，GDSC 的 `<invariant>` 通过 `<n>/<n>` 检查；这验证实现合同，不构成在线任务效果证据。

门禁未通过时，应写：

> 在预注册的 `<stage>` 中，`<metric>` 为 `<value>`，未达到 `<threshold>`，因此停止后续 `<stage>`；未追加样本或调整阈值。

只有 C9 全部证据齐全时，可写：

> 结果支持 τ³ retail/airline 范围内的跨域 development positive evidence；第二 benchmark 与正式人工 construct gold 仍未完成。
