# Qwen3-14B SWE-bench Lite 真实轨迹 canary（260915）

## 结论

Docker、镜像、任务、隐藏测试、checkpoint 和模型协议已经打通，但最终协议下的 Qwen3-14B
在 3 个真实软件任务上为 0/3。三个任务均耗尽 12 次模型调用；模型不能稳定完成“定位源码—
编辑—运行测试—纠错”的闭环。随后只在已暴露短题 `pytest-5221` 上完成了 thinking、未压缩
历史和 40 步上限的能力归因复验；模型在第 21 次调用主动提交，但有效源码 diff 为空、没有运行
测试，隐藏测试仍为 2/2 失败。因此现在仍不应继续执行剩余 5 题，也不能用这些轨迹比较
TraceGraph 与其他记忆方法。

这不是“题目或评测器全坏了”：8 个容器均已验证原始提交在 `FAIL_TO_PASS` 上失败，应用数据集
标准补丁后通过。模型的同配置校准也通过。最终失败发生在代码代理能力层，而非输出解析、Docker、
checkpoint 或隐藏测试层。

本结果是开发期管线 canary，不是官方完整 SWE-bench 分数，也不是独立验证。

## 冻结输入

- 数据：ModelScope 镜像的 `princeton-nlp/SWE-bench_Lite`，revision
  `1056b1963e8af34a3010add7c73a413a1ffe317b`。
- parquet SHA-256：`7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4`，
  共 300 条。
- canary：在 `pytest-dev/pytest` 的 17 条任务中按固定 seed 哈希选 8 条。未来正式验证应排除
  整个 `pytest-dev/pytest` 仓库。
- 最终任务文件：`data/real_canary/swe_bench_lite_pytest_260915_r5/tasks.jsonl`，SHA-256
  `b1933691da9e02d909d973c342d03ad27751c860b9d0b9ba498088aa9ac7b836`。
- 最终 mini plan：`outputs/server_eval/prepared_qwen3_14b_real_canary_mini_260915_r7`，blockers 为空。
- 镜像：Epoch Research GHCR 镜像，8 条均按 manifest digest 固定；运行时断网、无宿主挂载、
  drop all capabilities、no-new-privileges。
- 评测范围：应用隐藏 `test_patch` 后只跑 `FAIL_TO_PASS`。这足以验证采集管线，不等同官方
  SWE-bench 的完整回归评分。

## 模型门禁

- 模型：`Qwen3-14B-rev-40c0698`，weights revision
  `40c069824f4251a91eefaf281ebe4c544efd3e18`，BF16，vLLM 0.8.5，TP=2，GPU 0–1。
- 配置 SHA-256（稳定摘要）：`836743c611c6c85509d134cea9edbb773cd7de1bbac4c89e1d0081a7d655add8`。
- 校准：裁判 40/40，关键假阳性 0；首次/最终格式 40/40；Full History 24/24、Oracle 16/16。
- 校准请求：160/160 成功，无自动重试。

## 管线修复

1. Docker 29.6 会把 `docker commit --pause=true` 的弃用警告写入 stdout，导致合法镜像 ID 被
   解析器拒绝。已移除弃用参数；commit 默认仍暂停容器。
2. 历史上下文原先作为裸 JSON 用户消息发送。已增加“按时间顺序的既往执行记录、从最新观测
   继续、不得重复”的明确封装。
3. Markdown bash 围栏在 Qwen3-14B 上产生连续格式失败。已改为严格
   `{thought, command}` JSON Schema 传输，由客户端转换为单个 bash 动作。
4. 相关 mini action、checkpoint 和 restore 回归测试通过。

前两次对 `pytest-5413` 的运行用于发现和修复上述基础设施/协议问题，不作为最终能力数据。

## 最终协议结果

| 任务 | 描述长度层次 | 调用 | 退出 | 结果 | 主要失败模式 |
|---|---|---:|---|---:|---|
| `pytest-5413` | 中等 | 12 | `LimitsExceeded` | 失败 | 把示例当待改文件，反复使用错误的 macOS `sed`，未定位 `ExceptionInfo.__str__` |
| `pytest-7220` | 中等 | 12 | `LimitsExceeded` | 失败 | 反复向自造 `test_path_error.py` 追加代码，未检查 pytest 报告路径源码 |
| `pytest-5221` | 极短（239 字符） | 12 | `LimitsExceeded` | 失败 | 搜索方向部分正确，但覆盖 `helpconfig.py` 并写入语法无效的选项定义 |

三题的隐藏评测返回码均为 1。短题同样失败，使“只是长题题面困难”的解释明显变弱。结构化
动作在最终运行中有效，失败可以归入当前 Qwen3-14B 非 thinking 代码代理的定位、编辑和纠错
能力；但样本只有 3 条，不能估计总体成功率。

## `pytest-5221` 能力归因复验

复验只使用已暴露的 `pytest-5221`，剩余 5 题模型请求数保持为 0。最终有效配置启用了真实
Qwen3 thinking，使用权重 README 推荐的 `temperature=0.6/top_p=0.95/top_k=20/min_p=0`，
将步数上限设为 40，并使用未压缩 Full History。最后一次请求保留 40/40 历史事件、18,810
history token，总 prompt 19,646 token，未触及 32,768 context window。

模型 21 次调用后主动提交；21/21 provider 请求有效，20 个工具调用均有完整非空 thinking。
但模型没有形成有效源码 diff，没有运行测试或检查 diff，隐藏测试仍为 2/2 失败。因此 0 分不能
归因于题目无效、thinking 关闭、历史不足或 12 步截断；直接瓶颈是当前模型与代理脚手架下的
定位、有效编辑和验证能力。完整证据见
`docs/Qwen3-14B_pytest5221能力归因260915.md`。

## 下一步

1. 停止当前模型的剩余 5 题，保持 `pytest-7490/5227/7168/5495/5103` 未暴露。
2. 不修改 TraceGraph 检索算法；当前还没有产生可用于方法比较的成功/恢复型轨迹。
3. thinking/长历史/40 步复验仍失败后，使用代码专用且更强的模型已有直接依据。新模型先在
   已暴露的 `pytest-5221` 上做 smoke test；通过“有非空源码 diff、运行测试、隐藏测试通过”
   三项后，再只释放 1 条未暴露 canary。
4. 若不换模型，下一轮必须标为代理脚手架干预（重复命令阻断、空 diff 禁止提交、测试前禁止
   提交），不能把收益解释为 TraceGraph 记忆方法收益。
5. canary 通过后导出 mini 轨迹并做外部失败链/生命周期标注；正式 100 题集仍须另取真实新任务，
   且排除本 canary 的整个 pytest 仓库。
