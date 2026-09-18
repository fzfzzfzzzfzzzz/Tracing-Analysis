# Qwen3.8-27B 自建 benchmark 模型规模对照启动（2026-09-18）

## 目的

在不改变题目、方法或上下文预算的前提下，把统一六方法开发矩阵的基础模型从 Qwen3-14B 替换为 Qwen3.8-27B，快速判断现有结论对模型能力是否敏感。

这是一项暴露开发集上的探索性模型规模对照，不替代独立测试集、外部 benchmark 或人工复核。

## 冻结对照

- 14B 参照运行：`qwen3_14b_unified_methods_b1024_260917_r1`
- 27B 新运行：`qwen38_27b_unified_methods_b1024_260918_r1`
- 数据：同一组 8 个受控长前缀，`split=dev`
- 方法：`full_history`、`flat_bm25_archive`、`rolling_summary`、`acon_official`、`ama_official_bm25`、`tracegraph_0_4`
- history budget：1024 tokens
- context window：32768 tokens
- 单次最大输出：2048 tokens
- thinking：关闭
- seed：20260972
- 计划：208 episodes，其中 calibration 40、main 144、diagnostic 24
- Harness 与代码快照：`/home/fangc/tracegraph-server-eval-260917-ama-scorecard-r5`

除模型身份、分词器、运行时收据、服务地址和 judge model ID 外，27B 配置与 14B 配置逐字段相同。

## 服务与资源隔离

- 原外部基准服务：GPU 0–3，`127.0.0.1:8000`，保持不变
- 本对照服务：GPU 4–7，`127.0.0.1:8001`
- 模型：`Qwen3.8-27B-rev-1d4bf0f`
- 推理运行时：SGLang 0.5.10，BF16，TP=4，最大并发 1
- 服务 tmux：`qwen38_internal_bench_260918`
- 矩阵 tmux：`qwen38_internal_matrix_260918_r1`
- 远端日志：`/data/fangc/logs/qwen38_27b_unified_methods_b1024_260918_r1.log`
- 远端准备目录：`/data/fangc/prepared_qwen38_27b_unified_methods_b1024_260918_r1`
- 远端输出目录：`/data/fangc/qwen38_27b_unified_methods_b1024_260918_r1`

服务在 2026-09-18 03:20 CST 前完成健康检查，返回模型身份与冻结配置一致。矩阵于 2026-09-18 03:20:21 CST 启动；准备阶段成功生成并验证 208-episode 计划，随后开始产生 provider ledger，GPU 4–7 均进入推理负载。

启动后前 25 个 judge 请求的累计服务延迟为 115.52 秒，中位数为 3.62 秒。结合 14B 参照运行 626 次请求、累计服务延迟约 0.81 小时，当前预计 27B 全矩阵需要约 2–3 小时；构建阶段的长输出和格式修复次数会造成波动。

## 配置与脚本

- 配置：[server_eval_qwen38_27b_unified_methods_b1024_260918_r1.json](../configs/server_eval_qwen38_27b_unified_methods_b1024_260918_r1.json)
- 服务脚本：[serve_qwen38_27b_tp4_gpu4567_8001_260918.sh](../scripts/serve_qwen38_27b_tp4_gpu4567_8001_260918.sh)
- 运行脚本：[run_qwen38_27b_unified_methods_b1024_260918_r1.sh](../scripts/run_qwen38_27b_unified_methods_b1024_260918_r1.sh)
- 27B 配置 SHA-256：`2aaf5792131a72e340567ebc7c904b8b6e58842b0d33d339a3a6a3e238ff8c27`
- 14B 参照配置 SHA-256：`480c3d495c2b0704a5227fe9dd8af33519f635cb360b29ff554f0d1eee2311b1`

## 解释边界

当前 14B 运行由 14B 自身裁判，27B 运行由 27B 自身裁判。因此直接比较 hard pass 或模型裁判分数时，模型能力变化和裁判变化尚未完全解耦。本轮适合发现以下信号：

1. AMA、Rolling Summary 的构建成功率是否因 27B 提升而改善；
2. 各方法的有效运行率、格式失败率和输出截断率是否变化；
3. TraceGraph、Full History 与 BM25 的相对排序是否稳定；
4. 27B 是否只抬高所有方法，还是改变方法间差距。

若结果足以影响论文结论，正式模型规模比较应固定同一个外部裁判，或对两组答案做盲人工复核，再报告配对差异和置信区间。
