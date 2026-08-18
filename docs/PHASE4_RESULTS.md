# 第四阶段执行结果：可识别性、评测解耦与局部窗口

> **2026-07-21 范围说明：** 本文件记录的是旧 Phase 4/P3b-A 的历史执行结果，数值、provenance、工程 gate 和外部 API No-Go 仍然有效；其 Card-only 下一步与 negative-results 收口建议已被新版 [`第四阶段修改计划.md`](../第四阶段修改计划.md) v2.0 取代。当前同名计划链接指向研究重置版，不应误认为本结果执行了 GDSC 计划。

> 快照日期：2026-07-20
>
> 历史执行依据：旧版《第四阶段修改计划：Failure Card 可识别性与投稿级证据链》（保存在 Git 历史）；当前替代计划：[`第四阶段修改计划.md`](../第四阶段修改计划.md) v2.0
>
> 结论：Phase 4 **工程 gate 通过**；正向经验主张 gate 与 P3b-B 外部实验 gate 均为 **No-Go**
>
> 外部 API：本阶段实现、迁移、回放与故障注入共调用 **0 次**

## 1. 结论先行

第四阶段完成了 handoff 要求的 P3b-A：

1. Failure-chain v2 把“Card 是否仍应 active”与“为何失效”拆开，并同时报告 κ、Gwet's AC1、类别分布和方向性聚合错误；
2. τ³ generation 与 evaluator 已解耦，完整 trajectory 在 evaluator 前不可变落盘，evaluator 异常不再抹掉对话，离线重试不重新运行 agent/user；
3. 新指标固定在 failure 后最多 3 个 agent tool actions，并使用真实 provider usage 计算 post-failure 与 per-action token；
4. 冻结的 Phase 3 修复后矩阵已完成 60-session 离线回放；
5. 新增 fail-closed Phase 4 gate，严格区分“投稿级研究基础设施”和“已经支持正向论文主张”。

当前可以声称的是：**measurement、provenance、failure recovery 和复现基础设施已达到投稿级审计要求。** 当前不能声称的是：**Failure Card 已被证明能因果改善自然 tool-agent 轨迹。**

## 2. A1：Failure-chain schema v2

### 2.1 非破坏迁移

冻结的 `outputs/phase3/p2_failure_chain_v1/` 未被修改。迁移审计记录了四个 v1 输入文件的 SHA-256，并生成：

- 60/60 v2 prediction keys；
- 60 行 `migrated_codex_a.csv`；
- 60 行 `migrated_codex_b.csv`；
- 7 行迁移裁决；
- 两份各 60 行、不含 Codex 标签与身份的人工盲标空表。

只有 2 个旧标签发生有损原因映射：v1 的 `terminal/stale` expiry 被归入 v2 `expiry_cause=other`，原 `failure_class` 仍保留。所有有损项都写入 `migration_audit.json`，没有静默转换。

### 2.2 v2 Codex provisional 诊断

下表只是对旧 Codex A/B 的确定性迁移与重评分，不是独立人工 gold：

| 指标 | v2 provisional 结果 |
| --- | ---: |
| chain 数 | 60 |
| `should_card_remain_active` gold 分布 | no 57 / yes 3 |
| retention precision | 0.176 |
| retention recall | 1.000 |
| 双方均判 inactive 时 expiry-cause accuracy | 0.744 |
| unsafe overmerge | 0/33 = 0.000 |
| conservative undermerge | 4/53 = 0.075 |
| coverage accuracy | 60/60 = 1.000 |
| 最小字段 Cohen's κ | 0.000 |
| 最小字段 Gwet's AC1 | 0.946 |

最关键的新诊断是：算法预测 17 条 Card 应继续 active，而 provisional gold 只有 3 条，形成 TP=3、FP=14、FN=0、TN=43。v1 的 expiry-cause precision 不能直接表达这种“过度保留但很少危险欠保留”的结构。

`card_covers_next_step` 仍是近单类别：A 全部判 yes，B 判 57 yes / 3 no；因此 κ=0，而 AC1=0.949、原始一致率=0.95。v2 报告保留所有三个量和分布，不再用单一退化 κ 否决构念。

这些数值仍带 `annotation_provenance=codex_provisional`，正式 gate 明确拒绝把它们当作人工 gold。

## 3. A2：Trajectory / evaluator 解耦

新协议由 `trajectory_artifacts.py` 和 `tau3_offline.py` 实现：

```text
orchestrator.run
  -> generation.json 原子落盘
  -> conversation/task/environment/usage SHA-256 校验
  -> generation_complete.json
  -> append-only evaluation_attempt_NNNN
  -> raw evaluator response / exception 永久保存
  -> simulation_id + generation_sha256 校验
  -> merged.json
```

默认 τ³ 行为不变。只有 `run_glm_pilot.ps1 -GenerationOnly -TrajectoryStore ...` 显式开启时才安装 generation-only runner。离线 evaluator CLI 默认只打印计划；必须同时提供 `--execute` 和覆盖所选 artifact 数的 `--max-evaluations` 才会执行。

评测完成后，`merge_tau3_offline_rewards.py` 通过 simulation id、conversation hash、generation hash 和 merged hash 四重校验，将 reward 写入一个**新的** τ³ results 文件；命令拒绝原地覆盖 generation-only 结果。

零 API 故障注入的 10 项检查全部通过：

- evaluator 前 generation 已落盘并带完成标记；
- generation 与各 component hash 可回读验证；
- evaluator 人工抛错被单独记录；
- 抛错前的 raw response 已持久化；
- 失败前后 generation 文件逐字节不变；
- 第二次离线评测保存新的 raw response 并成功 merge reward；
- 成功评测后 generation 仍逐字节不变；
- generation error 与 evaluation error 分开计数；
- 同 simulation id 的冲突 generation fail closed。

审计产物：`outputs/phase4/trajectory_protocol_audit.json`。

## 4. A3：Failure 后 next-3-action 指标

### 4.1 定义

分析单位是 eligible negative failure（actionable、policy-denied 或 malformed）后的最多 3 个 agent tool actions。每个事件保存动作级 JSON，并报告：

- exact-signature 且再次失败的 repeated same invalid；
- structural/argument-completion retry；
- 修正后非负结果的 admissible correction；
- `RESOLVED_BY` 是否落在窗口内及 recovery action index；
- 对应 assistant provider message 的 input/output usage；
- token/action；
- 目标 Failure Card 的 operation-scope 匹配可见次数与 token；
- `raw_failure_messages_selected` 的可观察覆盖与 replay 次数。

没有 action 或 usage 时写 `null`，不写 0。一个 provider message 含多个 tool calls 时只汇总一次 message usage，再用窗口 action 数归一化。

### 4.2 Phase 3 冻结矩阵离线回放

完整性：60/60 sessions、49 个 eligible failure events、45 个有后续 action 的事件、98/98 action-view 对齐、provider input/output coverage 45/45。23 个事件因会话结束而少于 3 actions，明确标为 censored。

| 条件 | events | 有 action | repeat events | repair events | resolved | 平均 post-failure input | 平均 input/action | 目标 Card 可见 action |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Trajectory | 13 | 13 | 1 | 1 | 1 | 13,931 | 7,021 | 0 |
| Remove | 19 | 16 | 2 | 1 | 1 | 15,392 | 7,053 | 0 |
| Raw Hard | 10 | 10 | 0 | 0 | 0 | 15,877 | 6,907 | 0 |
| Compact Card | 7 | 6 | 0 | 1 | 1 | 13,831 | 6,508 | 12 |

Card 与 Remove 的 context manager 均提供 raw-replay 可观察字段：Card 的 6 个有 action 事件、Remove 的 16 个有 action 事件全部为 0 replay。Full/Raw 旧 view 不提供该字段，因此报告为 missing coverage，而不是补 0。

这些行的 event 分母不同，failure prefix 也不同。表中 token 和事件率只能说明新指标可计算及 Card exposure 可被确认，**不能**把 Card 7 events 与 Remove 19 events 当作随机或配对因果比较。正式 estimand 仍须来自相同 conversation/environment hash 的 common-prefix fork。

## 5. Phase 4 gate

机器报告：`outputs/phase4/phase4_gate_report.json`。

工程 gate 的 11 项检查全部通过：

- v1 immutable；
- v2 60/60 迁移与人工空白表完整；
- v2 provisional provenance 未被冒充人工；
- trajectory 故障注入通过；
- post-failure 60-session 回放完整；
- action-view 与 provider usage 覆盖完整；
- Card exposure 实际出现；
- Card lane 可观察窗口中没有 raw replay。

因此：

```text
phase4.engineering_gate_passed = true
phase4.empirical_claim_gate_passed = false
p3b_b.go_gate_passed = false
p3b_b.external_api_execution_authorized = false
aaai_readiness.research_infrastructure_ready = true
aaai_readiness.positive_empirical_claim_ready = false
```

经验主张 blocker 仍为：独立人工 v2 gold、common-prefix fork 完整性、Card vs Remove 机制识别、success/policy 安全和 failure-type 稳健性。

## 6. 复现命令

以下命令均不启动 agent/user/evaluator 外部 API：

```powershell
$env:PYTHONPATH = "src"

python scripts/migrate_phase3_failure_chains_v2.py `
  --v1-package outputs/phase3/p2_failure_chain_v1 `
  --output outputs/phase4/failure_chain_v2

python scripts/score_phase4_failure_chains.py `
  --annotator-a outputs/phase4/failure_chain_v2/migrated_codex_a.csv `
  --annotator-b outputs/phase4/failure_chain_v2/migrated_codex_b.csv `
  --annotation-key outputs/phase4/failure_chain_v2/annotation_key.json `
  --adjudication outputs/phase4/failure_chain_v2/migrated_adjudication.csv `
  --output outputs/phase4/failure_chain_v2/codex_provisional_v2_report.json

python scripts/analyze_post_failure_windows.py `
  --plan outputs/phase3/plans/p3_card_retail_codex_repaired_composite_v1.json `
  --results-root vendor/tau3-bench/data/simulations `
  --project-root . `
  --output outputs/phase4/post_failure_phase3_diagnostic `
  --horizon 3

python scripts/audit_phase4_protocol.py `
  --output outputs/phase4/trajectory_protocol_audit.json

python scripts/evaluate_phase4_gates.py `
  --migration-audit outputs/phase4/failure_chain_v2/migration_audit.json `
  --v2-construct-report outputs/phase4/failure_chain_v2/codex_provisional_v2_report.json `
  --trajectory-protocol-audit outputs/phase4/trajectory_protocol_audit.json `
  --post-failure-report outputs/phase4/post_failure_phase3_diagnostic/report.json `
  --output outputs/phase4/phase4_gate_report.json

python -m pytest -q
& .venv/Scripts/ruff.exe check src scripts tests
git -c "safe.directory=E:/科研/Tools Tracing" diff --check
```

## 7. 历史下一步与停止条件（已被 GDSC v2.0 取代）

本阶段没有执行 P3b-B。以下 Card/Remove 设计是该历史阶段当时的局部机制建议，不再是项目唯一或当前下一步。当前研究顺序以 GDSC v2.0 的 PromptBundle、DecisionStateGraph、benchmark eligibility 和多表示 common-prefix intervention 为准；任何外部实验仍需单独授权。

历史设计要求是：若执行旧 P3b-B，必须先冻结 10 个可重放 prefix，并在 Card/Remove 分叉前验证 conversation 与 environment hash 完全相同；40 个短分支的模型、成本上限和停止条件需单独报告。

若没有 10 个有效 Card-visible prefix，或 next-3 repeated/repair 没有 discordant pairs，只能判定 FailureGuard/H4 在该设置下不可识别，不能再据此否定总体 graph-constrained decision-state compilation 方向。任何 pilot 有方向性结果都不能自动启动正式矩阵；应先据 discordant rate 重新计算样本量并向用户报告。
