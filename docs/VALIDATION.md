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

本次最终复验结果：22/22 tests 通过，`compileall` 通过，CLI smoke 生成的图与 archive 校验均通过。

## 当前官方 τ³ 环境验证

通过 `scripts/setup_tau3.ps1` 在隔离的 `vendor/tau3-bench/.venv` 中完成：

- `uv 0.11.29` 管理上游环境；
- CPython 3.12.13；
- 上游发布包 `tau2 1.0.0`，77 个依赖包检查通过；
- `tau2 check-data` 通过，官方 mock、airline、retail、telecom 等域与 task sets 可发现；
- `tracegraph 0.1.0` 以 editable 模式安装；
- 调用 `register_tau3_agent()` 后，上游 registry 可发现 `tracegraph_agent`；
- `scripts/tau3_cli.py --help` 成功进入上游 CLI。

这证明安装、数据发现、包导入、适配器注册和命令入口已经接通。真实 agent/user 对话仍需模型 API key，并会产生外部模型调用成本，因此未在无密钥环境中执行。

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
