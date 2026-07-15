# τ³-bench 集成

官方旧 τ-bench 已明确提示任务过时；当前仓库是 `sierra-research/tau2-bench`，发布名为 τ³-bench。当前版本使用 Python `>=3.12,<3.14` 和 `uv`，因此与本机 Python 3.11 核心环境分离。

## 安装

先安装 `uv`，然后运行下面的脚本。若项目 `.venv` 中已有 `uv`，脚本会优先使用它；否则使用 PATH 中的版本。

```powershell
./scripts/setup_tau3.ps1
```

脚本执行：克隆官方仓库到 `vendor/tau3-bench`、`uv sync`、把本项目 editable 安装到上游环境、执行 `tau2 check-data`。脚本强制 Python/控制台使用 UTF-8，并直接传递 `.venv` 解释器路径，兼容中文 Windows 工作区；任一外部命令失败都会返回非零状态。

## Live 条件运行

```powershell
$env:OPENAI_API_KEY = "..."
$env:TRACEGRAPH_MANAGER = "full_ours"
$env:TRACEGRAPH_BUDGET = "2048"
$env:TRACEGRAPH_OUTPUT_DIR = "outputs/tau3_live/full_ours"

.\.venv\Scripts\uv.exe run --project vendor\tau3-bench python scripts\tau3_cli.py run `
  --domain retail `
  --agent tracegraph_agent `
  --agent-llm gpt-4.1 `
  --user-llm gpt-4.1 `
  --num-trials 3 `
  --num-tasks 10 `
  --save-to tracegraph_full_ours_retail
```

对每个 manager 使用完全相同参数和 task IDs。`scripts/tau3_cli.py` 只是在启动上游 CLI 前注册 `tracegraph_agent`，评价器、environment、tools 和 user simulator 仍是官方实现。

使用智谱 BigModel/GLM 时，推荐由安全 runner 加载被忽略的 `.env`，并通过 `extra_body` 关闭 thinking：

```powershell
./scripts/run_glm_pilot.ps1 -Domain mock -TaskId create_task_1 `
  -Manager full_trajectory -Budget none -MaxSteps 8 -VerboseLogs
```

runner 不会显示 API key，并在 `.env` 未被 Git 忽略时拒绝运行。真实执行记录与当前 user simulator 协议限制见 [GLM Pilot](GLM_PILOT.md)。

若模型明确用自然语言表达结束意图但不输出 τ³ 标记，可显式启用：

```powershell
./scripts/run_glm_pilot.ps1 -Domain retail -TaskId 0 `
  -Manager full_trajectory -Budget none -NormalizeUserStop
```

`tracegraph_user_simulator` 只做确定性停止协议规范化，默认关闭；正式对比中必须跨条件固定该开关并披露。

## Live agent 的上下文语义

- 固定 agent instruction 永远保留。
- domain policy 作为 Constraint 进入图；因此 constraint ablation 是真实开关。
- 被选择的原始消息保持上游消息类型和时序。
- tool result 与对应 assistant tool call 做闭包，避免产生无前置调用的非法 ToolMessage。
- summary/archive handle 作为 `<active_trace_context>` system fragment 进入模型。
- 每次调用前保存 `trace.json` 与 `context_views.jsonl`，原始 tool payload 存入 session archive。

## 当前验证边界

核心和 JSON adapter 已在 Python 3.11.9 通过 27 项测试。官方隔离环境已用 `uv 0.11.29` 安装 CPython 3.12.13 与 `tau2 1.0.0`，并通过 `tau2 check-data`；`tracegraph 0.1.0` 已以 editable 模式导入，`tracegraph_agent` 与可选 `tracegraph_user_simulator` 已在上游 registry 中注册。GLM mock live task 已获得 reward 1.0；两次 retail task 分别暴露 user-stop 与工具 variant 选择问题，因此正式多条件 benchmark 尚未启动。
