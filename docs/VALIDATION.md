# 已执行验证

## Phase 5.2 验证合同

Phase 5.2 新增以下 fail-closed 检查：

- 370 个请求可从 frozen manifest/schema 和当前代码逐个重建相同 request hash 与 opaque mapping；
- reward、未来事件、F5/5.1 label、prune result、token gain 和未知 event ID 在发送前被拒绝；
- 缺失/重复 span、非法 enum、截断 JSON、额外字段和未知 relation/evidence ID 不能形成有效标签；
- 结构化模型响应最多重试一次；429/网络/5xx 不消耗结构化重试名额，但每次 HTTP 尝试都计入 400 全局上限并立即暂停；
- 输入估算、实际 prompt、实际 output 和请求次数达到任一硬上限时停止；
- 15 个已观察工具必须被 `ToolEffectSpec` 一一覆盖，未知工具统一 `uncertain`；
- 完整/局部 snapshot、字段相交/不相交、retry、scalar consumption、receipt、reactivation 和 future suffix 均有测试；
- 同一 provider 消息只有在其中所有 call-level span 均 safe 时才能进入禁止发送的整组离线投影；
- 原 EventGraph、archive 和 Phase 4/5/5.1 受保护 outputs 哈希不得变化。

当前全量无标签预演覆盖 261 prefixes/1,092 predictions，determinism、future-suffix、
EventGraph unchanged、archive、protocol 和 send-forbidden 均为 100%。双遍伪标签仍因外部
rate limit 不完整，所以 pseudolabel gate 和 held-out semantic gate 都未形成。

## Phase 5 F5-WP0–WP3 离线工程验证（2026-07-28）

F5-WP0 checkpoint 在任何 tracked Phase 5 实现修改前冻结：dirty diff、tracked patch、
untracked 源文件包、三棵 Phase 4 artifact 树逐文件 hash、pytest/ruff/diff-check 日志均已
保存。checkpoint 自身 hash 复算通过，`outputs/gdsc_r0_audit`、
`outputs/gdsc_r2_1`、`outputs/phase4` 的 tree hash 在实现后仍完全一致。详情见
[Phase 5 checkpoint](PHASE5_CHECKPOINT.md)和
[Phase 5 results ledger](PHASE5_RESULTS.md)。

当前新增离线验证：

- `DecisionLifecycleGraph`、`LivenessRoots`、`LiveSubgraph`、`LifecycleContextView`
  canonical round-trip/hash；
- same-prefix determinism、future-suffix independence，以及旧派生 lifecycle 标记不污染
  prefix hash；
- explicit superseded/resolved 工具 span 回收；
- query change 对旧 span 的 raw-ref reactivation；
- archive verifier 缺失或 tamper 时 uncertainty-to-live；
- pending/missing result、side-effect receipt、parallel span 部分 live 时不 false-dead；
- duplicate/out-of-order/missing tool result 的 send-ineligible fail closed；
- retained raw messages 逐字段相等、system/tool schemas 固定、完整 request hash；
- soft budget 不诱发额外删除，hard limit 阻止发送；
- provider usage 只允许 join 同一 request hash；
- manager 不修改 EventGraph；
- `gdsc_structured_v1` 在 F5-G2 前明确拒绝。

F5-E0 随后冻结并回放了 30 个旧 session 的全部 261 个 decision prefixes：

- development manifest：
  `4da20d81ccbc61635baad08edad684234b3f74c0a51f0ca82612232b5fdd86f7`；
- 261/261 prefix hash、determinism、future-suffix、protocol、root/critical recall 和
  request hash 检查通过；
- 4/4 实际 evicted spans 的 archive hash 恢复与 query reactivation 通过；
- policy/confirmation/side-effect receipt false-dead 均为 0；
- 外部 provider generations 保持 0。

F5-G1 最终为 **No-Go**：185 个预冻结 cost-eligible prefixes 中仅 4 个完整 serialized
request 下降，paired median Prune−Raw token delta 为 `0`，未达到预冻结的严格 `<0`
门槛。权威 gate 位于
`outputs/phase5/e0_development_v1/prune_replay_v2/f5_g1_gate.json`。因此停止在 F5-G2、
Structured 和外部 pilot 之前；不得通过筛选有利 prefix 或修改阈值补跑。

最终全量回归为 `172 passed in 6.95s`；ruff 为 `All checks passed!`，compileall 与
`git diff --check` 通过。上述结果只支持工程 invariant 和 F5-G1 No-Go，不支持在线
节省、success non-inferiority、安全性或 ContextSafetyBench validity 主张。

## GDSC 验证状态（2026-07-21 实际执行）

改造前冻结基线是 `117 passed`、ruff 通过；改造后全量回归为 `144 passed`、ruff 通过。下方更早的 `69/69` 是历史快照。R0 通过，R2/E0 为 No-Go；精确 artifact 和停止原因见 [GDSC 结果账本](PHASE4_GDSC_RESULTS.md)。

新增验证清单：

- protocol closure：并行 tool calls、缺失 result、tool schema 成本、稳定 request hash；
- DecisionStateGraph：稳定 state hash、未来 suffix 不影响 prefix、conflict/supersession、slot、confirmation、side-effect receipt；
- representations：关键字段等价、claim provenance、archive tamper、非法 NegativeGuard、Omit 审计；
- compiler：hard coverage、provenance、protocol、budget 四类 invariant，软/硬预算失败、beam tie-break 与六项消融；
- risk：task-group split 无泄漏、artifact round-trip、high-risk recall/ECE/Brier gate；
- τ³：snapshot restore、实际发送对象与 request hash 一致、provider usage 回填、新旧 manager 兼容；
- governance：旧 Phase 1–4 artifact hash 不变，旧 `full_ours` 行为不变，任何 gate 失败在外部调用前停止。

推荐集成检查：

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
& .venv/Scripts/ruff.exe check src scripts tests
python -m compileall -q src tests scripts
git -c "safe.directory=E:/科研/Tools Tracing" diff --check
```

R0/R2/E0 的输入 hash、逐项 gate 值与停止原因已冻结在 `outputs/gdsc_r0_audit/`。R3/R4 因前置门禁失败保持“未运行”，不是“默认通过”。

## Phase 1–4 历史验证

### 自动化测试

本地核心环境：Windows、Python 3.11.9。

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
./scripts/run_smoke.ps1
```

覆盖归档 hash、类型边、图持久化、失败/重试/side effect、生命周期硬约束、可恢复压缩、全部 manager、消融开关、指标、固定 agent loop、τ 当前/旧格式、完整实验输出和无未来前缀图。

当前复验结果：69/69 tests 通过，`compileall` 通过，全仓 `src/`、`scripts/`、`tests/` Ruff 检查通过；CLI smoke 生成的图与 archive 校验均通过。

## 当前官方 τ³ 环境验证

通过 `scripts/setup_tau3.ps1` 在隔离的 `vendor/tau3-bench/.venv` 中完成：

- `uv 0.11.29` 管理上游环境；
- CPython 3.12.13；
- 上游发布包 `tau2 1.0.0`，77 个依赖包检查通过；
- `tau2 check-data` 通过，官方 mock、airline、retail、telecom 等域与 task sets 可发现；
- `tracegraph 0.1.0` 以 editable 模式安装；
- 调用 `register_tau3_agent()` 后，上游 registry 可发现 `tracegraph_agent`；
- `scripts/tau3_cli.py --help` 成功进入上游 CLI。

这证明安装、数据发现、包导入、适配器注册和命令入口已经接通。

## 官方 ACON 外部适配验证

- GitHub codeload 快照固定为 `microsoft/acon@d63f9ae18959dc7215ff62899c94c5e8c56847ae`，MIT license；
- 9 个执行相关源码、包入口和 prompt 文件的 SHA-256 全部匹配 `configs/acon_tau3.json`；
- 外部官方 `ObservationOptimizer` 与 `HistoryOptimizer` 已在本机和 τ³ Python 3.12 隔离环境成功实例化；
- 无 API 的 τ³ 端到端烟测确认调用上下文包含未压缩 policy、原始 task 和上游消息类型；
- 7 项适配器契约测试覆盖确定性序列化、官方参数签名、summary 状态、最近轮保留、严格失败、显式 raw fallback、provider usage/cost 和源码 hash 拒绝；
- `scripts/setup_acon.ps1` 通过 PowerShell 语法解析，目标已存在时拒绝覆盖；
- `glm-4.7-flash` 已通过 Stage 1；尚未执行 ACON official paired run，因为 compressor model、prompt/guideline、限流和 usage/cost provenance 仍需作为独立条件冻结，而非把适配器当作已产生效果结果。

## GLM 真实调用验证

本地 `.env` 中的 GLM 凭据通过认证和模型目录查询；该文件由 `.gitignore` 排除，发布前对全部 tracked/untracked candidate files 做动态密钥扫描，泄漏数为 0。

- `zai/glm-4.5-air` 最小 Function Call：成功产生 1 个合法工具调用；
- `glm-4.7-flash` 最小 Function Call：190 prompt + 12 completion tokens，成功产生指定工具调用；
- `zai/glm-4.7-flash` mock：reward 1.0、DB 1.0、write 1/1、正常 `user_stop`、实际成本 `$0.00`；
- `mock/create_task_1`：reward 1.0、DB 1.0、write action 1/1、正常 `user_stop`；
- mock 轨迹与 archive：10 nodes、6 edges，schema/hash 全部有效；
- `retail/0`：5 个预期工具动作全部无 error，写操作执行成功，但 user simulator 未产生 `###STOP###`，最终 `max_steps`、官方 reward 0；
- retail 轨迹与 archive：37 nodes、16 edges，schema/hash 全部有效；
- 可选 user-stop normalizer：问句/普通道别不触发，明确结束意图触发，幂等性测试通过；
- 第二次 `retail/0`：正常 `user_stop`，4/4 read actions，但错误键盘 variant 导致 write 0/1、DB 0、官方 reward 0；
- 三图 2048-token 离线 suite：12 个 manager、生命周期、Oracle、前缀回放与 manifest 全部生成，archive 校验通过。
- `glm-4.7-flash` Stage 1：30/30 sessions、16/30 success、normal stop 0.90、0 infrastructure error、实际成本 `$0.00`；
- 30 条 Stage 1 图已从错误的 prompt-usage 节点计量修复为 `content_estimate_v2`；1,931,320 旧计量 tokens 重算为 155,205 内容估算 tokens，0 validation error；
- 修正后 Stage 1 中位内容轨迹为 4,875，仍通过 ≥4096 gate；真实累计 agent prompt usage 中位数为 57,485；
- 新 30 图机器生命周期、Oracle、12-manager offline 与 2048/4096/8192/12288/16384 sweep 均完成，修正版推荐预算 4096；
- 2-task × 4-condition paired smoke：8/8 sessions/traces、8/8 正常停止、0 infra、所有 archive hash 有效；
- 10-task × 4-condition preliminary paired pilot：40/40 sessions/traces、40/40 archives、490 archive objects 全部验证通过；
- preliminary pilot 精确 `infrastructure_error` 为 0；Full Trajectory 的 2 个 wall-clock timeout 被分析器按基础设施型中止排除；
- 旧 10 tasks × 4 conditions × 3 trials paired matrix 的 120/120 raw sessions/traces 仍保留，但压缩条件受旧 token 口径影响，已降级为修复前诊断；
- `content_estimate_v2` corrected paired smoke：8/8 sessions/traces/archives、62 archive objects、0 graph/archive/API/参数/基础设施错误；四条件均成功 1/2；
- `content_estimate_v2` corrected lifecycle matrix：40/40 runs、120/120 sessions/traces、1,768 archive objects、0 graph/archive-reference/API/参数错误；termination 为 107 `user_stop`、11 `max_steps`、2 wall-clock `timeout`；
- corrected lifecycle matrix 的 Full Trajectory/Last-k/No-lifecycle/Full Ours raw success 分别为 13/30、13/30、14/30、17/30；Full Ours 对 Full Trajectory 的 28 个有效配对成功率差为 +0.1429，95% CI 跨 0；
- 压缩消息协议闭包在真实 `retail/0 + last_k` 故障条件上复验通过，不再产生非法 ToolMessage 序列。
- corrected failure-retention matrix `g47f_fr_c2` 完成 30/30 runs、90/90 sessions/traces；90/90 TraceGraph validation 通过，1,460 个 archive objects hash 全部有效，missing raw refs、graph errors、zero-token traces、malformed sessions 均为 0。Full Trajectory / Ours without failure retention / Full Ours raw success 分别为 10/30、4/30、7/30；Full Ours 相对 no-failure 的 25 个有效配对 success delta 为 +0.0800，95% CI [0.0000, 0.2000]，McNemar p=0.5000。全矩阵只有 2 条真实 `retries` 边、0 条 `resolves` 边，因此 H4 在当前 τ³ + GLM-4.7-Flash 组合下仍不可识别。

retail 结果不重解释为成功，也不作为 context manager 主结果。详细成本、诊断与结构 pilot 见 `docs/GLM_PILOT.md`。

## 正式矩阵规划验证

- 固定 10 tasks × 3 trials，展开为 10 runs / 30 sessions；
- 所有 run 共享 model、base seed、trials、max steps 和 user adapter；
- 保守估算总成本 `$0.30`；
- dry-run manifest 与 10 条命令生成成功，未调用 API；
- secret-like 字段、未知 manager、重复 task/condition 均由测试拒绝；
- 非负 `inter_run_delay_seconds` 写入 plan 并在 run 间显式执行；负值由测试拒绝；
- 缺失 cap 或 cap 低于估算时，在执行前拒绝；
- 生成的 `outputs/plans/` 继续由 Git 忽略。

## Stage 1 与 paired live 执行验证

- 历史 `glm-4.5-air`：10 runs / 30 sessions 全部完成，唯一失败 gate 为 success `0.40 < 0.50`；
- `glm-4.7-flash`：10 runs / 30 sessions、30 traces、30 非零 token 轨迹全部完成；
- 新 30 个 enrichment 后的 TraceGraph 均通过 `validate-trace`；
- 合并的 440 个 archive objects 全部通过 hash 校验；
- gate 聚合器读取官方 reward、termination、action checks、DB/NL/communication checks 与真实 TraceGraph token；
- `glm-4.7-flash` Stage 1 判定 `pass`：success 0.5333、normal stop 0.90、median tool calls 7、修正后 median content tokens 4,875、infra 0；
- 修正版 2048/4096/8192/12288/16384 结构 sweep 推荐 4096，并同时检查 mandatory context、Full Ours 溢出、Constraint/Failure/Evidence retention 与 unsafe removal；
- 12 managers × 30 graphs 离线结构实验及 8,868 条 prefix replay rows 生成成功；
- paired live 聚合器输出 manager/分域指标、Pass^1–Pass^k、task+trial 配对、exact McNemar、Holm 校正、bootstrap CI 和 selected-context token 配对差；
- 8-session paired smoke 完整，Full Trajectory/Last-k/No-lifecycle/Full Ours 分别成功 2/2、2/2、1/2、1/2；只作为 pipeline smoke；
- 40-session preliminary pilot 完整，四条件原始成功分别为 4/10、6/10、4/10、5/10；Full Ours 对 Full Trajectory 的 8 个有效配对成功率差为 0，selected-context token 配对均值差为 -27,146.4；
- Full Ours 对 no-lifecycle 的 10 个直接配对成功率差为 +0.10、McNemar `p=1.0`，当前只作为机器生命周期标签的待验证信号；
- 旧 120-session 三轮矩阵的成功数仍可作为修复前运行记录，但其 manager 效果比较已撤回；替代的 `g47f_ml_c2` 已用 4096 和 runtime token-accounting guard 完整执行；
- `g47f_ml_c2` 的 Full Ours 对 Full Trajectory 配对结果为 28 eligible / 2 timeout-excluded、success delta +0.1429、95% CI [-0.0714, 0.3571]、McNemar p=0.3438；estimated selected-context delta 的区间低于 0，但 actual provider input delta 区间跨 0；
- corrected smoke 同时验证估算 context tokens、累计 agent provider input tokens 与每次调用 provider input tokens；Last-k 在两个任务上每次调用平均少约 1,356 prompt tokens，但累计差受调用次数影响；
- 双标导出从 `g47f_s1_v1` 的 30 个真实图生成 120 条盲化样本，两份 CSV 各 120 条、独立顺序且不含机器预测；预测只保存在隔离 key 中，评分器验证 ID/标签并计算 Cohen's κ。

## 官方公开历史轨迹兼容验证

数据：旧版官方 `historical_trajectories/gpt-4o-airline.json`。

- 文件大小：4,114,038 bytes
- SHA-256：`E9E6C0297660C537F83D4FD9C476CE7A9A86ECD2784874B7BFC13BE598E37BFA`
- 50 tasks × 4 trials = 200 sessions
- 历史 Full Trajectory reward mean：0.42
- 导入结果：200 个唯一图、5,598 nodes、3,750 edges、73 errors、17 retries、0 个结构无效图
- 外部 archive：全部 SHA-256 校验通过
- 10-session 在线前缀回放：3,096 个 step × manager rows，step 范围 0–46

2048 token 结构离线 pilot：

| 条件 | mean tokens | compression | evidence | unresolved failure | constraint | unsafe removal |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Trajectory | 3355.6 | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 |
| Last-k | 501.0 | 0.840 | 0.987 | 0.870 | 0.005 | 7.855 |
| Summary-only proxy | 607.3 | 0.824 | 0.555 | 0.820 | 0.000 | 12.020 |
| Ours w/o failure retention | 2138.0 | 0.304 | 1.000 | 0.853 | 1.000 | 0.325 |
| Ours w/o constraint retention | 1535.9 | 0.531 | 1.000 | 1.000 | 0.075 | 0.925 |
| Full Ours | 2142.5 | 0.303 | 1.000 | 1.000 | 1.000 | 0.000 |
| Structural Oracle | 1941.1 | 0.374 | 1.000 | 1.000 | 1.000 | 0.000 |

生命周期计数：Active 2,151、Consumed 2,918、Audit-required 298、Critical Evidence 143、Superseded 15、Unresolved Failure 73。

## 解释边界

- 该数据来自官方已标记为过时的旧 τ-bench，只验证兼容性和初步结构现象，不是当前 τ³ 正式主结果。
- token 是无 provider usage 时的确定性 byte-aware 估计，正式 live run 应用 provider token usage。
- 只有 Full Trajectory 实际产生了历史 reward；其他条件 reward 为空，不能根据离线 view 声称 task success。
- 200 条中有 64 条的 mandatory context 超过 2048 token，正式实验需要更高预算或经过人工/模型验证的 policy 摘要。
- proxy baselines 不替代 AgentDiet/ACON 官方实现或真正 LLM scorer。

## GDSC v2.0 R0–R2 验证（2026-07-21）

- 改造前冻结基线 `117 passed`；改造后全量 `144 passed`，ruff 全绿，`git diff --check` 返回 0；
- R0 对旧 Phase 3 `full_ours` 的 192 个 context views 复现 192/192 graph-selected / protocol-closed 错位，中位错位 `42.040%`；
- 候选 oracle headroom 中位数为 airline `40.602%`、retail `63.798%`，R0 development gate 通过；
- E1 数据集固定为 30 source graphs、261 decision points、5,153 candidate objects、15,856 representation rows；
- R2 使用 native τ³ retail/airline OpenAI tool schemas、beam=16、五预算 `[2048,4096,8192,12288,16384]` 与六项消融；按稳定 ID 分 3 个 shard 后合并，唯一 decision points 为 261，budget rows 1,305，ablation rows 1,566；
- structured equivalence `5372/5372`、provisional decision sufficiency `261/261`、hard coverage `100%`；2048/4096 fallback `100%`，8192 起 fallback `0%`，故主预算为 8192；
- 主预算 median raw/compiled serialized tokens 为 `6451/5063`，median reduction `14.956%`，低于 `30%`，R2 gate 失败；
- E0 中 retail/airline 各 5 tasks，median actions 分别 7/6；动态 provider history、snapshot replay、native evaluator/success 证据不完整，判定 `stop_before_r3`；
- R3/R4 external sessions `0/340`，模型调用成本 `$0.00`。没有调低阈值、增样本、挑结果或自动补跑。

冻结证据：`outputs/gdsc_r0_audit/prompt_costs/prompt_cost_profile.json`、`outputs/gdsc_r0_audit/benchmark_eligibility.json`、`outputs/gdsc_r0_audit/decision_points/decision_point_dataset.json`、`outputs/gdsc_r0_audit/r2_offline/r2_offline_mechanism.json`。完整结论见 [`PHASE4_GDSC_RESULTS.md`](PHASE4_GDSC_RESULTS.md)。

## GDSC R2.1 成本归因验证（2026-07-22）

- `baseline_manifest.json` 冻结历史 R0/R2 JSON、逐点 CSV 和 config；全部 embedded hash 复算有效；
- 同一 261 个 decision points 均重建成功，历史 baseline request/cost 匹配 `261/261`；
- compiler bundle 经 Tau Message 模型往返并转换为 LiteLLM prompt 后，request hash 匹配 `261/261`；
- prompt hash 只覆盖 provider input 的 model/messages/tools；`tool_choice`、retry 与 generation controls 单列为 invocation envelope；
- policy 单次暴露、native tool schemas 顶层一致、constructive hard-state coverage 均为 `261/261`；
- runtime raw/compiled median 为 `6509/5063`，当前降幅 `15.723%`；固定 policy + tools floor 为 `4608`，理论最大降幅 `28.451%`；constructive hard-state floor 为 `4881`，降幅 `20.178%`；
- airline/retail fixed-floor 上界分别为 `29.347%/26.583%`，均低于 30%；
- 裁决 `unreachable_under_frozen_fixed_cost`，因此不实现 v1.1，不运行 R3/R4；
- 三份结果 JSON embedded hash 分别为 `e8085159…`、`5e8a1295…`、`a3237d3c…`；逐点 CSV 261 行，组件中位数 CSV 与 SVG 已生成；
- 全程没有 provider generation，外部 sessions 仍为 `0/340`，没有降低阈值、补样本或挑选 prefix。

冻结证据目录：`outputs/gdsc_r2_1/`。完整裁决见 [`PHASE4_GDSC_RESULTS.md`](PHASE4_GDSC_RESULTS.md)。

## Phase 5.1 lifecycle-evidence 验证（2026-08-01）

- `177 passed in 7.24s`；全仓 Ruff、compileall、`git diff --check` 通过；
- Phase 5.1 config 通过 Draft 2020-12 schema 检查；
- 261/261 prefix hash、冻结 F5 replay baseline、同前缀 artifact determinism、future-suffix
  independence、Grade A/ceiling protocol validity 与 request hash 均为 100%；
- Grade A 的 policy/confirmation/side-effect receipt false-dead 均为 0；
- 证据单测覆盖完整标量与 singleton wrapper、多字段结果只能进入 Grade B、JSON 类型不强制
  转换、future suffix 不可见、side-effect receipt 只保留、跨 prefix overlay 拒绝和稳定 edge ID；
- 旧受保护树哈希保持：`gdsc_r0_audit=15ac8851…`、`gdsc_r2_1=12e44336…`、
  `phase4=85a75eb9…`、`phase5=be1871f1…`；
- Phase 5.1 audit tree SHA-256 为
  `d0f2bb7009c3c4e03175bdc118a2fe9099e09eba2f6c56fd68fb0c1454c3bc4c`；
- task reward/treatment outcome 均未访问，ceiling request 未发送，external provider generations=0。
