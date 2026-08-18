# GDSC 第四阶段执行结果与门禁状态

> 结果账本版本：`phase4_gdsc_results_v2`
>
> 建立日期：2026-07-21
>
> R2.1 更新日期：2026-07-22
>
> 当前判定：R0 development gate 通过；R1 工程完成；R2 与 E0 均为 No-Go；R2.1 进一步证明冻结约束下 30% 总请求降幅不可达，继续停止在 R3 之前

## 1. 结论先行

本页与旧 [`PHASE4_RESULTS.md`](PHASE4_RESULTS.md) 分离。旧文件记录 Failure Card/P3b-A 的工程结果；本页记录 GDSC v2.0 及其 R2.1 诊断分支。历史 R2 的 median serialized reduction 为 `14.956%`，低于预注册的 `30%`；R2.1 用真实 τ³/LiteLLM prompt 口径重算后为 `15.723%`，而“完整 policy + native tool schemas、无动态历史”的乐观上界也只有 `28.451%`。因此 30% 在冻结 GDSC-Core 约束下不可达；完整 E0 仍不合格，没有运行 R3/R4 外部会话，也没有资格声称在线 token 改善、success non-inferiority 或相对 ACON 优势。

改造前冻结工程基线为 `117 passed`、ruff 通过；R2 完成时回归为 `144 passed`。R2.1 新增归因与发送口径测试后最新全量回归为 `147 passed`，ruff 通过，`git diff --check` 返回 0。新增通过不改变任何旧 artifact 或旧 `full_ours` 语义。

## 2. 阶段状态

| 阶段 | 目标 | 当前状态 | 下一 gate |
| --- | --- | --- | --- |
| WP0 | 架构/格式/指标/实验/主张文档同步 | 完成；相对链接检查与 `git diff --check` 通过 | R0 artifact hash 与成本审计 |
| R0 | 192-view 成本错位、≥30% oracle headroom、历史 hash冻结 | 通过；192/192 mismatch，候选域 headroom 均 ≥30% | R1/R2 离线机制 |
| R1 | PromptBundle、DecisionStateGraph、DecisionQuery、τ³ request provenance | 工程完成；确定性、closure、预算与 τ³ 集成测试通过 | R2 合取 gate |
| R2 | 六表示 compiler、risk gate、budget sweep、E1/E4 | **No-Go**；主预算 8192，median serialized reduction 14.956% <30% | 停止 |
| R2.1 | 261 点成本归因、发送对象一致性与 30% 可达性 | **完成 / 分支 B**；runtime hash 261/261，固定成本上界 28.451% <30% | 冻结不可达性报告；不实现 v1.1 |
| E0 | retail/airline eligibility | **No-Go**；每域仅 5 tasks，median actions 7/6，且多项证据缺失 | 停止在 R3 前 |
| R3 | 30-prefix Raw/Compiled/Drop pilot | 未运行；被 R2/E0 双门禁阻止 | 不适用 |
| R4 | 20 tasks × 2 seeds × 4 conditions | 未运行；被 R2/E0 双门禁阻止 | 不适用 |

## 3. 已冻结边界

- 旧 Phase 1–4 artifacts 与 `full_ours` 不追溯修改；
- 本轮只使用 τ³ retail/airline；
- live 默认且唯一允许模型为 `zai/glm-4.7-flash`，只允许免费额度，无付费 fallback；
- E0、R2、R3 任一 gate 失败即停止；
- R3/R4 外部 cap 合计 340 个分支/会话；
- 最强允许结论是“τ³ 跨域 development positive evidence”；
- 第二 benchmark、两位独立人工 gold 与 archive recovery 效果不在本轮结果范围。

## 4. R0/R1/R2 结果

| 项目 | 观测值 | 阈值 | 产物/hash | 判定 |
| --- | ---: | ---: | --- | --- |
| 192-view cost mismatch reproduced | 192/192；median graph→protocol-closed mismatch `42.040%` | true | `prompt_cost_profile.json`; `5dac1ac…` | 通过 |
| candidate oracle headroom | airline `40.602%`；retail `63.798%` | ≥30% | `benchmark_eligibility.json`; `b01a518…` | 通过 |
| stable prefix/state/request hash | deterministic regression 全部通过 | 100% | `tests/test_gdsc_core.py`, `tests/test_gdsc_integration.py` | 通过 |
| hard coverage | 五个预算均 `100%` | 100% | `r2_offline_mechanism.json`; `7205f73…` | 通过 |
| structured key equivalence | `5372/5372 = 100%` | 100% | 同上 | 通过 |
| provisional decision sufficiency | `261/261 = 100%` | ≥95% | 同上 | 通过（仅机器 provisional） |
| conservative fallback | 2048/4096 为 `100%`；8192 起 `0%` | ≤5% | 同上 | 主预算 8192 通过 |
| median serialized marginal cost reduction | `14.956%`（raw 6451，compiled 5063） | ≥30% | 同上 | **失败** |
| harm positives / high-risk recall / ECE / Brier | 无合格 counterfactual harm gold，未训练统计 artifact | 20 / .90 / .10 / better | deterministic safety mask | 统计模型不合格；保持 deterministic |

R2 sweep 使用全部 `261` 个冻结 decision points、原定五个预算、beam=16 和六项消融。因单进程逐步重序列化超过 10 分钟，执行按排序后的 decision-point ID 做三个模 3 确定性 shard；合并器验证 `261` 个唯一点、`1305` 条 budget rows、`1566` 条 ablation rows 和共同 dataset hash。该调度不改变样本、阈值或 compiler 参数。

### 4.1 R2.1 成本归因与可达性裁决

R2.1 不覆盖上述 v1 No-Go。它先用 `baseline_manifest.json` 冻结历史四份 JSON、逐点 CSV 与 `gdsc_core_v1` config，再对同一 261 点执行双口径审计：历史 compiler bundle 只用于验证旧 hash；主归因使用 τ³ Message → LiteLLM 的真实 prompt 转换。生成选项与 retry 参数归入 invocation envelope，不计入 provider input-token 的 prompt hash。

| 项目 | 观测值 | 阈值/要求 | 判定 |
| --- | ---: | ---: | --- |
| 冻结 baseline request/cost | `261/261` | 100% | 通过 |
| runtime prompt hash | `261/261` | 100% | 通过 |
| policy 仅暴露一次 | `261/261` | 100% | 通过 |
| native tool schemas 顶层一致 | `261/261` | 100% | 通过 |
| runtime raw / compiled median | `6509 / 5063` | — | 当前降幅 `15.723%` |
| 固定 policy + tools floor median | `4608` | 总降幅 ≥30% | 最大降幅 `28.451%`，**不可达** |
| constructive hard-state floor median | `4881` | 总降幅 ≥30% | 最大降幅 `20.178%`，**不可达** |
| airline fixed-floor max reduction | `29.347%` | ≥30% | **不可达** |
| retail fixed-floor max reduction | `26.583%` | ≥30% | **不可达** |

裁决为 `unreachable_under_frozen_fixed_cost`，采用 R2.1 分支 B：不创建 `gdsc_core_v1.1`，不压缩完整 policy、不删除 native tool schemas、不降低 30% 阈值、不挑选高 headroom prefixes，也不运行 R3/R4。若未来研究 GDSC-Policy 或新 benchmark，必须新开预注册并重新执行 E0/R2。

冻结产物：

- `outputs/gdsc_r2_1/baseline_manifest.json`，embedded hash `e8085159…`；
- `outputs/gdsc_r2_1/cost_attribution.json`，embedded hash `5e8a1295…`；
- `outputs/gdsc_r2_1/attainability_report.json`，embedded hash `a3237d3c…`；
- `outputs/gdsc_r2_1/cost_attribution_rows.csv`，261 行；
- `outputs/gdsc_r2_1/cost_attribution_component_medians.csv` 与 `fixed_cost_reachability.svg`。

## 5. E0 eligibility

| Domain | tasks | median actions | dynamic history | headroom | lifecycle coverage | snapshot | Full success | evaluator | 判定 |
| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- |
| retail | 5 | 7 | 缺失 | 63.798% | 0 类达到 30 points | false | 缺失 | false | **失败** |
| airline | 5 | 6 | 缺失 | 40.602% | 0 类达到 30 points | false | 缺失 | false | **失败** |

两域稳定任务清单均为 task ID `0,1,2,3,4`，每域 15 sessions。未按 GDSC 效果重新挑选任务，也未补样本。E0 报告判定 `stop_before_r3`，hash 为 `b01a518e02ec4eab506d2cb4e4461fe5050dc72cc9691f3c0fedc1ba4f9a3e4f`。

## 6. R3 登记模板

| 检查 | 观测值 | 阈值 | 判定 |
| --- | ---: | ---: | --- |
| treatment/snapshot/hash integrity | — | 100% | 被 R2/E0 阻止 |
| Compiled median provider-input reduction | — | ≥15% | 被 R2/E0 阻止 |
| Compiled-only policy/irreversible harm | — | 0 | 被 R2/E0 阻止 |
| representation-induced harm | — | ≤1/30 | 被 R2/E0 阻止 |
| Compiled/Drop discordant prefixes | — | ≥5，且多数支持 Compiled | 被 R2/E0 阻止 |

## 7. R4 登记模板

每个条件分别报告 retail、airline 与 pooled 的 sessions、success、policy/collateral、actions、repeat/recovery、五层 token、agent/user/compressor session total、净 token cost、provider monetary cost、infra exclusions 和全部 paired CI。

| 合取判定项 | 观测值 | 阈值 | 判定 |
| --- | ---: | ---: | --- |
| GDSC input/action reduction vs Full | — | ≥15%，95% CI upper `<0` | 未运行 |
| session total / net token cost | — | 均下降 | 未运行 |
| success risk difference | — | 95% CI lower `≥-0.05` | 未运行 |
| policy/collateral | — | 不增加 | 未运行 |
| action/repeat/recovery | — | 不恶化 | 未运行 |
| advantage vs official ACON | — | 至少一个 matched frontier 优势 | 未运行 |
| retail/airline direction | — | 一致 | 未运行 |

## 8. 验证记录

WP0（2026-07-21）：检查 README、handoff、计划与 `docs/*.md` 的 Markdown 相对链接，结果 `MARKDOWN_LINKS_OK`；`git diff --check` 返回 0。

R0–R2（2026-07-21）：`144 passed`；ruff `All checks passed!`；192-view R0 profiler、30-source/261-decision E1 数据、五预算 E4 sweep 与六消融均完成。外部 sessions 消耗 `0/340`，provider monetary cost `$0.00`。τ³ 本地初始化曾尝试获取 LiteLLM price map，被沙箱拒绝后使用本地备份；没有模型生成调用。

R2.1（2026-07-22）：`147 passed`；ruff `All checks passed!`；`git diff --check` 返回 0。同一 261 点完成成本归因、Tau/LiteLLM 往返与 prompt hash 审计；三份 JSON embedded hash 全部复算有效，CSV 为 261 个唯一点。归因运行只加载本地 τ³ schema 和 LiteLLM 备份价格表，没有 provider generation；外部 sessions 仍为 `0/340`，provider monetary cost `$0.00`。

停止原因：`r2_gate.passed=false`、`r2_1.attainability_decision=unreachable_under_frozen_fixed_cost`，同时 `benchmark_eligibility.eligible=false`。依预注册不运行 R3 或 R4，不进行免费价格在线复核，因为没有获准进入任何 live session 启动阶段。

每次更新本页时附上：

```text
timestamp:
git revision / dirty diff hash:
pytest:
ruff:
diff --check:
archive/hash audit:
input manifests:
output artifacts:
external sessions consumed / 340:
pricing evidence and estimated monetary cost:
stop reason:
```

在记录完整前，状态保持“未判定/未运行”，不得根据代码存在、单元测试通过或点估计方向补写正向结论。
