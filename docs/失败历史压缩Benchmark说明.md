# compression_audit_v1：失败历史压缩 Benchmark

## 它测量什么

`compression_audit_v1` 测量 agent 压缩历史以后，是否仍能审计一条完整的失败链：

`失败动作及参数 → 错误签名 → 诊断证据 → 切换决定 → 替代动作及参数 → 成功证据`

如果压缩遗漏了其中一段，第二赛道继续测量 agent 为重新获得事实而增加的模型调用、工具调用、模型输入/输出量、工具观察量、延迟和费用。它不把所有指标压成一个总分，而报告压缩率、审计通过率、因果遗漏率、重获成本和 Pareto 前沿。

本项目不声称首次研究压缩导致的重复检索。相关工作已经分别研究压缩后的安全性、轨迹问答和交互成本。这里更窄的贡献是：在同一组配对反事实条件中，同时测量“失败链能否审计”和“遗漏后的实际重获成本”。

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

## 主评分

主判分不调用 LLM judge。动作名、参数、错误签名、替代动作和因果顺序使用规范化的结构化匹配；证据必须引用真实事件 ID。自然语言诊断、切换理由和成功说明只报告辅助文本质量，文本相似度不决定主排行榜。

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

当前付费 CLI 固定为 v0 矩阵。正式真实交互赛道还需要完成 Docker 执行接入与逐条回放验证，ACON 等正式模型基线也尚未运行。数据校验中的 `diagnostic_ready` 仅表示数据与协议静态检查通过，不表示模型机制门禁已通过。

对外分发仅包含 `public/` 和明确声明的 legacy 兼容资产；`private/`、完整标注输入和组织者生成器不能随公开测试包一起分发。测试 gold 仅供组织者评分。legacy、确定性可见性 oracle 和未满足 token/usage 条件的记录均不会进入正式 Pareto 排名；v0 不输出推广性显著性检验。

真实来源固定为 [SWE-Gym/OpenHands-Sampled-Trajectories](https://huggingface.co/datasets/SWE-Gym/OpenHands-Sampled-Trajectories) 和 [AMA-Bench](https://huggingface.co/datasets/AMA-bench/AMA-bench)。相邻研究包括 [MemGym](https://arxiv.org/html/2605.20833)、[AMA-Bench](https://arxiv.org/html/2602.22769) 和 [interaction-cost study](https://arxiv.org/html/2608.16370)。

来源许可必须分别核对：SWE-Gym 的任务数据与 OpenHands 轨迹不是同一个数据包，不能把前者的许可自动移植给后者。当前轨迹包的再分发许可仍待确认；本地候选核查不等于已经获准公开发布。
