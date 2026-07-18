# 生命周期人工双标协议

本协议用于调研报告中的 lifecycle gold labels。机器推断不能充当 gold；必须由两位互不交流、看不到模型预测的标注者独立完成，再对分歧项做第三方裁决。

## 生成盲化标注包

先从已完成并校验的真实 TraceGraph 中按预测状态分层抽样：

```powershell
python scripts/export_lifecycle_annotation.py `
  --input data/processed/g47f_s1_v1_graphs `
  --output outputs/annotations/g47f_s1_v1 `
  --sample-size 120 `
  --seed 300
```

输出：

- `annotator_a.csv`、`annotator_b.csv`：相同样本、独立随机顺序，不含机器预测；
- `annotation_key.json`：样本与预测状态的隔离映射，只能由实验管理员保存，冻结人工标签前不得交给标注者。

每条记录提供节点类型、step、内容和入/出边邻居上下文。标注者只填写 `annotator_label`、`confidence` 和 `notes`，不得查看另一位标注者的文件。

## 标签定义

- `created`：节点刚创建，尚无足够证据判断后续用途；
- `active`：当前目标或下一步决策仍直接需要；
- `critical_evidence`：删除会破坏最终动作、policy 合规或关键事实依据；
- `consumed`：已被后续决策使用，但仍可能留在工作轨迹中；
- `unresolved_failure`：失败尚未被修复，必须防止重复同一路径；
- `resolved_failure`：失败已有成功重试或明确替代方案；
- `superseded`：内容已被更新、更具体或更可信的节点覆盖；
- `archived`：不再需要进入 active context，但原始记录应可恢复；
- `audit_required`：涉及 side effect、写操作或审计证据，必须外部保留。

判定优先级：未解决失败与审计要求优先于一般 active/consumed；关键证据优先于压缩收益；只有确有覆盖关系时才标 `superseded`，不能把“旧”直接等同于“过期”。

## 冻结、评分与裁决

两位标注者完成后先复制为只读快照，再运行：

```powershell
python scripts/score_lifecycle_annotation.py `
  --annotator-a outputs/annotations/g47f_s1_v1/annotator_a.csv `
  --annotator-b outputs/annotations/g47f_s1_v1/annotator_b.csv `
  --key outputs/annotations/g47f_s1_v1/annotation_key.json `
  --output outputs/annotations/g47f_s1_v1/scored
```

评分器会拒绝空标签、未知标签、重复 ID 和两份文件样本不一致，输出 observed agreement、Cohen's κ、标签边际分布、confusion matrix 以及 `adjudication.csv`。第三位裁决者只处理分歧项；裁决完成且全量校验后，才可称为 gold labels。

120 条只用于标注说明与一致性 pilot。正式论文样本量应根据状态稀有度扩展，并报告每类样本数、κ、分歧率、裁决规则和预测模型相对 gold 的混淆矩阵。

所有标注产物位于被 Git 忽略的 `outputs/`，默认不发布原始会话内容。

全局分层包已于 2026-07-16 从 `glm-4.7-flash` Stage 1 的 30 个真实图生成：`annotator_a.csv` 与 `annotator_b.csv` 各 120 条，表头不含 `predicted_lifecycle`，机器预测只存在于隔离的 `annotation_key.json`。盲标时只把两份 CSV 分别交给两位标注者；管理员保留 key，直到两份标签冻结后再评分。

修正版分歧优先包位于 `outputs/annotations/g47f_ml_c2_targeted/`。它从 `g47f_ml_c2` 的 Full Ours/no-lifecycle 成功分歧或失败信号中选出的 12 条 Full Ours trace 抽样，同样提供两份各 120 条的盲化 CSV；隔离 key 中的机器状态为 Active 50、Consumed 50、Audit-required 10、Critical Evidence 6、Unresolved Failure 3、Superseded 1。建议先使用该包做定向错误分析，再用 `g47f_s1_v1` 的全局分层包估计总体一致性；两套样本不得混合计算一个未披露采样设计的 κ。修复前 `g47f_ml_3t1_targeted` 只保留为历史错误分析包。选择逻辑和状态分布见 [生命周期分歧诊断](LIFECYCLE_DIAGNOSTICS.md)。
