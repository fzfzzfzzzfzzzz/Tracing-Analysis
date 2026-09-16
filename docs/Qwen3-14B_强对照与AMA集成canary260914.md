# Qwen3-14B 强对照与 AMA 集成 canary（260914）

## 阶段结论

TraceGraph 的原子失败链修复已在 8 个公开开发前缀上通过：512 与 768 token 两档均为 24/24 hard pass，其中完整失败链题均为 8/8。强方法单前缀 canary 随后确认 TraceGraph、完整历史和 BM25 都能完成 3/3；ACON 与 AMA 的早期 0/3 则分别定位为指导词/证据呈现问题和预算适配问题，不能再解释为“题目本身不可答”或统一归因于模型能力。

AMA 的官方适配链已修通到“相同预算下真实执行”的程度。最终 r6 中三道主问题全部通过发送资格检查，构建、检索和回答均实际调用 Qwen3-14B；其 0/3 是暴露开发切片上的内容选择结果，而不是构建失败、格式失败或预算超限。按 `pro260912`，下一步转入预注册的 ACON 指导词调优，而不是继续针对这个单前缀改 AMA。

## 强方法单前缀 canary

运行：`qwen3_14b_strong_methods_canary_260914_r2`，预算 768，公开前缀 `controlled:parameter_schema:software:R0:long`。64/64 episode 完成，220 次 provider 调用，校准通过，离线复评分未产生新调用。

| 方法 | hard pass | evidence pass | 发送合格 | 平均上下文 token | 解释 |
|---|---:|---:|---:|---:|---|
| full history | 3/3 | 3/3 | 3/3 | 4966.00 | 可答上界 |
| flat BM25 archive | 3/3 | 3/3 | 3/3 | 685.00 | 强检索基线 |
| TraceGraph 0.4 | 3/3 | 3/3 | 3/3 | 237.33 | 在该公开切片上保住完整因果证据 |
| recent masking | 1/3 | 1/3 | 3/3 | 756.00 | 只保住当前状态题 |
| rolling summary | 0/3 | 0/3 | 3/3 | 219.00 | 摘要内容不足，而非超预算 |
| ACON official | 0/3 | 0/3 | 3/3 | 209.00 | 语义片段存在但原始证据 ID 未可靠保留 |
| AMA official BM25 | 0/3 | 0/3 | 0/3 | 0.00 | 初始集成在构建阶段截断，未形成可发送上下文 |

这只是一个暴露前缀的集成 canary，不能据此宣称任何方法总体优越。

## AMA 预算适配修复阶梯

所有步骤均复用同一公开前缀、同一模型、同一 768-token 外层预算；每轮都保留不可变配置、prepared、真实响应和 provider ledger。

| 轮次 | 修复目标 | 结果 |
|---|---|---|
| r3 | 将构建任务改为查询盲、预算感知的状态记忆压缩，并缩小 session | 模型不再因 2048 token 截断，但返回 JSON 数组；官方提取器找不到 `memory_summary:`，3/3 构建后均不可用 |
| r4 | 明确官方 `**STATE_MEMORY**` / `memory_summary:` 输出协议 | 构建成功；官方检索仍使用近乎无限上限，三道上下文为 877/1093/1079 token，3/3 超预算 |
| r5 | 给官方检索传入 1536 字符上限 | 失败原因题 626 token、当前题 549 token 可发送；完整链题 877 token 仍不可发送，合格 2/3 |
| r6 | 将官方检索上限保守收紧到 768 字符，并保留外层精确 tokenizer 拒绝机制 | 三道题 295/669/335 token，发送合格 3/3；49/49 episode、186 次调用完成 |

r6 的构建共 5 次模型调用，输入/输出为 7471/2340 token；检索共 3 次调用，输入/输出为 2698/352 token。最终内容分数仍为 0/3：失败原因题只呈现 E005；完整链题呈现 E001–E005、缺 E006/E007；当前题呈现 E006/E007、缺 E023。因此当前应保留这一结果作为官方 AMA 在暴露切片上的真实表现，不再为追分修改官方算法语义。

## ACON 指导词调优

已冻结并准备三个候选，仅使用 8 个已暴露开发前缀：

| 候选 | 指导词意图 | config hash | episode | 请求上限 |
|---|---|---|---:|---:|
| original | 官方原始行为，不追加指导 | `d1f76584f60610e2770b884d5ac43f6d33f7fd3ebd06d2c984dab9637be5732a` | 112 | 672 |
| evidence | 保留事实旁的精确原始 ID，优先删除重复文字 | `c667780a9a196b7bd94c74c954f93ac0b0af1aed963d353cd6fbb8c599975d10` | 112 | 672 |
| causal | 保留失败、诊断、决策、恢复链接及当前状态覆盖 | `0671dc7827955deb9f8e31b17b108eb8f68fce218ab376378032e64ae31455d7` | 112 | 672 |

三候选均 `live_ready=true` 且无 blocker；总请求上限 2016。选择规则在见结果前已固定为：先最大化 ACON 主问题 hard pass，再最小化 ACON 部署 token，最后按候选名稳定打破平局。该选择属于开发调参，不是独立验证，也不是官方 ACON 指导训练的复现。

真实长跑已于 2026-09-14 17:19:37（Asia/Shanghai）在服务器 tmux 会话 `qwen3_14b_acon_tune_260914_r1` 启动，顺序为 `original → evidence → causal → tune-select`。代码位于 `/home/fangc/tracegraph-server-eval-260914-acon-tune-r1`；模型、prepared、运行、复评分和日志位于 `/data/fangc`。仅使用空闲 GPU 0、1，GPU 4–7 未触碰；脚本在结束或异常退出时清理本轮 vLLM 进程。

prepared 已同步回本地，31/31 个文件逐一比对无差异。准备计划 SHA256：`c8b27739ebd05f869de613410146478adec9dc29e1ab0a2f3ef378d6036eded5`。启动脚本服务器/本地 SHA256：`b46a04bec2814ed2fdc2e158089929f3ec60d4a4678a69c5fd3038433c104ff9`。

## 代码与验证

AMA 适配改动位于 `src/tracegraph/benchmark/server_eval/methods.py`：构建侧使用半预算状态记忆、4096 字符起步的 bounded session 和官方可解析格式；检索侧将同一预算传给官方 `memory_retrieve`；最终仍由项目 tokenizer 做精确预算判定，不做静默截断。对应回归测试位于 `tests/test_server_eval.py`。

在 r6 后已完成针对性测试和 Ruff；最后一次全量测试在 ACON 长跑期间重新执行并通过：426 passed（139.01 秒）。r4、r5 也已完成零调用离线复评分，保留其原始 provider 结果不变。

## 长跑结束后的固定动作

1. 同步三个 run、三个 rescore、选择回执和日志到本地。
2. 对 `report.json`、`episodes.jsonl`、`provider_ledger.jsonl`、`completed_requests.jsonl` 与 prepared manifest 做服务器/本地 SHA256 对照。
3. 核对三候选均 112/112 完成、校准通过且无未知 usage 或中断 job，再接受 `tune-select` 输出。
4. 按问题类型报告 hard/evidence/causal/current-state 结果及部署 token；不把开发集胜者写成总体方法结论。
5. 清理并复核本轮 tmux、评测进程、vLLM 进程和 GPU 0/1 基线。
