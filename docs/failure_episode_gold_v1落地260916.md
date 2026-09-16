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
