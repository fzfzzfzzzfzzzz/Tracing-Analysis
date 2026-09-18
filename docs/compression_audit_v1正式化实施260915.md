# compression_audit_v1 正式化实施记录（260915）

## 当前结论

正式 v1 的真实来源、候选选择、split 分配和双人盲标包已经冻结；人工 gold 尚未产生，因此 `v1_ready=false`，主比较和 τ³ 外部在线验证均未启动。这个停止是正确的实验门禁，不是程序故障。

2026-09-13 的两份 GLM 盲审只覆盖 40 条 Qwen3-14B 校准答案，用于开发期裁判诊断；它们不是 100 条真实轨迹的两位独立人工标注，不能复用为正式 v1 gold。

## 已完成的真实数据冻结

真实来源固定为配置 `configs/compression_audit_v1.json` 中的两个 revision：

- SWE-Gym/OpenHands-Sampled-Trajectories `baf3a4e4bff514d48ddc08a93a2ade5c126212c7`：6,055 条轨迹，三个 Parquet 分片；三个 LFS SHA-256 均已逐文件匹配。
- AMA-Bench `a5777378066f53229a94557a7b192435cd027909`：208 条轨迹，一份 JSONL；LFS SHA-256 `45c36052e1520d87ad9de4114f71c9df42d4aac9cf158c0c353e800b653d65ff` 已匹配。

全量启发式扫描提出 4,613 个候选。选择器在不读取人工标签和本项目方法成绩的情况下：

- 对 SWE-Gym 的多次 sampled run 按 source task 去重；
- 固定 seed `20260915`；
- 选择 60 条 SWE-Gym、40 条 AMA-Bench；
- 固定 dev/validation/test 为 20/20/60；
- source×split 为 SWE 12/12/36、AMA 8/8/24；
- 保证 100 个 source task 唯一，仓库不跨 split；
- 明确记录候选挖掘使用了源轨迹自身的 success/resolved 状态来寻找“失败后替代”窗口，但没有使用任何待比较方法的实验成绩。

冻结清单位于 `data/compression_audit/real_annotations_v1/selection_manifest.json`。第一批候选 SHA-256 为 `af693e8fc9ebe81986fce7aa2673c81706e71506aa1330f0f487410949504e4b`。

## 已修复的数据问题

AMA 官方 JSONL 的部分字符串包含 Unicode U+2028/U+2029。原 `load_jsonl()` 使用 `str.splitlines()`，会把合法 JSON 记录从字符串内部切断。现已改为按文件物理行迭代，并增加回归测试。

SWE-Gym 同一 task 有多个 sampled run，而旧候选 ID 只使用 task ID 和重复的消息 ordinal，存在跨 run 碰撞风险。现已把不可变 source-record SHA-256 纳入 candidate ID。

正式选择器支持全部三个 SWE 分片、source-task 去重、固定配额、仓库隔离和独立 reviewer 包。相关测试与 JSONL 回归共 11 项通过，ruff 通过。

## 双人标注包

两份包包含相同的 100 条 cases，但分别交给两位真实、彼此独立的标注者。reviewer cases 已移除 `candidate_hints`，避免自动候选窗口锚定人工判断；待填模板只保存 ID、冻结元数据和空白 failure-chain 字段。

- reviewer A：`data/compression_audit/real_annotations_v1/_packet/compression_audit_v1_reviewer_a_260915.zip`
- reviewer B：`data/compression_audit/real_annotations_v1/_packet/compression_audit_v1_reviewer_b_260915.zip`
- 两份 ZIP 内容 SHA-256：`2f0047a5f6654cb54c58d6cff9665d1b0291f6ae186fd761a44d61cbfc0cb476`

每位标注者应将自己的 `reviews.template.jsonl` 复制成 `reviews.completed.jsonl`，填写 `failure_chain`、唯一 `annotator` ID，并把状态改为 `annotated`。无法从轨迹证实完整链时必须标为 `rejected`，不能猜测。两人不得交换答案；GLM 或同一模型的两次输出不满足“双人独立人工标注”。

正式测试成员资格只在两份标注导入、分歧仲裁、质量门禁和 SWE Docker 回放全部通过后冻结。当前冻结的是候选成员和 split 分配，不能提前称作正式测试集。

## Docker 与许可

Docker Desktop 已可用。60/60 个 SWE 候选的 `xingyaoww/sweb.eval.x86_64.*` 镜像 manifest 均可访问，逐项 digest 保存在 `swe_gym_image_availability.json`。这只证明镜像可获取，不等于 60 条动作链已回放；命令回放必须等人工明确失败动作和替代动作后，用 digest 固定镜像执行并保存日志。

AMA 数据卡声明 MIT。SWE-Gym 代码仓库声明 Apache-2.0，但 OpenHands trajectory 数据卡没有声明数据许可。因此本地研究实验可继续，含 SWE 源文本的候选和 reviewer 包暂不可公开分发；详情见 `source_license_inventory.json`。

## 门禁与后续顺序

预标注构建位于 `outputs/compression_audit/v1_preannotation_freeze_260915`。普通结构校验通过；`benchmark-validate --require-v1` 返回退出码 1。当前真实门禁为 0 份双标、0 份仲裁、0 条真实回放，所以程序不会导入真实 prefixes，也不会启动主矩阵。

人工结果返回后的固定顺序是：

1. 校验两位 annotator 身份不同、100 条 ID/元数据完整、证据 ID 均存在于原轨迹；生成盲化分歧仲裁包。
2. 仲裁后计算 failure-family/recoverability Cohen's kappa 与 evidence-event F1；要求最小 kappa ≥0.80、平均 F1 ≥0.90。
3. 对 60 条 SWE 链使用已固定 digest 的 Docker 镜像执行回放并冻结日志 SHA-256。
4. 质量门禁通过后生成新的 v1.0 数据包，冻结 test IDs、private gold、exposure manifest 和逐文件 SHA-256；再次要求 `--require-v1` 退出码 0。
5. 才运行 Full History、recent masking、BM25、rolling summary、TraceGraph 五方法主比较。方法、预算、模型、prompt、tokenizer、runtime receipt 和费用口径在首个测试请求前冻结。
6. 离线主比较闭合后，使用现有官方 τ³ retail/airline 接入做在线外部验证。旧 τ³ 结果继续标记为 development evidence，不回填成新 v1 的确认结果。

