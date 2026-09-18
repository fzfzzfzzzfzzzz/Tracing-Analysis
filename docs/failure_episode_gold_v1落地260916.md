# failure_episode_gold_v1 落地记录（260916）

## 实施结果

已将任务级失败 episode 从临时 `annotation.audit_chain_rubric` 提升为一等金标结构 `compression_audit_failure_episode_gold_v1`。旧 `FailureChainGold` 仍可无损加载；没有 episode 的旧记录不会新增序列化字段，旧 gold hash 保持兼容。

新结构明确区分：

- 初始任务级失败锚点及其直接结果；
- 一个或多个修复阶段及每阶段的决定、动作、结果和 outcome；
- 最终 resolution；
- 首选核心事件与可选支持事件；
- audit-chain 的多证据路径和部分顺序；
- audit-recovery、interactive-reacquisition 各自的证据策略；
- 需要由答案完整表达的 recovery sequence。

`make_rubric()` 已原生消费 episode。`audit_recovery`、`audit_chain` 和 `interactive_reacquisition` 均会增加 `repair_sequence` 语义事实，并使用 episode 自身的 evidence policy；rubric/scoring 版本升级为 `compression_audit_rubric_v02_4` / `v0.2-development-judge-contract-r6`。

## 开发回归分支

从未改写的父数据集 `glm_ai_adjudicated_dev_260916_r1` 新建：

- `outputs/compression_audit/glm_ai_failure_episode_dev_260916_r1`
- 父 manifest SHA-256：`74ffd61a6c0760fb6fa79dd8ce960e9bfccbd24e43240a035f99ae11a7ebbac0`
- 新 manifest SHA-256：`95ee6afb7cb84dbd9813b74f60115deb48dc72c8804f38c22a26cfbd901329f0`
- 新 file manifest SHA-256：`307d606b23789d2a2e759129b99d067550f7b4953be1bc492ae28df66d933135`
- 新模型请求：0

两条已暴露案例各表达为三阶段修复 episode：

- `f31d17...`：梯度修复 → 两次缩进修复 → 成功；
- `9b9e94...`：补 spatial_dims → 补 channels → 补 strides → 成功。

三类查询的组织者标准答案全部通过新 rubric；只回答后续局部 `IndentationError` 或最后一次 `strides` 子失败的引用无法覆盖任务级 chain。两条金标已不再包含旧 `audit_chain_rubric` overlay。

## 新 A/B 盲标包

基于先前冻结的 100 条真实轨迹成员生成新 schema 的空白 A/B 包：

- `审阅/failure_episode_v1_260916_r1/failure_episode_v1_reviewer_a_260916.zip`
- `审阅/failure_episode_v1_260916_r1/failure_episode_v1_reviewer_b_260916.zip`
- 两份内容 SHA-256：`ae1472dffd3ff24d0b9dffc0945888cfbe1215d21f5b8b5c58e7bdc246b01c93`

每份包含 100 条案例、空白模板、JSON Schema 和说明。包内不包含本项目被测模型答案、候选启发式提示或旧 GLM/ZCode 标签。

暴露审计确认：先前开发实验使用的 7 条轨迹均属于这 100 条中的 dev split；validation/test overlap 分别为 0/0。因此 60 条 test 尚未被 Qwen3-14B 主实验使用。开发 7 条应继续只作 schema/评分回归，不得计入确认性测试结果。

旧 GLM A/B 表使用单失败/单替代 schema，不能自动转换成正式 episode 标注。新 A/B 必须由两位不同的人独立填写；若继续用两个 AI 标注，只能形成新的开发数据，不能满足论文的双人独立人工标注门禁。

校验入口：

```powershell
python scripts/validate_failure_episode_annotations.py `
  --cases 审阅/failure_episode_v1_260916_r1/reviewer_a/cases.jsonl `
  --reviews 审阅/failure_episode_v1_260916_r1/reviewer_a/reviews.completed.jsonl
```

校验器会检查身份和冻结元数据、事件存在性、tool-call 类型、阶段时间顺序、error signature 来源、最终 resolution、核心/可选证据、每个查询的最小证据集合以及因果边。

## 验证

- 新增两个暴露 episode 的“完整任务链通过、局部子失败不通过”回归测试；
- episode 序列化往返和旧 gold hash 兼容测试通过；
- recovery/interactive 原生 policy 测试通过；
- 相关测试共 101 项通过；
- 修改文件 Ruff 检查通过；
- 未启动 151，未产生 provider/model 请求。

## 下一门禁

当前程序开发阶段完成。下一步不是跑主矩阵，而是由两位独立人工分别填写新 A/B 包。两份结果通过程序校验后，才能生成盲化分歧包、进行第三方裁决、计算一致性并冻结正式 test gold。

## 260917 AI 开发双审进展

为加快流程，A/B 暂由同一底层 GLM-5.2 的两个 pass 生成。两份均已通过 episode 结构、事件存在性、tool-call 类型、时间顺序、error signature 来源、resolution 和 evidence-policy 校验，但明确保持 `development_only=true`、`formal_ready=false`：

- A：100 条，29 annotated / 71 rejected，SHA-256 `34c48ce66567d456fdbdc697650da96c815337c48c98949172a3ef336ecdad02`；
- B：100 条，31 annotated / 69 rejected，SHA-256 `0d71d69f62f1d921532ac6840719477d4324f340bd2377c7be801f3e954a5ab9`。

一致性结果：

- 状态原始一致率 0.98，状态 κ=0.9524；
- 69 条双方均拒绝；29 条双方均接受；2 条状态冲突；
- 双方均接受部分的 failure-family κ=0.3256，recoverability κ=0.2750；
- required-core evidence F1 均值 0.8441；全部引用事件 F1 均值 0.8050；
- 只有 1 条 episode 完全逐字段一致，且该条使用了共享示例，不能视为独立一致性证据。

因此不能机械选择 A 或 B。已生成 30 条第三方 AI 开发裁决包：

- `审阅/failure_episode_v1_260916_r1/adjudication_ai_r1/failure_episode_ai_adjudication_packet.zip`
- 组成：28 条双方接受但 episode 不一致，2 条接受/拒绝冲突；
- split：dev 13、validation 5、test 12；
- 其余 69 条共识拒绝和 1 条完全一致 episode 已单独保存，但仍只具开发地位。

裁决结果必须通过 `scripts/validate_failure_episode_adjudication.py` 后，才能组装开发数据集。即使通过，也不能称为双人独立人工 gold；若后续实验出现值得保留的结论，仍需对最终纳入案例进行独立人工复核和裁决。

## 260917 AI 开发 canary 与 r10 归因

AI 裁决已经组装为 29 条 R0 开发 episode，并在其中 3 条能装入 32K 的已暴露短 SWE
轨迹上完成 live canary。r9 修复了 server schema 漏传 `repair_sequence`；r10 进一步移除
任务级 episode 中与多步修复冲突的单一 replacement strict 字段，并把稳定错误签名改为
“答案包含金标签名”的单向匹配。旧单步题保持原契约。

零调用敏感性把 Oracle audit-chain 从 0/3 恢复到 2/3；fresh r10 thinking-off 复现
Oracle 2/3，但 Full History 仍为 0/3。开启 thinking 后，Full History 变为 1/3、Oracle
变为 1/3；6 条目标题总体 hard pass 仍为 2/6，而回答 token 和延迟分别约增至 2.03 倍
和 1.83 倍。两种设置的语义事实均为 6/6，主要差异来自精确锚点动作和引用选择。

完整过程、哈希和解释边界见
`docs/Qwen3-14B_failure_episode_AI开发canary结果260917.md`。下一步不是扩展模型调用，而是
对这 3 条的锚点层级和有效额外引用做独立人工复核；结论通过后再进入未暴露 validation。

## 260917 定向 AI 复核完成

定向 A/B 已由同一 GLM-5.2 的两个 pass 填写并通过程序验收。两份对 3 条锚点、严格
`execute_bash`、参数、错误结果、稳定签名和相关/禁止证据划分全部一致；唯一分歧是最终
验证命令应列为 required core 还是 optional support。确定性裁决采用较小充分核心，并把
验证命令保留为有效 optional support。该结果仍明确标记为非独立 AI 开发复核。

已从 `failure_episode_ai_dev_260917_r2` 新建子分支
`failure_episode_ai_dev_targeted_review_260917_r3`，只改变 3 个 gold hash，不改变任何公共
prefix 或 query。对 thinking-off/on 的已保存调用完成零调用复算：直接可解释的 Full
History 分别仍为 0/3 和 1/3；thinking-on 的通过项由 MONAI 换成 pandas，证明聚合分数对
证据边界和错误签名敏感，但“语义正确而锚点 provenance/引用不稳定”的结论仍成立。

下一门禁收缩为两位不同人工对这 3 条做独立复核；在此之前不扩展模型调用，也不把当前
开发分数写成正式 benchmark 结果。
