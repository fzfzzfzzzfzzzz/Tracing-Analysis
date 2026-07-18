# Tools Tracing 项目 Agent 交接文档

> 快照日期：2026-07-19
> 仓库：`E:\科研\Tools Tracing`
> 当前测试基线：`103 passed`
> 当前研究判定：P0/P1 工程与机制实验完成；P2 为 Codex 临时版、不是人工 gold；P3 修复后复合分析完成；P4 工程完成但扩展实验按预注册 gate 判定为 **No-Go**。

## 0. 接手后的第一条原则

不要继续运行 P4，不要原样扩大现有 P3 矩阵，也不要把 Codex A/B 改名为人工标注。

当前最有价值的下一步不是增加模型、环境或 baseline，而是修复 P3 的**机制可识别性**：把“Card 是否应该继续保留”和“Card 因何失效”分开，随后用相同失败前缀的 fork 实验直接比较 Card 与 Remove。

## 1. 接手须知

### 1.1 工作树不是干净状态

当前仓库包含大量尚未提交的第三阶段修改和新增文件。这些修改是本阶段工作的主体，不是可随意清理的临时文件。

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

## 5. 下一步：P3b 可识别性修复

按以下顺序执行。不要并行启动 P4。

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

## 6. 明确禁止事项

接手 agent 不得：

1. 把 `codex_provisional` 改成 `human_independent`；
2. 把同模型同会话 A/B 写成两位人工标注；
3. 删除或改写 `annotation_key.json`、Codex CSV、裁决和 notice；
4. 删除 `aborted_attempts`；
5. 使用原始 43 个有效 session 宣称无偏 P3；
6. 把 repaired composite 写成一次连续正式运行；
7. 绕过 `p4.go_gate_passed=false`；
8. 继续 ACON live、第二模型、第二环境或更大旧矩阵；
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
- [ ] 运行测试并得到 `103 passed`；
- [ ] 确认没有外部实验进程仍在运行；
- [ ] 将任务限定为 P3b-A，除非用户明确授权进入 P3b-B；
- [ ] 保留所有 provenance、失败批次和 evaluator raw response；
- [ ] 每次准备启动 API 前先向用户报告 session 数、模型、预计成本和停止条件。

## 10. 一句话交接结论

工程已经成功消除了“原始失败消息无界反复回注”，但自然 τ³ 轨迹中的真实 failure/retry/resolve 事件太少，现有 P3 的 token 和 repeated-action 点估计被轨迹随机性混杂；下一位 agent 应先完成零成本的标签/评分拆分和 trajectory/evaluator 解耦，再用 common-prefix fork 验证 Failure Card 相对 Remove 的真实边际作用。
