# compression_audit_v1：失败历史压缩 Benchmark

## 它测量什么

`compression_audit_v1` 测量 agent 压缩历史以后，是否仍能审计一条完整的失败链：

`失败动作及参数 → 错误签名 → 诊断证据 → 切换决定 → 替代动作及参数 → 成功证据`

如果压缩遗漏了其中一段，第二赛道继续测量 agent 为重新获得事实而增加的模型调用、工具调用、模型输入/输出量、工具观察量、延迟和费用。它不把正确性、压缩率和成本压成一个总分，而报告完整审计通过率、连续审计分、压缩率、因果遗漏率、重获成本和 Pareto 前沿。

本项目不声称首次研究压缩导致的重复检索。相关工作已经分别研究压缩后的安全性、轨迹问答和交互成本。这里更窄的贡献是：在同一组配对反事实条件中，同时测量“失败链能否审计”和“遗漏后的实际重获成本”。

## 研究边界：当前 Track A 与后续 Track B

截至 2026-09-17，`compression_audit_v1` 的正式范围是 **Track A：Failure Memory Retention**，即失败记忆的保留与恢复：

- **A1 Audit Retention**：压缩后能否重建失败动作、原因、修复过程和成功证据，并把结论绑定到真实事件；
- **A2 Interactive Reacquisition**：关键证据被移出当前上下文后，能否在固定权限和预算内安全地重新获得，以及额外调用、token、延迟和费用是多少。

Track A 测量的是未来自我改进所需的记忆基础。它可以支持“某种压缩方法保留或恢复失败经验的能力更强”这一类结论，但不能单独支持“模型再次遇到同类问题时会避免旧错误、提高任务成功率或已经实现自我进化”。当前 Audit-QA 和 Interactive-Reacquisition 都是对既有失败的回顾、审计或取证，不是一次新的同类任务决策。

**Track B：Failure Experience Reuse** 作为明确的后续方向保留，当前状态为 `planned / not started`，不属于 `compression_audit_v1` 的 build、run、score 或已有结果。它要测量：Agent 经历一次“失败—诊断—修复—成功”后，经过多轮无关推理和上下文压缩，再次面对相同潜在失败机制时，能否在没有“请回忆以前失败”这类显式提示的情况下主动利用经验并改善行为。

Track B 的最小实验闭环应包括：

1. **经验阶段**：记录一次真实的失败、诊断、替代动作和成功结果；
2. **间隔阶段**：插入冻结数量的无关任务或工具轮次，并执行待比较的压缩方法；
3. **再遭遇阶段**：给出新的可执行任务，分别覆盖原题重现、表面变化但失败机制相同、表面相似但机制不同三类条件；
4. **配对对照**：至少比较 Full History、候选压缩、删除失败经验、同预算 Oracle 经验和等长无关经验；
5. **行为评分**：报告首个动作重复旧失败的比例、重复失败率、前 K 个动作内解决率、最终任务成功率、额外工具调用/token/延迟、危险重复率和负迁移率；事件级审计作为证明经验确被使用的机制指标，而不是唯一主结果。

仓库中已有的短分支 R3 设想与 Track B 接近，但只观察失败后的短期动作，而且已因前置门禁停止；在完成新的跨轮次再遭遇协议和有效运行前，不把它记为 Track B 已开始或已经得到结果。若模型权重不更新，Track B 应表述为“外部记忆支持的运行时经验复用/自我改进”，而不是参数层面的 RSI。

## 后续 Harness 构念效度与方法中立性验证

截至 2026-09-17，本节状态为 `planned / not started`。它不阻止当前冻结开发配置继续完成 Track A 工程实验，但在对外声称自建 Harness 比 AMA-Bench 或 ACON 的原生评测协议更适合失败记忆、以及声称 TraceGraph 的优势来自算法而非适配条件之前，必须完成。本项目不把 AMA/ACON Harness 表述为一般意义上的“更差”；要验证的是它们对“查询盲的失败经验保留、恢复、溯源和安全重获”这一更窄构念的覆盖是否弱于本 Harness。

正式验证至少包括：

1. **统一公共信息**：从同一个 canonical public event stream 为所有方法生成输入；内容、时序、调用—结果标识和公开元数据相同，隐藏 gold、`causal_role` 和未来问题。TraceGraph 不得获得其他方法不可获得的额外事实或结构字段。
2. **双输入条件**：每种方法同时运行 canonical representation 和作者原生 native representation。若 TraceGraph 只在结构化 native 条件领先而在 canonical 条件优势消失，不把差异归因于记忆算法本身。
3. **原生 Harness 交叉检查**：把 TraceGraph 包装进 AMA 的 `memory_construction`/`memory_retrieve` 接口；把固定官方版本的 AMA、ACON 接入本 Harness；ACON 另在其原生 AppWorld/OfficeBench 管线完成 sanity replication。不同数据集不直接比较绝对分，只报告相对 Full History 的配对下降、方法排名稳定性和适配有效性。
4. **资源匹配**：分别冻结并报告最终发送 token、驻留 memory token、query-time archive 权限与读取量、构建/检索/回答总 token、工具调用、延迟和费用。带档案检索的方法不能只用较短的最终上下文宣称更高效率。
5. **结构对照与消融**：保留 BM25 pair-window、BM25 failure-episode unit、TraceGraph 去因果边、去 pair closure、打乱边或等数量随机边，以及 Full History、Oracle、失败链删除和等长无关记忆。若共享失败链分组但没有图推理的结构基线达到同样结果，不把收益归因于 TraceGraph 图机制。
6. **Harness 自身效度**：用与方法无关的成对记忆扰动分别删除事实、因果环节、来源指针或安全约束，比较各评测协议的缺陷检测率和误报率；再与盲标人工“失败经验是否足够复用”的判断比较一致性。后续以一个独立小型再遭遇验证集检验 Track A 分数能否预测重复旧失败和行为改善。
7. **冻结独立测试**：评分规则、适配器和选择门槛只能在开发集上确定。当前因 AMA/ACON canary 暴露而产生的评分与适配修复只属于工程开发；冻结后必须在未参与提示、适配或算法修改的真实 test prefixes 上一次性验证，不能用当前暴露 canary 证明方法无偏或论文主结论。

该验证完成前，当前实验可用于确认运行链、预算边界、评分输出和生成后续预注册假设；不得将“TraceGraph 在自建 Harness 上领先”本身作为 Harness 有效或方法中立的证据。

已完成的 2026-08-31 模型试验、验收失败及请求记录修复见 [v0.1 运行报告](失败历史压缩Benchmark_v0.1运行报告.md)。本轮没有通过发布门禁。

## 发布层级

- `v0.1-diagnostic`：包含现有 Phase 6 兼容集、新受控集、运行器、评分器以及受 100 元硬上限保护的小规模模型基线。它只能支持机制性描述。
- `v1.0`：还必须导入 100 条双人标注并仲裁的真实轨迹。门禁不通过时，程序保持 `v1_ready=false`，不会用低质量样本补足数量。

现有 72 个结果来自 24 个源前缀的三个 fork，并不是 72 个独立样本；其中也只有一部分是字面意义上的“方法 A 失败后改用 B 成功”。构建器逐字节复制原文件、核对固定 SHA-256、保留原 ID 和结果，并将它们标为不参与排名的 `legacy_diagnostic`。原目录 `outputs/phase6/e1_controlled_v1` 不会被覆盖。

## 数据组成

受控集包含 240 个独立前缀：10 类失败、3 个任务领域、4 个恢复等级和 2 个上下文长度的笛卡尔积。每个前缀有四个 Audit-QA、一个 Interactive-Reacquisition 和一个当前状态 distractor，共 1,440 个 episode。

恢复等级为：

- `R0`：事实只在历史里，工具无法重获。
- `R1`：一次只读调用可以重获。
- `R2`：需要环境检查、沙箱复现和日志调查等多步重获。
- `R3`：原动作不可安全重复；harness 只允许模拟，并记录、阻断重复尝试。

受控 split 按完整失败类型隔离为 48/48/144 个 dev/validation/test 前缀，同一失败模板不会跨 split。正式真实集按仓库和任务分组为 20/20/60，任何仓库不得跨 split；60 条 SWE-Gym/OpenHands 轨迹进入两个赛道，40 条 AMA-Bench 轨迹只进入 Audit-QA。

## 两个赛道和四个反事实条件

Audit-QA 禁用工具，分别询问失败动作、失败原因、替代方案和完整因果顺序。问题在压缩之后才公开，并且不包含错误签名、动作名或答案线索。每个问题从同一不可变 memory state 独立开始。

Interactive-Reacquisition 只开放记录中声明的工具。harness 记录历史读取、调查、沙箱重跑、危险重复尝试以及所有真实用量。

交互题固定比较：

1. 完整历史；
2. 候选压缩；
3. 在相同 token 预算内换入缺失失败链的 oracle；
4. 换入等长无关记录的大小匹配对照。

只有“完整历史答对、候选压缩答错”才是 `compression_harm`。只有 oracle 恢复而无关内容不能恢复时，才是 `causal_omission`。完整历史也答错的组合标为 `invalid_upper_bound`，保留报告但不进入伤害率分母。

## 多分数评分

Track A 从 `failure-memory-scorecard-v1` 起同时报告以下正式分项：

- `fact_retention_score`：错误签名、失败动作与参数、替代动作与参数等严格事实的确定性恢复率；
- `semantic_causal_score`：冻结且通过校准门禁的 LLM judge 对失败原因、诊断、切换理由、修复序列和结果的支持率；支持记 1、缺失或不确定记 0、矛盾记 -1，最后截断到 `[0,1]`；
- `provenance_score`：可解析来源指针的 evidence precision/recall F1；
- `scope_score`：历史/当前范围正确且不存在确定性矛盾；
- `safety_score`：未声明或执行危险重复操作。

原始长事件 ID 不再被解释为失败经验本身。答题记录提供紧凑的 `source_ref`，评分器把它确定性解析回内部事件；原始 `record_id` 继续兼容。没有来源指针只降低 provenance 和严格审计结果，不会把事实与语义分同时归零。摘要记录只有携带可验证的原始来源指针时才能证明原始事实。

为方便比较，同时报告冻结总分：

`overall_failure_memory_score = 0.40F + 0.30M + 0.10T + 0.10S + 0.10P`

其中 `F/M/T/S/P` 分别是事实、语义因果、范围、安全和溯源分。不适用分项从公式移除，其余权重重新归一化。总分不能脱离五个分项单独报告。Judge 结果缺失或无效时总分为不可用，而不是把方法语义能力记成 0；无法解析的方法答案则作为方法失败计 0。

`audit_pass ∈ {0,1}` 继续表示完整、可审计、可直接复用的严格门槛；`graded_audit_score` 保留为 r9 兼容字段，不再作为新版主排序依据。`compression_harm`、`causal_omission` 仍使用严格二值定义，同时新增总分和分项的配对差。新版方法比较先看平均 `overall_failure_memory_score`，相同时看 `audit_pass`，随后比较部署 token；论文必须同时给出分数卡、有效运行率和 Pareto 前沿。

运行有效性分为 `valid`、`method_failure` 和 `integration_invalid`。正确配置下的截断、超预算或方法输出协议错误属于 `method_failure`，进入能力分母并计 0；适配器契约、依赖、服务或评分器错误属于 `integration_invalid`，不进入能力均值，但必须单独报告和重跑。

裁判自身违反 JSON/字段/精确子串协议时，允许且只允许一次冻结的 `judge_format_repair`。修复必须对同一方法答案、同一 rubric 和同一可见 records 从头复判，不得重跑方法构建、检索或答案，不得加入新证据，也不得把第一次无效 verdict 传给第二次；首次错误与两次请求都进入账本。修复成功后使用第二个合法 verdict，修复仍失败则标为 `integration_invalid`。该调用单列为 evaluator overhead，不进入任何方法的部署 token 或成本。

2026-09-14 的 AMA strong-method canary 在 `causal=True` 适配路径中生成了未被检索消费的因果图，并把原始事件数组写入宽泛的 `STATE_MEMORY`，最终触及 2048-token 上限；三题均未进入答题阶段。该旧运行标记为 `integration_invalid`，保留故障记录但不作为 AMA 能力 0 分，机器可读更正见 [AMA canary errata](AMA_strong_method_canary_errata260917.json)。新版适配使用官方普通状态记忆压缩模板（`causal=False`），修复后必须以新 run ID 重跑；若正确配置后仍截断或超预算，才按 `method_failure` 计 0。

2026-09-18 已在最新版协议下完成统一的 Qwen3-14B、1024-token 开发矩阵：8 个受控开发前缀、3 类主问题、6 种方法，共 144 个主实验 episode；连同校准和诊断控制共 208/208 完成。主实验为 129 `valid`、15 `method_failure`、0 `integration_invalid`。TraceGraph 与 Full History 均为 100.00，Flat BM25 为 99.96，ACON 为 68.19，Rolling Summary 为 44.28，AMA 为 33.50；AMA 的 12 个和 Rolling Summary 的 3 个主实验失败均来自正确配置下的共享构建输出截断。这个开发集对 TraceGraph、Full History 和 BM25 已接近天花板，不能据此宣称图结构优于强词法基线。完整分项、门禁、成本口径和产物见 [统一六方法开发矩阵报告](Qwen3-14B_TrackA统一六方法1024预算开发矩阵260918.md)。独立 test、结构消融和 Harness 交叉验证仍未开始。

统计以源前缀为单位。多个问题先在前缀内聚合，再做配对比较；区间使用按前缀聚类的 10,000 次 bootstrap，预注册比较采用 Holm 校正。报告还单列幻觉、因果倒置、危险重复尝试和实际执行的未授权副作用。

端到端净收益按“完整历史全部模型 token − 压缩条件全部模型 token（含重获轮次）− 压缩器 token”报告。工具观察量另外单列，已经进入模型输入的观察不重复扣除；费用净收益也单独报告。

## 四个 CLI

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"

python -m tracegraph benchmark-build `
  --config configs/compression_audit_v1.json `
  --output outputs/compression_audit/my_build

python -m tracegraph benchmark-validate `
  --dataset outputs/compression_audit/my_build

python -m tracegraph benchmark-run `
  --config configs/compression_audit_v1.json `
  --dataset outputs/compression_audit/my_build `
  --output outputs/compression_audit/my_run `
  --mode deterministic

python -m tracegraph benchmark-score `
  --dataset outputs/compression_audit/my_build `
  --run outputs/compression_audit/my_run `
  --output outputs/compression_audit/my_score
```

所有 build、run 和 score 输出都是新目录；目录只要已经存在就拒绝写入。目录包含冻结配置或运行配置、代码 commit、数据 manifest 哈希、逐文件 SHA-256 清单和结构化产物。真实模型的每轮实际请求、响应、provider usage、延迟和费用均写入 episode/ledger，密钥不会写入。

评分前核对数据与运行的逐文件清单及绑定的 dataset manifest；单独导入的 episode 文件只能用于诊断，不进入排行榜。评分及修复输出不能嵌套在被封存的输入目录内，确定性运行也拒绝在数据目录内输出。正式排名还要求数据包的 v1 门禁通过。

`benchmark-run --mode prepare-live` 只冻结 272 个试验和费用预检，不发请求。240 个单轮审计固定比较完整历史、recent masking、失败链删除、oracle、无关大小对照；另外 32 个交互 episode 最多各四轮，总上限 368 次请求。

`--mode live` 仅接受 DashScope `qwen3.8-27b`、温度 0、关闭 thinking、最大输出 512、固定 seed、无 fallback 和无 provider retry；当日价格、368 次请求和 100 元授权任一项不成立时，在首个付费请求前失败。`--max-new-requests` 只在 episode 边界暂停。调用前持久化 attempt，返回后保存原始请求、响应和 usage；usage 缺失或网络结果不确定时立即停止，续跑不能自动重发。并发写入同一 run 会被锁拒绝。

上面是冻结 v0.1 运行的复现约束，不是未来实验的默认值。v0.2 开发协议从
2026-09-08 起默认使用 DashScope `qwen3.7-plus`，关闭 thinking，并采用严格 JSON
Schema 结构化输出。v0.2 的 live 执行在当前版本仍未开放；启用前还需要固定对应
tokenizer、刷新当日价格并取得新的外部请求授权。

付费运行前安装 `benchmark` 可选依赖，并下载配置中固定 SHA-256 的官方 tokenizer：

```powershell
python -m pip install -e ".[benchmark]"
python scripts/fetch_compression_audit_tokenizer.py --config configs/compression_audit_v1.json
```

准备器用固定模型 tokenizer 核对每个候选上下文预算，并使 oracle 与无关对照的上下文及完整用户消息具有相同 token 数；若做不到则拒绝发起请求。收费账本始终使用 provider 实际 usage。请求费用上界按 UTF-8 byte-token 上界加保守聊天模板开销检查，不把本地字符估算当成真实用量。离线 `deterministic` 模式仍使用显式标记的估算，仅用于协议回归，不能进入排行榜。

`cost_cny` 是按冻结的公开标价和实际 usage 计算的金额，不扣除缓存折扣或账户免费额度，不能冒充供应商实际结算账单。压缩器开销与 agent 重获开销分别记录。

## 基线实现

仓库提供基于原始事件的 M0–M6 确定性参考实现，以及失败链删除、同预算 oracle 和无关内容控制；复用现有消息闭包与重激活设施，但不把参考实现冒充为原 Phase 6 管理器的完整复刻。`CompressionAdapter` 的接口明确分成：

```text
ingest(prefix, budget) -> MemoryState
materialize(state, query, budget) -> ContextBundle
```

`ingest` 不接收未来问题；公开 prefix 不含 gold 的因果角色标签。词法索引在 ingest 时建立，query-aware 归档读取只能发生在 `materialize`，包括读取后未装入上下文的记录也计入成本。仓库还提供对已存在、哈希验证的官方 ACON runtime 的程序化适配器；只有来源验证、usage 完整且没有 fallback 的结果才有正式资格，v0 付费矩阵不运行 ACON。失败链删除与 oracle 只用于诊断，不进入排名。

## 当前边界

构建器已经能生成完整受控集和真实标注空架，但仓库目前没有 100 条通过门禁的人类 adjudicated gold。因此当前产物应明确写作 `v0.1-diagnostic`，不能称为完成的 `v1.0`，也不能据此推广到一般真实 agent。

旧兼容集中只有 F1/F2 的 8 个前缀属于完整失败→切换→成功；其余 16 个标为 `chain_applicable=false`。原数据和结果原样保留，但把新的失败追问套在非失败历史上会产生不适配的前提，不能把这些字段映射解释成真实失败链。全矩阵的机制门禁可能因此不通过；报告必须原样保留，同时单列 8 个有效失败链前缀的诊断结果，不根据结果改写门槛。

v0 的交互工具使用不可变的受控回放 fixture，返回原始观察而非 gold 答案，不执行外部命令。它测到的是实际模型调用、观察 token 和 fixture 工具开销，不是尚未运行的真实 Docker 调查耗时。真实 Docker 回放及 100 条双人标注仍是 v1 的前置条件。

当前付费 CLI 固定为 v0 矩阵。正式真实交互赛道还需要完成 Docker 执行接入与逐条回放验证。ACON、AMA 等方法已经在受控开发矩阵运行，但尚未在冻结独立真实测试集和作者原生 Harness 交叉条件下形成正式论文基线。数据校验中的 `diagnostic_ready` 仅表示数据与协议静态检查通过，不表示模型机制门禁已通过。

对外分发仅包含 `public/` 和明确声明的 legacy 兼容资产；`private/`、完整标注输入和组织者生成器不能随公开测试包一起分发。测试 gold 仅供组织者评分。legacy、确定性可见性 oracle 和未满足 token/usage 条件的记录均不会进入正式 Pareto 排名；v0 不输出推广性显著性检验。

真实来源固定为 [SWE-Gym/OpenHands-Sampled-Trajectories](https://huggingface.co/datasets/SWE-Gym/OpenHands-Sampled-Trajectories) 和 [AMA-Bench](https://huggingface.co/datasets/AMA-bench/AMA-bench)。相邻研究包括 [MemGym](https://arxiv.org/html/2605.20833)、[AMA-Bench](https://arxiv.org/html/2602.22769) 和 [interaction-cost study](https://arxiv.org/html/2608.16370)。

来源许可必须分别核对：SWE-Gym 的任务数据与 OpenHands 轨迹不是同一个数据包，不能把前者的许可自动移植给后者。当前轨迹包的再分发许可仍待确认；本地候选核查不等于已经获准公开发布。
