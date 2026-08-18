# GDSC R0–R4 预注册

> 协议版本：`gdsc_preregistration_v1`
>
> 冻结日期：2026-07-21
>
> 范围：τ³-bench retail / airline development study
>
> 方法：Graph-Constrained Decision-State Compiler（GDSC）

## 1. 研究问题与证据边界

研究问题是：在不降低任务成功率与 policy compliance 的前提下，能否把不断增长的工具轨迹编译为下一动作所需的最小、可验证决策状态，并显著降低最终 provider input 与 session 总 token cost。

本轮只使用现有 τ³ retail/airline。它可以支持“τ³ 跨域 development positive evidence”，不能单独支持最终 AAAI 双 benchmark、正式 construct-gold 或 archive-recovery 主张。两位独立人工 gold 和第二 primary benchmark 不在本轮范围。

旧 Phase 1–4 与 `full_ours` 是历史兼容边界：保留原 hash、行为、结果与 No-Go 解释。旧 Card-only P4 gate 既不能替代本协议，也不因本协议被追溯改写。

## 2. 冻结实现身份

- GDSC manager：`decision_state_compiler`；
- context policy version：`gdsc_core_v1`；
- 组合 manager：`acon_official_with_gdsc_state`；
- EventGraph：原 `TraceGraph` 不可变兼容层，不扩写旧 `NodeType`；
- state layer：独立 `DecisionStateGraph`；
- compiler：hard closure 优先、确定性 beam search、默认 beam width 16、canonical tie-break；
- representations：`RAW_MESSAGE`、`STRUCTURED_STATE_DELTA`、`VERIFIED_SUMMARY`、`NEGATIVE_GUARD`、`ARCHIVE_HANDLE`、`OMIT`；
- omission risk：默认 deterministic safety mask；统计 artifact 只在其独立门禁通过后启用。

核心接口冻结为：

```text
compile(event_graph, decision_state, query, provider_protocol, budget, risk_model)
  -> PromptBundle
```

`PromptBundle` 必须包含最终 messages/tools、representation manifest、closure provenance、request hash、五层成本和 compiler decision log。

## 3. 成本与安全口径

五层成本固定为 graph-selected、compiled、protocol-closed、serialized request 和 provider-actual。serialized request 包含 system、messages、tool schemas 与协议格式开销；provider-actual 仅由 provider usage 回填。

软实验预算不可行时，编译器返回保留 hard state 的 conservative over-budget bundle，并设置 `matched_budget_eligible=false`。超过 provider hard context limit 时在调用前中止。不得为了满足预算丢弃 hard fact、关键 policy、未确认的不可逆动作条件或唯一不可恢复证据。

`VERIFIED_SUMMARY` 只从 verified atoms 确定性渲染。`NEGATIVE_GUARD` 只允许来自 schema、policy predicate、环境状态或验证过的替代路径。`ARCHIVE_HANDLE` 在 R4 前只作 provenance，不计关键证据可见覆盖，也不作恢复效果主张。

## 4. R0：治理、成本审计与开发门禁

R0 不调用外部模型。必须：

1. 冻结旧 Phase 1–4 artifact hashes；
2. 记录改造前 `117 passed`、ruff 通过的基线；
3. 复现 192-view 的 graph-selected / protocol-closed / serialized cost 错位；
4. 对候选子集计算 provider-token oracle headroom；
5. 生成 claim-evidence matrix、eligibility report schema 与本预注册。

R0 开发 gate：成本错位可复现，且至少一个候选子集 provider-token oracle headroom ≥30%。不满足则停止 R1–R4 的实验性推进，并报告测量/benchmark 不适配。

## 5. R1：状态层与 PromptBundle 验收

必须验证：

- 纯 tool-call assistant 也产生 Decision event；
- 相同 prefix 重建得到相同 event/atom/edge/state/request hashes；
- prefix 后追加未来事件不改变既有 cutoff state；
- reducer 从 neutral lifecycle 重算且只读 cutoff 内事件；
- conflict、supersession、slot、confirmation、side-effect receipt 均有稳定 provenance；
- `DecisionQuery` 由 pending operation、retry、tool schema 与 policy scope 确定构造；不确定时保留候选工具而非猜成单工具；
- LLM parser fallback 只能生成低置信 provisional atom；
- pre-send request artifact 与实际发送对象 hash 一致，post-return usage 关联同一 request hash。

任何未来信息泄漏、随机 ID、hard-state 丢失或 request/hash 不一致都阻止 R2。

## 6. R2：多表示离线机制实验

运行 E1/E4 和 serialized budgets `[2048,4096,8192,12288,16384]`。主预算选择规则：在 hard coverage 100% 的预算中，选择 conservative fallback ≤5% 的最小值；无预算满足则 R2 No-Go。

R2 gate 同时要求：

- structured representation 关键字段等价率 =100%；
- provisional decision sufficiency ≥95%；
- median serialized marginal cost 相对 Raw 下降 ≥30%；
- protocol、provenance、coverage、budget 四类 invariant 全部通过；
- archive tamper、非法 Guard、缺失 tool result 等负向 fixture 全部 fail closed。

统计 risk model 使用 task-group held-out split，不得让同一 task/prefix 跨 train/test。启用门禁为 harm positives ≥20、high-risk recall ≥0.90、ECE ≤0.10 且 Brier score 优于常数基线。任一不满足时，R4 继续使用 deterministic safety mask；不得放宽阈值或把训练集指标当 held-out 指标。

## 7. E0：R3 前 benchmark eligibility

任务只依据冻结的结构特征选择，以 task ID 稳定排序，禁止参考 GDSC treatment outcome。retail 与 airline 每域均必须满足：

| 检查 | 阈值 |
| --- | ---: |
| 固定任务数 | ≥10 |
| median agent tool actions | ≥10 |
| dynamic-history decision points | ≥40% |
| provider-token oracle headroom | ≥30% |
| lifecycle 现象 | 至少三类、每类 ≥30 decision points |
| snapshot replay | 100% hash/restore 通过 |
| Full success | 20%–85% |
| native evaluator | 能识别任务成功与副作用 |

先执行 R4 Full 条件首 seed 的 20 sessions，作为 eligibility 与 prefix-capture tranche。任一域不通过完整 E0，则停止，不挑选更有利任务、不降阈值、不增加样本、不进入 R3。

## 8. R3：common-prefix 表示干预

从 retail 与 airline 各冻结 15 个 prefix，共 30 个；六类对象各覆盖至少 5 个。每个 prefix 保存 conversation、environment snapshot、EventGraph、DecisionStateGraph、DecisionQuery、tool schema、representation payload 及全部 hashes。

冻结设计：

- treatments：Raw / Compiled / Drop；
- replicates：2；
- 总分支：`30 × 3 × 2 = 180`；
- 最多 3 个 agent tool actions；
- temperature=0、并发=1；
- 不自动补跑。

R3→R4 gate 是合取条件：

1. treatment 注入与 snapshot/hash 检查 100% 通过；
2. Compiled provider input 中位数相对 Raw 至少下降 15%；
3. 无 Compiled 独有 policy violation 或不可逆副作用；
4. representation-induced harm 最多 1/30 prefix；
5. Compiled/Drop 至少 5 个 discordant prefixes，且多数方向支持 Compiled。

安全条件失败、干预未真正进入 prompt 或 discordance 不可识别均立即停止，不运行 R4 剩余矩阵。

## 9. R4：τ³ development matrix

冻结样本为 10 retail + 10 airline tasks、2 seeds、4 conditions，共 160 sessions。E0 已运行的 20 个 Full sessions计入总数；通过 R3 后最多再运行 140 sessions。

四条件为：

1. Full Trajectory；
2. hash-pinned official ACON；
3. GDSC-Core；
4. official ACON + GDSC safety/state layer。

agent、user simulator、ACON compressor 均固定 `zai/glm-4.7-flash`；temperature=0、max steps=50、timeout=900 秒、并发=1、run 间隔20秒。native τ³ evaluator 与 trajectory generation 解耦。

外部调用总 cap 为 340 个分支/会话（R3 180 + R4 160，重用的 Full tranche不重复计）。启动时必须重新核验官方免费价格并把来源、时间和估算写入 manifest。任何非零计费估算、免费额度不足、模型切换/fallback 请求或 cap 超限都在调用前 fail closed。

## 10. 正向判定

只有以下全部成立，才能写“τ³ retail/airline 跨域 development positive evidence”：

- GDSC-Core input/action 相对 Full 至少下降 15%，且配对 bootstrap 95% CI 上界 `< 0`；
- session total tokens 与净 token cost 均下降；
- success risk-difference 95% CI 下界 `≥ -0.05`；
- policy violation 与 collateral damage 不增加；
- action count、repeated invalid 与 recovery 不恶化；
- 相对 official ACON 存在 matched-cost reliability 或 matched-reliability token 优势；
- retail 与 airline 方向一致。

未满足合取门禁时，报告对应 No-Go、负结果或不可识别性，不使用“趋势正向”替代预注册判定。

## 11. 排除、缺失与停止规则

- infrastructure failure 与 agent failure 分开；排除理由必须在看 outcome 前冻结并逐 session报告；
- 无自动补跑；任何人工重跑需新 manifest、原因和 cap 计算，不得覆盖旧记录；
- provider usage 缺失、snapshot/hash 不匹配、treatment 未注入、非预注册模型均无主结果资格；
- 所有 JSON/CSV/图表只从冻结 artifacts生成；
- 每阶段运行 pytest、ruff、diff check 与 archive/hash audit；
- E0、R2、R3 任一门禁失败时，后续外部矩阵不得启动。
