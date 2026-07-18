# 已执行验证

## 自动化测试

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
