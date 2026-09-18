# Qwen3-14B failure episode AI 开发 canary 结果（260917）

## 结论

本轮已经完成从 AI 双审开发金标、答案契约修复、零调用敏感性复算，到 thinking
能力归因的闭环。结果只适用于开发诊断，不能写成正式 benchmark 成绩：标注来自同一
底层 GLM 的两个 pass 和 AI 裁决，29 条接受案例全部是 R0，实际模型实验又只用了其中
3 条已暴露、能装入 32K 窗口的短 SWE 轨迹。

目前最稳健的结论有三条：

1. 原任务级评分契约确实存在误杀。多阶段修复不能由单一
   `replacement_action/replacement_arguments` 表示；稳定错误签名也不应要求连外层报错
   前缀和末尾句号都逐字相等。
2. 修复后 Oracle 在相同 3 条完整失败链上稳定达到 2/3，证明题目不是普遍不可答、证据
   也不是普遍不足；Bokeh 的剩余失败是引用精度问题。
3. Full History 的瓶颈不是“完全看不懂”。thinking-off 和 thinking-on 的语义事实均为
   3/3；主要困难是把长上下文中的概念性失败绑定回锚点记录的精确工具调用，以及同时
   给出必要且不过量的引用。thinking 改善了动作绑定，但恶化了引用稳定性，整体 hard
   pass 没有增加。

因此，现在不能写“Qwen3-14B 没能力”，也不能写“只要修评分就能解决”。更准确的表述
是：**在完整证据可见时，14B 模型能复述任务级因果链，但精确 provenance 绑定与引用
选择仍不稳定；增加 thinking 产生精度—引用—成本之间的权衡。**

## 开发金标

AI 双审与开发裁决最终得到：

- 候选 100 条；接受 29，拒绝 71；
- 接受案例：SWE 18、AMA 11，全部 R0；
- 开发 review：
  `审阅/failure_episode_v1_260916_r1/development_gold_ai_r1/reviews.adjudicated.development_ai.jsonl`；
- review SHA-256：
  `51ff37f20b4c0adf8a731a4968f12c0f076569de71a25d9c5ff50fda97183cc0`；
- 数据集：`outputs/compression_audit/failure_episode_ai_dev_260917_r2`；
- dataset manifest SHA-256：
  `ac4229bf73a526e249a6a75ee4f4371b1929b3f13466d5a8a80470a88b782542`；
- file manifest SHA-256：
  `a0023455a2860a2f66e7dadcdfe351e4ad781f1d233608c0ce7845c9155defb0`。

这不是双人独立人工标注，也没有通过 formal v1 门禁。

## r1/r2 主 canary

第一次 live canary 暴露了 server answer schema 漏掉 `repair_sequence` 的协议缺陷。该轮保留
为负证据，没有解释成模型失败。修复后答案契约升级到 r9，并完成 94/94 episode、258 次
provider 请求：

- 运行：`outputs/server_eval/qwen3_14b_failure_episode_ai_canary_260917_r2`；
- report SHA-256：
  `adf3069ab0b801f9ff97885935e0737f7e9b3a6cfe0c672685bf8a814afc4491`；
- episodes SHA-256：
  `a12d7ebf9d0c59f1cd1910c4a544378283896b6782497b76ed9f8a338eb997fa`；
- provider ledger SHA-256：
  `c1ec1c3d703083920dfdaed982e8e66ad368a4c925422d23cfb5ce83345609fa`；
- 校准：judge 40/40；cell format 40/40；Full 24/24；Oracle 16/16。

主矩阵每种方法只有 9 个 episode（三条前缀 × 三种查询）：

| 方法 | 上下文支持 | 协议通过 | 引用通过 | 语义通过 | hard pass |
|---|---:|---:|---:|---:|---:|
| Full History | 9/9 | 9/9 | 3/9 | 9/9 | 2/9 |
| recent masking | 7/9 | 9/9 | 3/9 | 9/9 | 1/9 |
| BM25 | 5/9 | 8/9 | 3/9 | 8/9 | 2/9 |
| rolling summary | 0/9 | 6/9 | 0/9 | 5/9 | 0/9 |
| TraceGraph 0.4 | 8/9 | 4/9 | 1/9 | 4/9 | 0/9 |

该表不能用作方法排名。样本量只有 3 条前缀，而且 TraceGraph 的 5 条未发送不是简单的
token 超限，而是完整因果闭包无法在 provider hard limit 内安全呈现，因此实现按策略拒绝
发送局部不完整链。

## r10 契约

针对任务级 episode，r10 做了两项受控修改：

- `audit_recovery`、`audit_chain`、`interactive_reacquisition` 不再把旧的单一
  `replacement_action/replacement_arguments` 作为 strict 值；多步修复继续由
  `repair_sequence` 语义事实约束；
- `error_signature` 要求答案包含金标稳定签名，允许外层前缀、空白和末尾句号差异；只
  允许“答案包含金标”，不允许反向短串匹配。

旧的单步/受控题仍保留严格 replacement 字段。版本为：

- answer contract：`compression_audit_answer_contract_v02_r10`；
- rubric：`compression_audit_rubric_v02_5`；
- scoring：`v0.2-development-judge-contract-r7`。

相关本地回归测试 64 项通过；服务器相同代码 63 passed / 1 optional tokenizer skipped。

## 零调用敏感性复算

对 r2 保存的 258 次调用执行零调用复算：

- 输出：
  `outputs/server_eval/qwen3_14b_failure_episode_ai_canary_260917_r2_contract_sensitivity_r1`；
- 新 provider 请求：0；
- report SHA-256：
  `9c0b4b27b68ce93aa08112936ab4fa8726aace06be2596adbda69b4d54e24161`；
- changed episodes SHA-256：
  `49c41df45fba55524ad41f18feeA0d5b968ff852e435cd0a79296e3a54b6b388`。

结果：

- 主矩阵 hard/audit 从 5/45 变为 6/45；
- Oracle audit-chain 从 0/3 变为 2/3；
- 主矩阵 audit-chain 仍为 0/15；
- audit-chain 的 error-signature 通过从 3/24 变为 11/24；
- 旧 replacement 两字段原本为 0/24，删除后不再造成系统性误杀。

这份结果是 post-hoc sensitivity，不是 fresh r10 成绩，因为旧答案没有看到 r10 schema。

## fresh r10：thinking off

为了验证后验复算，fresh r10 只运行 Full History 和 Oracle：

- 运行：
  `outputs/server_eval/qwen3_14b_failure_episode_contract_r10_capability_260917_r1`；
- 46/46 episode，172 次请求；
- 校准：judge 40/40；format 40/40；Full 24/24；Oracle 16/16；
- Full History：上下文 3/3、引用 2/3、语义 3/3、hard 0/3；
- Oracle：上下文 3/3、引用 2/3、语义 3/3、hard 2/3；
- report SHA-256：
  `148f962c75afc258fc3478d8e2e7052fb1457633cf03d1586fa7d68d315b8845`；
- episodes SHA-256：
  `e2e03d69b11355734f4394f43a06b8c53a104a5719892d941cb1a17e0768b719`；
- provider ledger SHA-256：
  `821051fe1bff68bd4765aadeb8aa93d8a375701c0bdb34ed8c5ee431bb45bfe9`；
- prepared manifest SHA-256：
  `5454ed2418a01606c31b73aefda5f18a5dc26c5ebb3f8d87f3d244b638924c00`。

Full History 三题都表达了全部语义事实，但精确 `failed_action/failed_arguments` 为 0/3。
模型倾向写概念性内部操作（如 `validate`、`download_and_check_clang_format`）或修复动作，
而金标锚点要求复制公开记录中的外层工具调用 `execute_bash`。

## fresh r10：thinking on

保持模型权重、3 条前缀、32K 上下文、r10、Full/Oracle 和 hidden scoring 不变，只将回答
开启 thinking，并按 Qwen3 推荐采样使用原生工具提交；judge 不开启 thinking：

- 运行：
  `outputs/server_eval/qwen3_14b_failure_episode_contract_r10_thinking_capability_260917_r1`；
- 46/46 episode，174 次请求；
- 校准门禁通过：judge 38/40；format first/final 39/40、40/40；Full 22/24；
  Oracle 15/16；
- Full History：上下文 3/3、引用 1/3、语义 3/3、hard 1/3；
- Oracle：上下文 3/3、引用 2/3、语义 3/3、hard 1/3；
- 6 条目标题回答 token：3,978 → 8,093，约 2.03 倍；
- 6 条目标题回答延迟：112.36 秒 → 205.79 秒，约 1.83 倍；
- Full History 的精确初始动作从 0/3 提升到 2/3；
- Full + Oracle hard pass 合计仍为 2/6；
- report SHA-256：
  `a2b6006941b29ad9e5a46ca4e96cf7d2ffb1e5d0b1d9ea938f6261cbcf5a868a`；
- episodes SHA-256：
  `131f37ade6b9f7228e955cdc1bdd42dd2a3307ba94e11284a8d2a337034b7783`；
- provider ledger SHA-256：
  `06398449361a0a79cf73aa4ff18284a6d61104b1cd0482018e5f83fd4b22b9cd`；
- prepared manifest SHA-256：
  `668683c9b96d35b584d28a33c992a1eec2e6f454d60c17d6e2c625523d125b7a`。

thinking 不是整体修复：它改善了精确动作绑定，但 Bokeh 漏掉直接错误记录的引用，pandas
引用了过多历史记录；Oracle 的 MONAI 又把外层 `execute_bash` 写成内部 `check_hash`。

## 论文现在能写什么

可写成开发发现：

- 多阶段失败链需要 first-class episode gold；单一 replacement 字段会产生系统性构念
  错配；
- Oracle 与 Full History 的差异表明，完整上下文“包含答案”不等于模型能稳定绑定精确
  事件；
- thinking 改善一部分 provenance extraction，却增加约 2 倍输出与 1.8 倍延迟，并没有
  提高 6 条目标题总体 hard pass；
- TraceGraph 的安全闭包拒发与普通检索遗漏应分开报告。

暂时不能写：

- 任一方法优于另一方法；
- Qwen3-14B 普遍不具备失败链理解能力；
- 29 条 AI 金标代表正式 v1；
- 当前结果能外推到 R1–R3 交互重获或未暴露 test。

## 下一门禁

现在不应继续扩大模型调用。下一步是对最终会影响结论的最小集合做独立人工复核：

1. 复核这 3 条前缀的锚点层级：strict `failed_action` 应是公开工具调用
   `execute_bash`，还是允许概念性内部动作；
2. 复核 Bokeh 的有效额外引用，特别是 E020（已在 optional support 中，却未列入
   audit-chain relevant）以及终局总结 E023；
3. 冻结复核后的 3 条开发 rubric，再做一次零调用复算；
4. 若“精确 provenance 绑定不稳定、thinking 只改变错误分布”的结论仍成立，再将同一
   标注规则用于独立人工 A/B 和未暴露 validation；
5. validation Oracle 达到预注册门槛后，才扩展 Full History、recent masking、BM25、
   rolling summary、TraceGraph 主比较，并接已有 τ³ 做外部在线验证。

已生成只含上述 3 条公开开发轨迹的盲化人工 A/B 包；包内没有模型答案和 AI 金标：

- `审阅/failure_episode_r10_targeted_human_260917_r1/failure_episode_r10_reviewer_a_260917.zip`，
  SHA-256 `b255bdb08b106c5f22a4e6f2ea8f5680c08f372170fa463cdeafb783b59a839e`；
- `审阅/failure_episode_r10_targeted_human_260917_r1/failure_episode_r10_reviewer_b_260917.zip`，
  SHA-256 `95542d5f539923c2885434862bc61ae115e1c8008e69d5a1609b0b7362ea3f63`。

两包的 `cases.jsonl` 与空白模板逐字一致；A/B 应由不同的人独立填写。

151 上两次运行结束后均已退出 tmux；GPU 0、1 回到约 10 MiB。本轮没有使用 GPU 2–7。

## 定向 AI 复核与零调用金标迁移（260917）

为先验证结论是否值得投入人工，定向 A/B 包由同一底层 GLM-5.2 的两个 pass 填写，因而
仍不是独立人工复核。程序化验收和确定性最小核心裁决得到：

- 3/3 锚点、严格动作与参数、错误结果、稳定签名、相关/禁止证据划分完全一致；
- required core 逐项完全一致为 0/3，但平均 F1 为 0.9091；三个分歧都只涉及最终验证
  `execute_bash` 是否必须引用；
- 裁决采用较小的充分核心，并把最终验证动作保留为有效 optional support；
- adjudication SHA-256：
  `085fbad5dab77510762159bed39613c9fe0c5cbcc03263339b5507dd300754ad`。

复核确认三题的 task-level strict 动作均应是公开记录中的 `execute_bash`，不应以内部函数
或后续编辑动作替代；同时扩大了有效支持证据范围，并把 MONAI 的稳定错误签名从具体
`check_hash failed <digest>` 改为失败结果原文中的 `sha1 check of downloaded file failed`。

没有改写父数据集。新建开发金标子分支：

- 数据集：`outputs/compression_audit/failure_episode_ai_dev_targeted_review_260917_r3`；
- 父 manifest SHA-256：
  `ac4229bf73a526e249a6a75ee4f4371b1929b3f13466d5a8a80470a88b782542`；
- 新 manifest SHA-256：
  `42c8e6482d96bbbf391f1ce924aa58119f8dc01f72a88b1987de9d9c64030a69`；
- 新 file manifest SHA-256：
  `455d8ad34a7b53c4f486de68b34da2361a268f9e527bac6defab05a0f0dbd67c`；
- 仅 3 个 gold hash 改变；公共 prefix 和全部 query hash 均未改变；新模型请求为 0。

`score-gold-migration` 同时修正了一个复算边界：`strict_values`、证据集合和因果约束是本地
确定性规则，其改变不应使缓存语义 judge 失效；只有 `necessary_facts`、
`contradictory_facts` 或 `expected_scope` 改变才属于语义 judge 契约改变。回归测试 44 项
通过。

对 fresh r10 thinking-off/on 保存结果的零调用复算如下。Full History 没有 prompt 或上下文
mismatch，可直接解释；Oracle 仍使用迁移前金标构造的上下文，只能视为反事实规则复算。

| 设置 | 条件 | 上下文支持 | 引用通过 | 语义通过 | hard pass | 解释 |
|---|---|---:|---:|---:|---:|---|
| thinking off | Full History | 3/3 | 2/3 | 3/3 | 0/3 | 直接 |
| thinking on | Full History | 3/3 | 2/3 | 3/3 | 1/3 | 直接 |
| thinking off | Oracle | 3/3 | 3/3 | 3/3 | 2/3 | 反事实 |
| thinking on | Oracle | 3/3 | 3/3 | 3/3 | 2/3 | 反事实 |

Full History 的净 hard 分未改变，但具体通过项改变了：thinking-on 中，pandas 因相关证据
边界修正从失败转为通过；MONAI 则因稳定错误签名修正从通过转为失败。Bokeh 仍把初始
外层 `execute_bash` 写成内部 `validate`，thinking-on 还漏引直接失败结果；thinking-off
的 pandas 仍过量引用 E034–E037。换言之，旧金标确有证据边界偏窄，但修正后仍保留了
核心发现：语义因果链 3/3，不等于能稳定输出锚点级 provenance 和最小充分引用。

这也说明 1/3 的小样本净分对标注细节很敏感，论文不应只报聚合 hard pass；至少要同时
报告逐题严格字段、引用失败类型和金标迁移敏感性。当前最值得进入人工门禁的结论是：
thinking 使精确动作命中从 0/3 提升到 2/3，但代价约为 2 倍输出、1.8 倍延迟，且引用
稳定性不足使总 hard 只保持 1/3。

复算产物：

- thinking off：
  `outputs/server_eval/qwen3_14b_failure_episode_contract_r10_capability_260917_r1_targeted_review_rescore_r1`，
  report SHA-256 `0cc569db4b3e4a2e5872d9f9b8b3fbdb318b8835de42154d596ce275aad90dd5`；
- thinking on：
  `outputs/server_eval/qwen3_14b_failure_episode_contract_r10_thinking_capability_260917_r1_targeted_review_rescore_r1`，
  report SHA-256 `ee00b13b92bdcdd86e2886edb644adb8695621eb32c56332f683e7288f6ff508`；
- 两次复算的新 provider 请求均为 0；每次 43/46 条目可直接复算，3 条 Oracle 均明确
  标记为上下文 mismatch；语义 judge 失效为 0。

下一步不再扩大这 3 条上的模型调用。应先由两位不同的人复核这 3 条的锚点、错误签名和
证据边界，并由第三人只裁决分歧；若逐题结论仍成立，再冻结 validation 协议，在未暴露
样本上运行 Oracle 门禁。只有 validation 门禁通过，才进入五种记忆方法的主比较。

## 原 validation 子集的 AI 开发 Oracle 门禁（260917）

数据构建时预分配为 validation、且此前未发送给 Qwen 的 4 条轨迹已在当前 AI 金标分支中
统一标成 dev；因此本节只能称为“原 validation 子集上的 AI 开发伪验证”，不能称为独立
validation。4 条均为 R0，冻结门槛为：hard pass 至少 3/4、必要证据 4/4、协议 4/4、
安全 4/4；失败则不启动五方法主矩阵。

最初的 `oracle` 实现把金标链与 `budget // 3` 的近期历史混合。4096 下得到 hard 3/4，
但长题的 6040-token artifact 未发送，协议只有 3/4；提高到 8192 后协议为 4/4，hard
反而降至 2/4。原因不是更多上下文无帮助这一研究结论，而是 Oracle 的干扰项随预算改变：
一个 4096 下通过的题在 8192 下被新增近期记录诱导到后续修复动作。因此该条件不是稳定的
能力上限，两次结果均保留但不用于放行主矩阵。

为修复构造而不覆盖旧语义，新增 `oracle_evidence_only`：只发送冻结的
`ordered_event_ids` 及确定性的 call/result pair closure，不加入近期历史。回归测试验证，
一旦上下文可发送，4096 与 8192 的记录集合和 token 数不因预算改变。`tests/test_server_eval.py`
共 33 项通过。

evidence-only 的 4096 轮完成 44/44 episode、166 次请求，judge 38/40；前三题 artifact
为 2088、2440、1232 token，第四题精确计数为 4743，故仍未发送。结果 hard 2/4、协议
3/4。其产物为：

- prepared：`outputs/server_eval/prepared_failure_episode_ai_validation_oracle_evidence_only_260917_r1`，
  manifest SHA-256 `1ae6ce987c56d1c28ba456f9838eb35b20e69e8d599340d82d47dbf492c55780`；
- run：`outputs/server_eval/qwen3_14b_failure_episode_ai_validation_oracle_evidence_only_260917_r1`；
- report SHA-256 `9165e474bea7ee864bbea965ac5553d0da61cde28355d2a058b5011228833316`；
- episodes SHA-256 `727a13fddbe1e94c45fcdf98d589abb3a25b139bc589af962c8c5eb1620e6e5b`。

由于 evidence-only 记录对预算不变，最终做了一次 8192 的发送可行性补跑。该轮完成
44/44 episode、168 次请求，judge 40/40、0 个 critical false positive，4 条均完成模型
回答。冻结门禁最终仍失败：

| 指标 | 结果 |
|---|---:|
| 必要证据存在 | 4/4 |
| 引用通过 | 4/4 |
| 语义 judge 通过 | 4/4 |
| 安全通过 | 4/4 |
| 初始动作精确 | 4/4 |
| 初始参数精确 | 4/4 |
| 错误签名精确 | 2/4 |
| hard pass | 2/4 |

两个失败均只剩错误签名粒度：

1. `4b66e74f8d1830e3` 把金标
   `ModuleNotFoundError: No module named 'deepdiff'` 写成
   `No module named 'deepdiff'`；异常类型仍在后续自然语言说明中出现，但不在严格标签值内。
2. `6f4e03fe44805b6e` 把金标
   `ImportError: cannot import name 'count' from 'dask.array.routines' (/testbed/dask/array/routines.py)`
   写成去掉环境路径后缀的同一 ImportError。

两题的全部必要证据、最小引用、因果顺序、语义、初始动作和参数均通过。因此当前不能写成
“Qwen3-14B 看不懂失败链”，更准确的开发结论是：模型在固定充分证据下可恢复 4/4 的
因果链与 provenance，但对严格错误签名的逐字边界只能达到 2/4。究竟应计为能力失败还是
金标/评分契约过严，需要在看不到候选来源和总成绩的情况下复核“稳定签名”定义。

最终补跑产物：

- prepared：
  `outputs/server_eval/prepared_failure_episode_ai_validation_oracle_evidence_only_b8192_260917_r1`，
  manifest SHA-256 `74ded755f6d73276e7a3a6d99e2c0bbf2dd873af239bef19343a73dfb7a75db8`；
- run：
  `outputs/server_eval/qwen3_14b_failure_episode_ai_validation_oracle_evidence_only_b8192_260917_r1`；
- report SHA-256 `3b3c8439756c5dc1dad4175cf59f59696c89569582f3448464b7e0bcf3eb31d5`；
- episodes SHA-256 `9f4a10d5aa149fbdc073d40a8f69408877c7373f29f42cadb87b4d876ab973a7`；
- provider ledger SHA-256
  `d008aacc23143f6345c4466944b9661fbf5fe18767a6f10f94e1156c1d4cb33b`。

按冻结规则，五方法主矩阵没有启动。已生成只含上述 2 个签名边界、A/B 来源盲化的 GLM
开发复核包：`审阅/failure_episode_signature_boundary_glm_260917_r1`，manifest SHA-256
`ba93ebce05f02b44b4e0beeb9933c0b00e8768ca833bd95d7945c1e763e8ee9f`。只应把
`cases.jsonl` 和 `reviews.template.jsonl` 交给 GLM；`blind_mapping.private.jsonl` 不得提供。
收到 `reviews.completed.jsonl` 后先做零调用重算和敏感性分析，再决定是修正稳定签名金标、
把 exact signature 降为次要指标，还是维持 hard 门禁失败。此前不得启动主矩阵。

为量化该选择的影响，已对保存答案做零调用、事后敏感性分析：维持当前“答案必须包含完整
金标签名”的单向规则为 2/4；只从金标去掉绝对 Python 文件路径后仍使用单向规则为 3/4；
改为双向规范化包含则为 4/4。后两者都会越过 3/4 门槛，但它们是在看到答案后定义的，
不能覆盖冻结结果。敏感性报告位于
`outputs/server_eval/qwen3_14b_failure_episode_ai_validation_oracle_evidence_only_b8192_260917_r1_signature_sensitivity_r1/report.json`，
SHA-256 `2ee278979bd39af79ebe9b9e1c067d8e33612fc0433e2176903dd48449b803f1`。只有盲审先独立选择
构念后，才能据此建立新开发金标分支并复算；不能按哪个分数更高来选规则。

151 上两轮 evidence-only 运行结束后均已清理进程；GPU 0、1 回到约 10 MiB，GPU 2–7
从未使用。
