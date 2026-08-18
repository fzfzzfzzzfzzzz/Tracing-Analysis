# Tools Tracing 项目 Agent 交接文档

> **2026-08-01 Phase 5.2 执行中（外部限流暂停）：** P52-WP0 checkpoint 已冻结，SHA-256
> 为 `0827bc97ffe661156ab6120a0bebe8e56a5957f1104a63a8c98989cabd54f0d0`。方案 A 的
> GLM 双遍盲化协议、15-tool `ToolEffectSpec` 注册表、实体/字段状态机、可恢复 runner、
> 共识/门禁和 held-out evaluator 已实现。冻结集为 261 prefixes，其中 185 个调用
> `glm-4.7-flash` 两遍、76 个结构化为无机会；每遍 1,092 个 call/result 单元，共 370
> 请求，估算输入 3,528,760 token，低于 3,600,000 硬上限。全语料无标签预演覆盖
> 1,092 个预测，determinism、future-suffix independence、EventGraph unchanged、archive、
> protocol、projection-send-forbidden 均为 100%。首个真实请求成功；随后同一 Pass B 单元
> 四次收到 Z.AI HTTP 429/code 1305。2026-08-01 再探测时 Pass B 成功，但下一条请求立即
> 再次 429，说明限流只短暂解除；当前 7/400 HTTP attempts、2/370 valid，累计 usage 为
> 16,085 input / 1,016 output，已保存脱敏响应和 `pause_0002`，收集是“可恢复暂停”，不是质量
> No-Go。恢复时只能先用 `scripts/record_phase52_pricing_snapshot.py` 记录追加式官方免费价格
> 复核，再把新快照路径和哈希交给原 runner；禁止付费、第二模型、fallback、
> Scheme B 或行为实验。最新全量验证为 `190 passed`，Ruff/compileall/diff-check、配置 schema、
> 370-request regeneration 和 partial-artifact hash audit 均通过。完整说明见
> [`docs/PHASE52_IMPLEMENTATION.md`](docs/PHASE52_IMPLEMENTATION.md)。

> **2026-08-01 Phase 5.1 P51-G0 Stop：** 已按独立增量计划完成纯本地生命周期证据
> ceiling audit。全部 261 个冻结 prefixes 均纳入，185 个 cost-eligible；Grade A
> `complete_scalar_consumption` 将有缩减的 eligible prefixes 从 F5 的 4 个提高到 10 个，
> Grade B 乐观上限也只有 36 个，低于改变配对中位数所需的预冻结 93 个，且中位数仍为
> `0`。P51-G0 判定 `stop_old_corpus_path`。这不推翻结构化运行时证据的方向，而是说明旧
> traces 不含足够的 effect scope/version/receipt/consumption 事实；不得继续在旧语料调规则，
> 也不得自动进入新遥测采集。结果见
> [`docs/PHASE51_LIFECYCLE_EVIDENCE_RESULTS.md`](docs/PHASE51_LIFECYCLE_EVIDENCE_RESULTS.md)。
> F5-G1 仍为 No-Go，Structured/外部试验仍未授权，provider generations 仍为 0；最新全量
> 验证为 `177 passed`、ruff/compileall/diff-check 通过。

> **2026-07-28 Phase 5 F5-G1 No-Go：** outcome-blind development manifest 已冻结全部
> 261 个旧 decision prefixes（30 sessions；185 个 cost-eligible）。离线 replay-v2 的
> determinism、future-suffix、protocol、root/critical recall、archive reactivation 和
> request hash 均为 100%，policy/confirmation/receipt false-dead 为 0；但仅 4/185 个
> eligible prefixes 降低完整 serialized input，paired median Prune−Raw token delta 为
> `0`，未达到预冻结的 `<0` 门槛。因此停止在 F5-G2、GDSC-Structured 与所有外部 pilot
> 之前；不得筛选有利 prefix、放宽阈值或覆盖 replay-v1/v2。权威结果见
> [`docs/PHASE5_RESULTS.md`](docs/PHASE5_RESULTS.md)；外部 sessions 仍为 0。

> **2026-07-28 Phase 5 规划通知：** 项目级下一阶段已经明确为 [`第五阶段修改计划.md`](第五阶段修改计划.md)。新主线把 GDSC 收敛为 agent 内的生命周期图上下文模块：先以 `GDSC-Prune` 验证 dead tool trace 的安全回收，再独立检验 `GDSC-Structured` 的 Live Subgraph 结构化投影。本文下述 Phase 4 R2/E0 No-Go、数值、artifact 和停止事实保持历史有效；Phase 5 是新预注册版本，不追溯降低或改写旧30%门槛。该规划文档本身不授权新的外部模型会话。

> **2026-07-21 研究重置通知：** 本文件主体记录的是 Phase 4/P3b-A 完成时的历史快照。关于“下一步只能做 Failure Card common-prefix fork”、Card-only P3b-B、禁止扩展一般决策状态表示/benchmark/baseline，以及以 Card negative-results 收口的指令，均已被 [`第四阶段修改计划.md`](第四阶段修改计划.md) v2.0 取代。客观结果、代码位置、provenance 和外部 API gate 仍然有效；新的主线是 Graph-Constrained Decision-State Compiler（GDSC）。

> 快照日期：2026-07-20（Phase 4 P3b-A 更新）
> 仓库：`E:\科研\Tools Tracing`
> 当前测试状态：GDSC 改造前基线 `117 passed`；R2 完成时 `144 passed`；R2.1 后最新全量 `147 passed`，ruff 全仓通过
> 当前研究判定：GDSC R0 development gate 通过，R1 工程完成；R2 因 median serialized reduction `14.956% <30%` 为 No-Go。R2.1 的真实发送口径为 `15.723%`，而完整 policy + native schemas 固定成本下界的最大 median 降幅仅 `28.451% <30%`，故裁决为不可达、分支 B，不实现 v1.1。E0 也因每域仅 5 tasks、median actions 7/6 及证据缺失为 No-Go。继续停止在 R3 前，外部会话 0/340。

## 0. 接手后的第一条原则

不要原样扩大现有 P3 Card 矩阵，也不要把 Codex A/B 改名为人工标注。旧 P3b-B 仍不得直接运行。

P3 的**机制可识别性基础设施**已经在 Phase 4/P3b-A 修复：状态/原因已拆分，trajectory/evaluator 已解耦，next-3-action 指标已在冻结矩阵回放。这些成果保留为 FailureGuard 机制切片与新 common-prefix 框架的基础。GDSC 的 PromptBundle、DecisionStateGraph、benchmark eligibility 与多表示编译器现已实现；由于 R2/E0 双门禁失败，外部 representation fork 与 R4 matrix 不得执行。

### 0.A GDSC 执行与交接规则

- `117 passed` 是 GDSC 改造前冻结基线；新增实现后的验证结果为 `144 passed` 与全仓 ruff 通过。
- `TraceGraph`/EventGraph 和旧 `full_ours` 行为是兼容边界，不追溯改写；GDSC 使用新 manager `decision_state_compiler`、版本 `gdsc_core_v1`。
- 五层成本、R0–R4 门禁、样本冻结和停止规则以 [`docs/GDSC_PREREGISTRATION.md`](docs/GDSC_PREREGISTRATION.md) 为准；任何主张先查 [`docs/CLAIM_EVIDENCE_MATRIX.md`](docs/CLAIM_EVIDENCE_MATRIX.md)。
- GDSC 的实际运行状态只写入 [`docs/PHASE4_GDSC_RESULTS.md`](docs/PHASE4_GDSC_RESULTS.md)。没有产物 hash、运行 manifest 和 gate 报告的项目必须保持“未运行/未判定”，不得从计划或单元测试推断为正向结果。
- 本轮经验范围固定为 τ³ retail/airline development evidence。没有第二 primary benchmark 和两位独立人工 gold 时，即使 R4 全部通过，也不得写成最终 AAAI 双 benchmark 或正式 construct-validity 结论。
- 外部运行固定 fail closed：仅 `zai/glm-4.7-flash` 免费额度，无付费或模型 fallback；启动前重核价格并把证据写入 manifest。E0、R2 或 R3 任一门禁失败即停止后续矩阵。

### 0.1 Phase 4/P3b-A 最新状态

实施计划与结果：

- `第四阶段修改计划.md`
- `docs/PHASE4_RESULTS.md`
- `outputs/phase4/phase4_gate_report.json`

已完成：

- failure-chain v2：60/60 非破坏迁移，2 个 lossy expiry 显式审计，两份干净人工盲表；
- provisional v2 诊断：retention precision `0.176`、recall `1.000`、expiry-cause accuracy `0.744`；仍不是人工 gold；
- immutable generation artifact、append-only evaluator attempts、raw response 保存和 simulation/hash reward merge；
- 零 API 故障注入 10/10 checks 通过；
- Phase 3 冻结矩阵 next-3-action 回放：60 sessions、49 eligible events、45 有 action、98/98 action-view 对齐；
- Card 条件：7 events、6 有 action、目标 Card 可见 12 次、repeat/repair/resolve 为 `0/1/1`；仅为 post-hoc，不是因果结果；
- `phase4.engineering_gate_passed=true`；
- `phase4.empirical_claim_gate_passed=false`；
- `p3b_b.go_gate_passed=false` 且 `external_api_execution_authorized=false`。

新增产物必须保留：

- `outputs/phase4/failure_chain_v2/`
- `outputs/phase4/post_failure_phase3_diagnostic/`
- `outputs/phase4/trajectory_protocol_audit.json`
- `outputs/phase4/phase4_gate_report.json`

下文第 5 节是本次更新前的 P3b 设计依据；其中 P3b-A 已执行完毕，P3b-B 仍未获授权。

## 1. 接手须知

### 1.1 工作树不是干净状态

当前仓库包含尚未提交的第四阶段修改和新增文件。这些修改是本阶段工作的主体，不是可随意清理的临时文件。

禁止执行：

```powershell
git reset --hard
git checkout -- .
git clean -fd
```

不要覆盖未知修改，不要删除未跟踪文件，不要暂存或提交，除非用户明确要求。

Windows Git 可能触发 dubious-ownership。使用一次性参数，不修改用户全局配置：

```powershell
git -c "safe.directory=E:/科研/Tools Tracing" status --short
git -c "safe.directory=E:/科研/Tools Tracing" diff --check
```

### 1.2 `outputs/` 中的产物必须保留

大量真实实验结果、trace、日志和失败批次位于 `outputs/` 或 vendor 的 simulation data 下，其中部分受 `.gitignore` 管理。Git 不显示不代表可以删除。

尤其保留：

- `outputs/phase3/p1_interventions_v2/`
- `outputs/phase3/p2_failure_chain_v1/`
- `outputs/phase3/plans/`
- `outputs/phase3/gates/`
- `outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/`
- `outputs/phase3/aborted_attempts/`
- `outputs/phase3/logs/`
- `outputs/tau3_live/`
- `vendor/tau3-bench/data/simulations/p3_card_retail_codex_*`

失败批次是 provenance 和问题复盘的一部分，不应删除或混回正式分析。

### 1.3 文档和结果的可信优先级

发生描述冲突时按以下顺序判断：

1. JSON gate/report 和冻结 plan；
2. `docs/PHASE3_RESULTS.md`；
3. `第三阶段修改计划.md`；
4. README 和其他概览文档；
5. 历史聊天结论。

当前主要入口：

- `工具调用建图_生命周期压缩调研报告.md`
- `第二阶段修改计划.md`
- `第二阶段实验结论.md`
- `第三阶段修改计划.md`
- `docs/PHASE3_RESULTS.md`

## 2. 当前算法是什么

第三阶段的主方法是 `full_ours` / `GraphLifecycleManager`。它不再把所有未解决失败原始消息作为无预算 mandatory context，而是：

1. 原始 call/result 始终写入 archive；
2. 用 operation scope 聚合可行动失败；
3. 生成受置信度、TTL 和显式 expiry 控制的 compact `FailureCard`；
4. Failure Card 只使用总预算的 12.5%；
5. 原始失败工具消息默认不回注模型 prompt；
6. 修复、替代完成、语法纠正、supersession 或 TTL 可使 Card 失效；
7. Raw Hard 旧行为只作为对照保留。

核心流程：

```text
ON_TURN(history):
    graph = rebuild_graph_from_prefix(history)
    infer_retry_resolve_supersede(graph)
    apply_lifecycle(graph)

    archive_all_raw_tool_records(graph)
    hard = active_goals + active_constraints + unrecoverable_evidence

    cards = build_failure_cards(
        unresolved_actionable_failures,
        scope=operation_scope,
        confidence>=0.75,
        ttl_steps=8,
    )
    cards = fit_into_independent_budget(cards, fraction=0.125)

    optional = recent_and_recoverable_non_failure_context(graph)
    return project_to_messages(hard + cards + optional)
```

实现入口：

- `src/tracegraph/context.py`
- `src/tracegraph/failure_cards.py`
- `src/tracegraph/lifecycle.py`
- `src/tracegraph/integrations/tau3_agent.py`
- `src/tracegraph/message_protocol.py`

### 已解决的原始问题

“失败工具调用被强制保存并反复进入后续上下文”已经修复：

- raw tool call/result 仍可审计和恢复；
- active context 默认只接收 compact Card；
- Card 不触发历史 tool-call/result protocol closure；
- P3 Card 条件中 `raw_failure_messages_selected = 0`；
- P3 Card 条件中 `budget_infeasible_sessions = 0`。

因此无界原始失败回注不再是当前主问题。

## 3. 各阶段做了什么

## 3.1 P0：收缩主算法语义

状态：**工程完成**。

已实现：

- `FailureCard` schema；
- operation scope 聚合；
- actionable、terminal、policy-denied、malformed、stale 等失败分类；
- resolved、superseded、alternative-completed、corrected-syntax、TTL 等 expiry；
- 12.5% 独立 Card 子预算；
- archive/audit 与 active context 分离；
- `raw_hard_failure_retention` 旧行为对照；
- 缺失/空参数被成功补齐时的 `argument_completion` retry 与即时 resolution；
- Failure Card 不恢复历史原始工具协议交换。

P0 的意义是消除第二阶段“必须可恢复 = 必须每轮可见”的错误等价，不是证明在线收益。

## 3.2 P1：确定性机制干预

状态：**通过**。

产物：`outputs/phase3/p1_interventions_v2/manifest.json`。

设计：

- 4 类干预：参数修正、只保留最新失败、替代工具完成、malformed 后合法调用；
- 每类 8 个固定任务，共 32 个任务；
- 4 个条件：Full、Remove、Raw Hard、Compact Card；
- 共 128 runs。

结果：

- 128/128 TraceGraph 有效；
- controlled card precision = `1.0`；
- controlled expiry correctness = `1.0`；
- Card 相对 Remove：repeated invalid action `-1.0`、recovery steps `-1.0`、success delta `0`；
- Card 相对 Raw Hard：selected representation tokens `-206.75`、protocol-closed tokens `-350.5`、本地 controller 输入 `-277.75`、success delta `0`；
- 四类干预方向一致。

边界：P1 是确定性机制识别，不是自然在线效果证据。

## 3.3 P2：failure-chain 构念验证

状态：**Codex 临时版完成，正式人工 gate 未通过**。

产物：`outputs/phase3/p2_failure_chain_v1/`。

数据：

- 32 条 P1 受控 failure chains；
- 28 条自然 failure-rich chains；
- A/B 各 60 行，顺序独立；
- 7 条 chain、11 个字段出现 A/B 分歧；
- Codex 临时裁决后 unresolved adjudication = `0`。

provenance 必须保留：

```text
annotation_provenance = codex_provisional
A identity = codex_gpt5_pass_a
B identity = codex_gpt5_pass_b
independence_warning = same_model_same_thread_not_independent_human_gold
```

不要把这两次 pass 描述为“两位独立标注者”。正式 gate 会拒绝 `codex_provisional`。

临时评分：

| 指标 | 结果 |
| --- | ---: |
| actionable precision | 0.942 |
| actionable recall | 1.000 |
| expiry precision | 0.744 |
| operation-scope aggregation error | 0.400 |
| card coverage accuracy | 1.000 |
| 最小字段 Cohen's κ | 0.000 |

字段 κ：

| 字段 | κ |
| --- | ---: |
| same operation scope | 0.861 |
| relation | 0.936 |
| failure class | 1.000 |
| expiry trigger | 0.936 |
| card covers next step | 0.000 |

coverage κ 为 0 是近单类别造成的 prevalence degeneration，不等同于 coverage accuracy 为 0。

进一步诊断：

- 22/55 个 scope mismatch 全部是算法预测 `not_applicable`；Codex gold 为 `yes` 20 个、`no` 2 个；
- 没有出现“算法预测 same scope、Codex 判 different scope”的危险过度聚合；
- 当前主要是保守欠聚合，可能造成重复 Card 或压缩不足；
- 11/43 个 expiry mismatch 中，10 个是 `resolved` vs `superseded`，1 个是 `resolved` vs `corrected_syntax`；
- 这些 cause 虽不同，但都可能意味着旧 Card 应离开 active context。

结论：当前标签把“是否继续 active”和“为何失效”混在同一字段，不能直接据此大改算法，也不应立即把旧表交给人工。

关键文件：

- `codex_provisional_p2_report.json`
- `annotator_a.csv`
- `annotator_b.csv`
- `adjudication.csv`
- `CODEX_PROVISIONAL_NOTICE.md`
- `annotation_key.json`

## 3.4 P3：单环境四条件实验

状态：**临时数据完整，正式效果 gate 未通过**。

### 原始 60-session 矩阵的问题

`p3_card_retail_codex_v1` 计划为 5 tasks × 3 trials × 4 conditions = 60 sessions。

原始运行中有 17 个 session 在 agent/user 对话完成后的 τ³ natural-language assertion evaluator 阶段触发 OpenAI `insufficient_quota`。错误集中在后执行的 task 76/36 和 Remove/Raw/Card 条件，而先执行的 Full 没有错误，因此存在条件顺序偏差。

上游在 evaluator exception 后写出的 `SimulationRun` 丢失了完整对话，不能只离线补算 reward。43 个有效 session 不得作为无偏正式矩阵使用。

### evaluator 修复

未修改 vendor 源码。修复位于：

- `scripts/tau3_cli.py`
- `scripts/run_glm_pilot.ps1`
- `scripts/plan_glm_matrix.py`
- `src/tracegraph/matrix.py`

重要陷阱：导入 `tau2.config` 会先执行 `tau2.__init__`，evaluator 使用 `from tau2.config import ...` 提前复制旧默认值。只修改 `tau2.config.DEFAULT_LLM_NL_ASSERTIONS` 看起来正确，实际 evaluator 仍可能使用 GPT-4.1。

因此显式 evaluator 覆盖必须同时修改：

```text
tau2.config.DEFAULT_LLM_NL_ASSERTIONS
tau2.evaluator.evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS
```

参数对象也必须同时覆盖。

ZAI 会返回 fenced JSON。当前 wrapper 使用：

```text
TRACEGRAPH_TAU_NL_EVALUATOR_JSON_MODE = strict_then_extract
```

先尝试严格 `json.loads`，只有失败时才提取 fenced JSON；不能删除该兼容层。

两个诊断批次已移入：

- `outputs/phase3/aborted_attempts/p3_card_retail_codex_evalfix_v1_bad_config_20260718_2320/`
- `outputs/phase3/aborted_attempts/p3_card_retail_codex_evalfix_v1_fenced_json_20260718_2324/`

它们不进入分析，但必须保留。

最终平衡补跑：

- 配置：`configs/phase3_p3_compact_card_evalfix_codex_v1.json`；
- task：76、36；
- 四条件全部重跑；
- 8/8 runs；
- 24/24 sessions；
- 0 infrastructure errors；
- 0 graph validation errors；
- provider usage coverage = 100%。

### 修复后复合数据集

计划：`outputs/phase3/plans/p3_card_retail_codex_repaired_composite_v1.json`。

构成：

- task 27/33/34：原始矩阵中未受 evaluator 配额影响的结果；
- task 76/36：平衡补跑中四个条件的结果；
- 每个 task 内 evaluator 对所有条件一致；
- task strata 之间 evaluator provenance 可以不同。

因此当前 P3 是 **task-stratified evaluator 复合数据**：只能汇总 task 内 paired delta，不能描述为“一次连续、不间断的 60-session 正式运行”。

完整性：

- 20/20 runs；
- 60/60 sessions；
- 60/60 traces；
- 0 infra errors；
- 0 graph errors；
- 0 zero-token traces；
- 0 malformed sessions；
- provider usage coverage = 100%。

条件汇总：

| 条件 | success | normal stop | 平均 provider input | 平均 protocol tokens | repeated invalid |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full | 2/15 | 93.3% | 93,494 | 94,864 | 0.067 |
| Remove | 4/15 | 86.7% | 94,000 | 75,744 | 0.133 |
| Raw Hard | 2/15 | 86.7% | 95,649 | 90,821 | 0.000 |
| Compact Card | 4/15 | 93.3% | 86,123 | 74,593 | 0.000 |

Card paired delta：

| 参考 | success Δ [95% CI] | provider Δ [95% CI] | protocol Δ [95% CI] | repeated Δ [95% CI] |
| --- | ---: | ---: | ---: | ---: |
| Full | +0.133 [-0.133, 0.400] | -7,371 [-37,254, 18,157] | -20,272 [-77,770, 29,068] | -0.067 [-0.200, 0.000] |
| Remove | 0.000 [-0.200, 0.200] | -7,877 [-46,901, 24,514] | -1,152 [-41,512, 40,012] | -0.133 [-0.400, 0.000] |
| Raw Hard | +0.133 [-0.133, 0.400] | -9,526 [-37,611, 17,323] | -16,228 [-62,759, 30,115] | 0.000 [0.000, 0.000] |

报告目录：

- `outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/full_trajectory_reference/`
- `outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/remove_reference/`
- `outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/raw_reference/`

## 3.5 P4：强 compressor 与扩展

状态：**工程完成，扩展实验 No-Go**。

已实现 `acon_official_with_failure_cards`：保持官方 ACON selected-message plan 不变，再叠加受独立预算约束的 native Failure Card。源码 hash、runtime eligibility、provider usage、fallback 均 fail closed。

最新 gate：

`outputs/phase3/gates/p3_card_retail_codex_repaired_composite_v1_gate_report.json`

已通过：

- P1 engineering gate；
- P3 matrix completeness；
- provider usage completeness；
- bounded Card 且 raw replay 为 0；
- failure-type consistency；
- Card 不增加 Raw repeated invalid。

未通过：

1. `p2_human_construct_gate`；
2. `card_reduces_raw_protocol_and_provider_input`；
3. `card_improves_repeats_or_recovery_vs_remove`；
4. `task_success_noninferior`。

`p4.go_gate_passed = false`。

这是计划中 No-Go 分支的正确执行，不是遗漏实现。P4 runner 会在任何外部 API 会话开始前拒绝执行。不要伪造正向 gate，不要绕过 runner，不要继续 ACON live、第二模型家族或第二环境。

## 4. 当前真正遇到的问题

### 4.1 H4 在当前 τ³ 设置中仍不可识别

Card 条件只有 7/15 个 session 实际激活 Failure Card，共约 27 个 card-visible turns；只有 1 个 session 出现 resolved failure。

更关键的是，Card 相对 Remove 的 repeated-invalid 改善出现在没有激活 Card 的分层中：

- Card-active 事后分层：7 对，success delta `-0.143`，provider delta约 `-29k`，repeat delta `0`；
- no-Card 事后分层：8 对，success delta `+0.125`，provider delta约 `+10.6k`，repeat delta `-0.25`。

该分层是 post-hoc diagnostic，不可作因果结论，但它明确说明当前 repeated-invalid 点估计不能归因于 Card。

task 33 的 Card 分支没有任何 Card，却比 Remove 少 2 次 repeated-invalid。这更像轨迹随机分叉，而不是 Failure Card 机制作用。

### 4.2 总 token 被轨迹长度混杂

Card vs Remove 的 provider delta 在任务间方向相反：

- task 36：约 `-55.9k`；
- task 76：约 `+53.7k`。

总会话 token 同时受到以下因素影响：

- 工具调用次数；
- agent 是否提前停止；
- user simulator 后续响应；
- 模型 API 的非完全确定性；
- 是否进入长错误循环；
- context manager 本身。

temperature=0 不等于 API 轨迹完全可重复。当前 `--seed` 主要进入 τ³ harness，agent/user LLM args 没有冻结模型采样 seed。

下一轮不能只比较全会话 token，必须报告失败发生后的固定动作窗口和 action-normalized token。

### 4.3 P2 标签体系需要先改，不能先找人工

现有 `expiry_trigger` 同时承担状态和原因；`same_operation_scope` 又把 alternative completion、syntax correction 和 tool signature 混在一起。

如果直接让人工填写旧表，最可能得到更昂贵但仍无法解释的 gold。

正式人工标注必须等 v2 schema 稳定后再做，并从不含 Codex 标签泄漏的干净副本开始。

### 4.4 trajectory generation 与 evaluator 耦合

当前上游 evaluator exception 会让结果包装丢失已完成的对话。TraceGraph trace 虽保留部分结构，但不足以重建完整官方 `SimulationRun` reward 输入。

下一轮必须先持久化完整 conversation、环境状态摘要和 usage，再离线评分。evaluator 失败只能产生 `evaluation_error`，不能把已生成 trajectory 变成空结果。

### 4.5 外部有效性未建立

当前 agent、user simulator 和修复任务的 evaluator 均使用 `zai/glm-4.7-flash`。这可用于低成本调试，但不能证明跨模型、跨 evaluator 或跨环境的稳健性。

但是现在不应立即增加第二模型或第二环境；主机制在单环境内尚不可识别，扩展只会增加成本和解释混乱。

## 5. 历史下一步：P3b 可识别性修复（已完成 A；B 不再是当前主线）

以下内容保留用于解释旧 Phase 4/P3b-A 的来源和复用旧 Card fork 基础设施，不再作为当前行动顺序。当前顺序以《第四阶段修改计划》v2.0 为准。

## 5.1 P3b-A：零 API 成本

### A1. 设计 failure-chain label schema v2

保留原 v1 文件不变，新增版本，不原地重写旧报告。

至少拆分为：

```text
should_card_remain_active:
  yes | no | unclear

expiry_cause:
  resolved | superseded | corrected_syntax | alternative_completed |
  user_abandoned | constraint_changed | final_accepted | ttl_expired |
  still_active | other | unclear

scope_relation:
  same_operation | different_operation | alternative_completion |
  syntax_correction | not_applicable | unclear

card_covers_next_step:
  yes | no | not_applicable | unclear
```

评分拆分：

- retention safety：`should_card_remain_active` precision/recall；
- expiry cause accuracy：只在双方都判 inactive 时计算；
- unsafe overmerge：算法判 same、gold 判 different；
- conservative undermerge：算法判 different/N/A、gold 判 same；
- coverage：报告 confusion counts 和 accuracy；近单类别时不再用最小 κ 一票否决；
- κ/AC1 等一致性指标必须同时报告类别分布，禁止只报一个退化值。

先用现有 60 chains 做离线 v2 映射和错误审计；不要调用外部模型。

### A2. 解耦生成与评分

新增运行阶段：

```text
generate trajectory
    -> persist conversation + environment summary + usage + hashes
    -> mark generation_complete
    -> offline evaluator
    -> merge reward by simulation_id
```

要求：

- evaluator 开始前 trajectory 已落盘；
- evaluator model/args/JSON mode 写入 manifest；
- raw evaluator response 永久保留；
- evaluator failure 不覆盖 conversation；
- retry evaluator 不重新运行 agent/user；
- generation 和 evaluation 分别统计 infra error。

### A3. 增加失败后窗口指标

对 actionable failure 之后的最多 3 个 agent actions 计算：

- 是否重复同一 invalid operation；
- 是否产生 admissible correction；
- 是否在窗口内 resolve；
- recovery steps；
- post-failure provider input/output tokens；
- provider tokens per agent action；
- Card 实际可见次数和 token；
- raw failure message 是否误回注。

## 5.2 P3b-B：common-prefix fork pilot

P3b-A 的单元测试和离线回放通过后，才申请/使用外部 API。

### 固定设计

```text
10 actionable-failure prefixes
× 2 conditions: Compact Card, Remove
× 2 replicates
= 40 short branches
```

只观察失败后的 3 个 agent actions。Full 和 Raw Hard 不进入 primary pilot；Raw 可在不影响成本时作为诊断，不能替代 Remove 主对照。

### prefix 必须保存

每个 `prefix_id` 至少记录：

- domain/task/seed；
- conversation prefix；
- environment/DB state 或可验证快照；
- conversation SHA-256；
- environment state SHA-256；
- failure tool call/result IDs；
- failure class；
- operation scope；
- treatment Card payload；
- branch horizon；
- agent/user/evaluator model config。

Card 和 Remove 分支开始前必须验证 conversation hash 与 environment hash 完全一致。

### treatment invariant

- Card 分支：下一决策输入中必须出现目标 Failure Card；
- Remove 分支：同一 Card 必须缺失；
- 其他 hard/optional 选择规则保持一致；
- 两个分支都不得出现原始失败 tool-call/result 回注；
- 如果 Card 没有真正进入 treatment prompt，该 prefix 不算有效 fork，必须替换而不是按 0 效果计入。

### primary metrics

- next-3-action repeated-invalid rate；
- next-3-action repair rate；
- recovery steps；
- post-failure provider tokens。

secondary：

- 完整任务 success；
- policy violation；
- normal stop；
- action count。

### pilot 结束后的决策

pilot 只估计事件率和 discordant-pair rate，不作论文显著性结论。

- 如果无法获得 10 个有效、hash 匹配且 Card 实际可见的 prefixes：停止扩大，当前 benchmark/model 对 H4 不可识别；
- 如果有效 prefixes 足够，但 repeated/repair 完全没有 discordance：停止扩大，转向 negative-results；
- 如果出现方向稳定的 discordant pairs，且 success/policy 没有明显安全回退：根据 discordant rate 计算正式样本量，暂停并向用户报告，不自动启动正式矩阵；
- 人工 gold 只在 v2 schema 稳定且正式研究仍值得继续时安排。

## 5.3 后续 Go/Stop

只有新的 P3b 正式结果同时支持以下条件，才重新讨论 P4：

1. Card vs Remove 在相同 failure prefix 下改善 repeated-invalid 或 repair；
2. post-failure token 不增加，或收益/成本边界明确；
3. task success/policy 不劣；
4. v2 retention safety 通过人工 gold；
5. 结果不由单一 failure type 驱动。

否则应收口为 measurement/negative-results：

- raw unresolved failure 为什么导致 token 膨胀；
- archive 与 active context 为什么必须分离；
- 哪些负证据具有可行动性；
- compact Card 为什么在当前自然 τ³ 轨迹中事件率不足。

## 6. 历史禁止事项与当前有效边界

以下 provenance、数据完整性和授权边界继续有效；第 8 项只禁止绕过旧 gate 续跑旧矩阵，不再禁止按 GDSC v2.0 在新 eligibility、预注册和授权后引入必要的 benchmark、模型或 baseline。

1. 把 `codex_provisional` 改成 `human_independent`；
2. 把同模型同会话 A/B 写成两位人工标注；
3. 删除或改写 `annotation_key.json`、Codex CSV、裁决和 notice；
4. 删除 `aborted_attempts`；
5. 使用原始 43 个有效 session 宣称无偏 P3；
6. 把 repaired composite 写成一次连续正式运行；
7. 绕过 `p4.go_gate_passed=false`；
8. 在旧 `p4.go_gate_passed=false` 下继续 ACON+Card live、第二模型、第二环境或更大旧 Card 矩阵；
9. 用 total session token 单独声称 Card 因果节省；
10. 在保存 trajectory 之前调用易失败 evaluator；
11. 在旧标签体系上直接安排昂贵人工标注；
12. 未经用户授权暂存、提交、推送或清理工作树。

## 7. 关键文件索引

### 研究和计划

- `工具调用建图_生命周期压缩调研报告.md`
- `第二阶段修改计划.md`
- `第二阶段实验结论.md`
- `第三阶段修改计划.md`
- `docs/PHASE3_RESULTS.md`

### P1/P2

- `outputs/phase3/p1_interventions_v2/manifest.json`
- `outputs/phase3/p2_failure_chain_v1/codex_provisional_p2_report.json`
- `outputs/phase3/p2_failure_chain_v1/CODEX_PROVISIONAL_NOTICE.md`
- `src/tracegraph/interventions.py`
- `src/tracegraph/failure_chain_annotation.py`

### P3

- `configs/phase3_p3_compact_card_provisional_codex_v1.json`
- `configs/phase3_p3_compact_card_evalfix_codex_v1.json`
- `outputs/phase3/plans/p3_card_retail_codex_v1_executed.json`
- `outputs/phase3/plans/p3_card_retail_codex_evalfix_v1_executed.json`
- `outputs/phase3/plans/p3_card_retail_codex_repaired_composite_v1.json`
- `scripts/build_phase3_repaired_composite.py`
- `scripts/analyze_live_matrix.py`

### P4/gate

- `configs/phase3_p4_acon_card_smoke_v1.json`
- `src/tracegraph/phase3_gates.py`
- `scripts/evaluate_phase3_gates.py`
- `outputs/phase3/gates/p3_card_retail_codex_repaired_composite_v1_gate_report.json`

### evaluator 与日志

- `scripts/tau3_cli.py`
- `scripts/run_glm_pilot.ps1`
- `outputs/phase3/logs/p3_card_retail_codex_evalfix_v1.stdout.log`
- `outputs/phase3/logs/p3_card_retail_codex_evalfix_v1.stderr.log`

## 8. 离线复现命令

以下命令不应触发新的 agent/user API 会话。

### 8.1 测试

```powershell
Set-Location 'E:\科研\Tools Tracing'
$env:PYTHONPATH = 'src'
python -m pytest -q
```

预期：`103 passed`。

### 8.2 重建修复后复合 plan

```powershell
python scripts/build_phase3_repaired_composite.py `
  --original-plan outputs/phase3/plans/p3_card_retail_codex_v1_executed.json `
  --repair-plan outputs/phase3/plans/p3_card_retail_codex_evalfix_v1_executed.json `
  --repair-task-id 76 `
  --repair-task-id 36 `
  --matrix-id p3_card_retail_codex_repaired_composite_v1 `
  --output outputs/phase3/plans/p3_card_retail_codex_repaired_composite_v1.json
```

预期：20 runs、60 sessions。

### 8.3 三参考分析

```powershell
$plan = 'outputs/phase3/plans/p3_card_retail_codex_repaired_composite_v1.json'
$results = 'vendor/tau3-bench/data/simulations'

python scripts/analyze_live_matrix.py `
  --plan $plan --results-root $results `
  --reference-manager full_trajectory `
  --output outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/full_trajectory_reference

python scripts/analyze_live_matrix.py `
  --plan $plan --results-root $results `
  --reference-manager ours_without_failure_retention `
  --output outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/remove_reference

python scripts/analyze_live_matrix.py `
  --plan $plan --results-root $results `
  --reference-manager raw_hard_failure_retention `
  --output outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/raw_reference
```

预期每份报告：60/60 sessions、0 infra、0 graph error、provider usage 100%。

### 8.4 重算 gate

```powershell
python scripts/evaluate_phase3_gates.py `
  --p1-manifest outputs/phase3/p1_interventions_v2/manifest.json `
  --p2-report outputs/phase3/p2_failure_chain_v1/codex_provisional_p2_report.json `
  --p3-report full_trajectory=outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/full_trajectory_reference/live_matrix_report.json `
  --p3-report ours_without_failure_retention=outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/remove_reference/live_matrix_report.json `
  --p3-report raw_hard_failure_retention=outputs/phase3/p3_card_retail_codex_repaired_composite_v1_analysis/raw_reference/live_matrix_report.json `
  --output outputs/phase3/gates/p3_card_retail_codex_repaired_composite_v1_gate_report.json
```

预期：

```text
p3.complete = true
p3.formal_p3_gate_passed = false
p4.go_gate_passed = false
```

## 9. 接手检查清单

开始任何实现前：

- [ ] 阅读本文件和 `docs/PHASE3_RESULTS.md`；
- [ ] 运行 `git ... status --short`，确认不覆盖现有修改；
- [ ] 确认 P1/P2/P3/gate JSON 存在；
- [ ] 运行测试并至少复现 GDSC 改造后的 `144 passed`；
- [ ] 确认没有外部实验进程仍在运行；
- [ ] 读取 `docs/PHASE4_GDSC_RESULTS.md`：当前 R2/E0 均为 No-Go，不得启动 representation fork 或 benchmark matrix；
- [ ] 保留所有 provenance、失败批次和 evaluator raw response；
- [ ] 每次准备启动 API 前先向用户报告 session 数、模型、预计成本和停止条件。

## 10. 一句话交接结论（2026-07-21 GDSC 执行后）

GDSC 工程与最终请求成本核算已经实现，R0 证实 192-view 成本错位和足够 oracle headroom；但 R2 的 serialized reduction 只有 `14.956%`，E0 数据也不具备进入 common-prefix pilot 的资格。下一位 agent 应把这次结果作为诚实的 No-Go 诊断，优先分析完整 policy/tool-schema 固定成本和 benchmark eligibility 缺口；不得绕过门禁启动 R3/R4，也不得通过调阈值或补样本改写本轮结论。

## 11. 2026-08-01 Phase 5.2 GLM-5.2 e1 试跑交接

用户已明确授权将 Phase 5.2 伪标注输入发送到 Z.AI `open.bigmodel.cn` 的 `GLM-5.2`，先跑 10 个请求，无报错再全量。为保持原 `glm-4.7-flash` e0 条件不可污染，已新增独立条件：

- config：`configs/phase52_lifecycle_modeling_glm52.json`
- output：`outputs/phase5_2/e1_glm52_pseudolabel_v1`
- model：`zai/glm-5.2`
- pricing snapshot SHA-256：`216525a2e3c749b1f9a7b81d9eb68c35f3da7290dcd6ea5ed8184dc5a4dbbef8`
- frozen requests：370
- estimated input tokens：3,528,234

为支持该条件，`load_phase52_config` 现在允许两种明确协议：原 `glm-4.7-flash` 免费条件，和带有 `paid_use_authorized_by_user=true`、`condition_id=e1_glm52_pseudolabel_v1` 的 `glm-5.2` 条件。runner 的价格预检也已分支：free 条件仍必须三项免费；paid 条件必须有显式付费授权和非负 USD/M token 价格。`verify_phase52_artifacts.py`、`preflight_phase52_state_machine.py`、`report_phase52_collection_pause.py` 新增 `--config`，以便 e0/e1 独立验账。

已完成本地检查：

- `python -m pytest tests\test_phase52_lifecycle_modeling.py`：13 passed
- Ruff target files：passed
- e1 request build：370 requests, provider_requests=0
- e1 state-machine preflight：261 prefixes / 1,092 predictions，所有 integrity rates 100%
- e1 verifier：valid, regenerated_requests=370

真实 GLM-5.2 smoke 第一条请求 `pass_a_08654b21b5f4685c8b12` 返回 HTTP 429，因此按计划停止，没有继续跑剩余 9 条或全量。当前 e1 状态：

- pause report：`outputs/phase5_2/e1_glm52_pseudolabel_v1/pause_reports/pause_0001.json`
- report SHA-256：`8bdf0093fa462a8e99f3b7c7b5c9c1b9d06789a85846d44487375c82dbc4f8be`
- attempts：1
- valid labels：0
- usage：0 input / 0 output token
- failure is quality No-Go：false

恢复 e1 时先重新核价，继续同一 frozen request set；不得把 e1 与 e0 标签混合作为同一条件，不得训练 Scheme B，不得启动外部行为实验。

## 12. 2026-08-10 Phase 5.2 Qwen3.7-Plus e2 结果

用户授权开始阿里云百炼 `qwen3.7-plus` 的 Phase 5.2 伪标注实验。已建立独立条件，未覆盖 e0/e1：

- config：`configs/phase52_lifecycle_modeling_qwen37plus.json`
- output：`outputs/phase5_2/e2_qwen37plus_pseudolabel_v1`
- model：`aliyun-bailian/qwen3.7-plus`
- pricing snapshot SHA-256：`81c525e8bc599b10e549eb64859ca2c05d816eef897bdba57fde8a44496ccc8e`
- frozen requests：370
- estimated input tokens：3,527,768

前 10 个真实请求全部 HTTP 200 且标签合法，因此继续全量。续跑在 `pass_a_842b87d6eaf6a211bf97` 停止：该 20-span calibration 请求连续两次把 `superseded` 放入 `disposition`，而它只属于 `terminal_reason`。单请求唯一重试已耗尽，不得原地恢复或手工改写。

停止状态：

- provider attempts：75，全部 HTTP 200
- valid labels：72/370；完整双遍 prefix：36/185
- invalid response attempts：3
- usage：816,109 input / 37,047 output / 853,156 total token
- 未折扣估算费用：0.266035 USD
- pause report：`outputs/phase5_2/e2_qwen37plus_pseudolabel_v1/pause_reports/pause_0001.json`
- report SHA-256：`308307a3e61706185edaa18c6dda2093c374bd0afb05d8b01e6a2990c0d9d04e`
- failure is quality No-Go：true

本条件未形成完整伪标注人口，因此不得计算/报告总体 opportunity prevalence、双遍 κ 或 held-out 状态机质量；`pseudolabel_gate.json`、`pseudolabel_summary.json` 和最终 manifest 均不应存在。详细报告见 `docs/PHASE52_QWEN37PLUS_PILOT_RESULTS.md`。如继续，必须新建独立条件并预注册协议修改；不得覆盖 e2 或拼接 e0/e1/e2 标签。

## 13. 2026-08-10 relation-first e3 调试交接

用户批准按 relation-first 建议继续调试。所有版本均独立保存：

- v1：`outputs/phase5_2/e3_qwen37plus_relation_first_v1`，请求准备预计 3,605,838 token，超过 3,600,000 上限后在 provider_requests=0 时中止；目录保留。
- v2：`outputs/phase5_2/e3_qwen37plus_relation_first_v2`，四值 `current_target_need` 在定向失败 prefix 上连续两次被错误填为 `superseded`；0 valid / 2 attempts，停止报告 SHA `b6deff7253e3e02ca1597676018f7ec6ad178ad454c973dd5908c22cbf2fb551`。
- v3：`outputs/phase5_2/e3_qwen37plus_relation_first_v3`，将当前目标需要性改为两个布尔字段，模型不再输出 disposition；程序 fail-closed 推导 disposition。

v3 定向复现和补充调试共 10/10 请求一次合法，覆盖 5 个完整双遍 prefix、55 个 span。格式问题已修复，但 safe 二元一致率为 0.80、κ=0.4954、全字段一致 12/55、consensus safe 8、consensus uncertain 43。逐字段一致率最低的是 relation_target_ids（0.418），其次 obligations（0.800）和 required_for_current_target（0.855）。原 20-span 困难 prefix 的 safe 一致率仅 0.60、全字段一致 1/20。

debug report：`outputs/phase5_2/e3_qwen37plus_relation_first_v3/debug_reports/relation_first_debug_0001.json`，SHA `2a0b9317a81bede9dfcb84a39475fc8850771d4a2f3a4684b57041a00a13fb4e`。详细说明见 `docs/PHASE52_RELATION_FIRST_DEBUG_RESULTS.md`。

当前判断是“结构接口已改善，但语义稳定性不足”，不是生命周期方向 No-Go。不得把 5-prefix 定向样本当正式门禁或继续全量。下一步若获批准，应新建 chunked-labeling 调试条件，保留完整 prefix 上下文而缩小每次输出的 span 数；必须重新冻结请求人口、成本和门禁，不得覆盖或拼接 e2/e3 标签。
