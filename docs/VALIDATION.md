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

本次最终复验结果：32/32 tests 通过，`compileall` 通过，CLI smoke 生成的图与 archive 校验均通过。

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

## GLM 真实调用验证

本地 `.env` 中的 GLM 凭据通过认证和模型目录查询；该文件由 `.gitignore` 排除，发布前对全部 42 个 tracked/untracked candidate files 做动态密钥扫描，泄漏数为 0。

- `zai/glm-4.5-air` 最小 Function Call：成功产生 1 个合法工具调用；
- `mock/create_task_1`：reward 1.0、DB 1.0、write action 1/1、正常 `user_stop`；
- mock 轨迹与 archive：10 nodes、6 edges，schema/hash 全部有效；
- `retail/0`：5 个预期工具动作全部无 error，写操作执行成功，但 user simulator 未产生 `###STOP###`，最终 `max_steps`、官方 reward 0；
- retail 轨迹与 archive：37 nodes、16 edges，schema/hash 全部有效；
- 可选 user-stop normalizer：问句/普通道别不触发，明确结束意图触发，幂等性测试通过；
- 第二次 `retail/0`：正常 `user_stop`，4/4 read actions，但错误键盘 variant 导致 write 0/1、DB 0、官方 reward 0；
- 三图 2048-token 离线 suite：12 个 manager、生命周期、Oracle、前缀回放与 manifest 全部生成，archive 校验通过。

retail 结果不重解释为成功，也不作为 context manager 主结果。详细成本、诊断与结构 pilot 见 `docs/GLM_PILOT.md`。

## 正式矩阵规划验证

- 固定 10 tasks × 3 trials，展开为 10 runs / 30 sessions；
- 所有 run 共享 model、base seed、trials、max steps 和 user adapter；
- 保守估算总成本 `$0.30`；
- dry-run manifest 与 10 条命令生成成功，未调用 API；
- secret-like 字段、未知 manager、重复 task/condition 均由测试拒绝；
- 缺失 cap 或 cap 低于估算时，在执行前拒绝；
- 生成的 `outputs/plans/` 继续由 Git 忽略。

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
