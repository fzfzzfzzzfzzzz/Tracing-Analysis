# Qwen3.8-27B 自建 benchmark Harness 修复与重跑（2026-09-18）

## 结论

首轮 27B 统一矩阵 `qwen38_27b_unified_methods_b1024_260918_r1` 虽完成 208/208，但其中 198 条为方法失败，不能用于与 14B 比较。失败的主要原因是 Qwen3.8/SGLang 被分配到旧式 `a/e/t/s + regex response_format`，而 14B/vLLM 使用 typed server fields；此外三种生成式压缩方法使用无约束纯文本 construction transport，Qwen3.8 会返回空正文或仅返回格式标题。

现已为 Qwen3.8 增加两项 provider 适配：

1. 答案使用已有的 typed `native_tool_call` 协议；
2. construction 使用新的单字段 typed `submit_memory_v1` 工具，原始工具响应完整写入 provider ledger，方法层只接收工具参数中还原出的纯文本 memory。

方法提示、数据、history budget、context window、最大输出、non-thinking、方法解析逻辑和评分协议均未改变。

## 首轮失败证据

- 208/208 episode 完成，但只有 10 条有效，198 条 `method_failure`。
- 主矩阵 144 条中只有 10 条 `distractor_current` 有效；失败链和失败原因题均无有效结构化答案。
- 27B 初始答案 116/137 达到 2048-token 上限；14B 参照仅 8/193 达到上限。
- 71 条主矩阵 episode 因方法上下文构建失败而不可答：ACON 24、Rolling Summary 24、AMA 23。
- 47 次 construction 请求中 18 次返回空正文；AMA 常只返回 `**STATE_MEMORY**` 标题。

因此 r1 应标记为 Harness/transport 失败，而不是 27B 能力结果。

## 答案 transport 校准

校准配置：[server_eval_qwen38_27b_native_calibration_b1024_260918_r2.json](../configs/server_eval_qwen38_27b_native_calibration_b1024_260918_r2.json)

远端输出：`/data/fangc/qwen38_27b_native_calibration_b1024_260918_r2`

40 条校准结果：

- 有效完成：40/40
- first format valid：37/40，门槛 36
- final format valid：40/40，门槛 38
- Full History hard pass：21/24，门槛 20
- Oracle hard pass：10/16，门槛 14，未通过
- 规则裁判：40/40，critical false positive 为 0

native answer transport 已消除协议失败。Oracle 的 6 条失败来自失败链题多引无关 evidence，属于模型引用行为，不是 transport 或 JSON 失败。原始模型门禁仍记为未通过。

## Construction 探针与修复

construction-only thinking 探针：`/data/fangc/probes/qwen38_construction_thinking_260918_r3.json`

- Rolling Summary：2/2 仍无最终正文；
- AMA：2/2 思考到 2048 tokens 后仍无最终正文；
- ACON：2/2 有正文，但一条达到 2048-token 上限。

因此没有通过开启 thinking 掩盖问题。

第一版自动工具选择 smoke：`/data/fangc/qwen38_27b_construction_native_smoke_260918_r4`

- Rolling 2/2、ACON 2/2 完成；
- AMA 2/2 继续选择普通正文而非工具调用。

随后将 construction tool choice 从 `auto` 收紧为指定 `submit_memory_v1`。最终 smoke：`/data/fangc/qwen38_27b_construction_native_smoke_260918_r5`

- 2 个前缀 × 3 种生成式方法 = 6/6 build 完成；
- Rolling memory resident token：483、284；
- ACON memory resident token：348、503；
- AMA：`ingest_eligible=true`，resident token 为 310、117；
- 全部低于 1024-token history budget。

实现位于 [provider.py](../src/tracegraph/benchmark/server_eval/provider.py) 和 [config.py](../src/tracegraph/benchmark/server_eval/config.py)。相关 server-eval 测试 67/67 通过。

## 完整重跑

配置：[server_eval_qwen38_27b_unified_methods_b1024_native_260918_r6.json](../configs/server_eval_qwen38_27b_unified_methods_b1024_native_260918_r6.json)

- 配置 SHA-256：`7c47d1ded7a4c32358c4c1f58febecd44ba023150d2e337b799c0511ff906f68`
- 启动时间：2026-09-18 08:47 CST
- tmux：`qwen38_internal_matrix_native_260918_r6`
- 准备目录：`/data/fangc/prepared_qwen38_27b_unified_methods_b1024_native_260918_r6`
- 输出目录：`/data/fangc/qwen38_27b_unified_methods_b1024_native_260918_r6`
- 日志：`/data/fangc/logs/qwen38_27b_unified_methods_b1024_native_260918_r6.log`
- 计划：208 episodes，包含 40 calibration、144 main、24 diagnostic
- 方法：Full History、Flat BM25、Rolling Summary、ACON official、AMA official BM25、TraceGraph 0.4

本次显式使用仅限开发用途的模型校准覆盖，以便和 14B 统一矩阵保持相同研究口径。原始 Oracle 10/16 未通过仍保留为权威门禁记录，不能将 r6 表述为正式独立测试。

## 比较边界

14B 与 27B 使用相同语义答案契约，但 provider wire transport 不同：14B/vLLM 使用 typed guided JSON，27B/SGLang 使用 typed native tools。construction tool 只包装完整 memory 字符串，不改变 ACON、AMA、Rolling Summary 的提示和解析器。论文中应将其披露为 provider-specific structured-output adapter，并单独报告有效运行率、构建调用数和 token 成本。

## r6 完成后的外部方法桥接修复

r6 于 2026-09-18 10:02 CST 完成 208/208，provider 580 次请求没有网络或服务错误；但 17 条 `integration_invalid` 暴露出两个更窄的外部方法桥接问题：

- ACON 有 5 个 prefix 的 observation/history construction 在生成过长内容时达到 2048-token 上限；这 5 个共享 build 导致 15 个 episode 无效。
- AMA 有 2 个 build 生成了合法的 `memory_summary:`，但漏掉上游解析器要求的 `**STATE_MEMORY**` 标题；另有检索调用返回空正文或进入当前 disabled code-search。

修复没有改写方法输出中的事实：

1. typed construction schema 增加可冻结的 `content.maxLength`，b1024 配置固定为 4096 字符，阻止模型无限复述 neutral observation；最终 1024-token budget 检查仍由原方法层执行。
2. AMA 只在正文已经以 `memory_summary:` 开头时补上缺失的 `**STATE_MEMORY**` 协议标题；其他不可解析文本仍失败。
3. AMA retrieval 使用独立的 typed `submit_retrieval_v1`，同样固定为 4096 字符，不改变 `SUFFICIENT/NEED_GRAPH/NEED_CODE` 内容。
4. 151 服务器没有 Docker，但 `/usr/bin/bwrap` 可用；AMA code-search 改为只读系统目录、临时 `/tmp`、无网络、最小环境变量的用户命名空间沙箱。沙箱已用真实服务器进程验证。

定向验证输出：

- r7（8192 construction output）：单次请求在 180 秒超时，作为失败边界保留。
- r8（4096 construction output）：仍出现截断，证明单纯放宽 token 上限不是修复。
- r9（4096 字符 typed 上限）：5/5 个旧 ACON build 失败全部恢复；5/5 AMA build 完成；9/10 旧 AMA materialize 异常恢复。
- r10（retrieval 同样加入 4096 字符上限）：剩余 AMA `audit_chain` 完成，最终上下文 632 tokens。

本地 server-eval 回归测试为 71/71 通过；远端新增适配测试与真实 `bwrap` 执行均通过。

## ACON/AMA 正式修正矩阵

配置：[server_eval_qwen38_27b_acon_ama_repair_b1024_native_260918_r11.json](../configs/server_eval_qwen38_27b_acon_ama_repair_b1024_native_260918_r11.json)

- 配置 SHA-256：`2cade3341455b1262f69b8eb4471c3a18814730fec3dff1c4b7acaa944b6fd16`
- 启动时间：2026-09-18 10:52 CST
- tmux：`qwen38_acon_ama_repair_matrix_260918_r11`
- 准备目录：`/data/fangc/prepared_qwen38_27b_acon_ama_repair_b1024_native_260918_r11`
- 输出目录：`/data/fangc/qwen38_27b_acon_ama_repair_b1024_native_260918_r11`
- 日志：`/data/fangc/logs/qwen38_27b_acon_ama_repair_b1024_native_260918_r11.log`
- 计划：112 episodes；主矩阵只包含 ACON official 与 AMA official BM25，其他四种方法沿用 r6。

r11 仍使用与 r6 相同的开发用途校准覆盖。合并分析必须保留 run ID、适配字段和有效运行率，不能把 r11 伪装成 r6 内部的原始结果。
