# Phase 5.1 生命周期证据增强结果

> 状态：P51-G0 `stop_old_corpus_path`（2026-08-01）  
> 上位结论：F5-G1 No-Go 保持不变；GDSC-Structured 与外部试验仍未授权  
> 外部 provider generations：0

## 为什么做这个审计

F5-G1 证明了现有 GDSC-Prune 很安全，但旧 trace 中能确定性判为 dead 的工具交换太少。Phase
5.1 检查用户提出的方向：不增加 LLM 调用，只用运行时结构化字段、确定性规则和受约束正则，
能否把“已消费、已替代、已失效、必须保留 receipt”等关系记录得更清楚。

本次没有采集新会话。它在同一批 261 个冻结 prefixes 上做 evidence-ceiling audit，并在看结果
前冻结了门槛。成本分析仍是原来的 185 个 eligible prefixes；没有读取 task reward、最终成败或
treatment outcome。

## 两层证据

Grade A 是安全下界。只有当一个成功、非 side-effect 工具结果的**全部内容**就是一个标量（或
仅包装该标量的单字段对象），且后续工具参数按相同 JSON 类型和值精确包含它，才添加
`provides_input`。一般详情查询即使返回的 ID 被复用，只要还含姓名、状态等其他字段，就不能
宣称整个结果已消费。成功写操作的 receipt 只会加强保留，不触发删除。

Grade B 是故意偏乐观的上限：多字段结果中的精确实体 ID 流，以及写操作和旧读结果的精确实体
重合，都暂时假设来源工具 span 可以删。由于它缺少 effect scope、entity version 和 refresh
dominance，这个投影明确禁止发送给 provider。

正则只匹配结构化字段名和工具名前缀。自由文本语义、模糊字符串和 LLM 分类均未使用。

## 结果

| 条件 | eligible prefixes 有缩减 | paired median delta | 是否达到 P51-G0 |
| --- | ---: | ---: | --- |
| 冻结 F5-G1 Prune | `4/185` | `0` | 否 |
| Grade A 安全下界 | `10/185` | `0` | 否 |
| Grade B 乐观上限 | `36/185` | `0` | 否；需至少 `93/185` |

Grade A 共得到 10 条可产生 hard-dead 的完整标量消费关系和 25 条只保留的 side-effect receipt
记录。Grade B 共记录 1,483 条 exact-entity-flow 与 30 条 mutation-invalidation 候选；这些数字
会在多个 prefix 重复，实际只有 82 个 prefix 出现 Grade B 候选，成本合格层最终只有 36 个发生
序列化缩减。

分域上，airline 的 eligible reduced 数为 F5/Grade-A/ceiling=`4/4/5`，retail 为
`0/6/31`。这说明当前可利用的新关系主要来自 retail 的 user-ID 等结构化流，但覆盖面仍不足以
改变总体中位数。

所有 261 个 prefix 的 determinism、future-suffix independence、protocol validity、request
hash 与冻结 F5 baseline match 均为 100%。Grade A 的 root/policy/confirmation/receipt
false-dead 为 0。

## 裁决与含义

P51-G0 的 blockers 是：

1. `optimistic_coverage_below_median_requirement`：36 小于冻结的 93；
2. `optimistic_paired_median_not_negative`：乐观上限的配对中位数仍为 0。

因此停止继续在旧语料上添加规则。这不是说“运行时记录关系”方向错误；相反，Grade A 已把
缩减 prefix 从 4 提到 10，证明纯本地结构化关系确实能工作。停止的原因是旧 trace 没有记录
足够的写入影响范围、实体前后版本、receipt ID 和显式 consumption/invalidation，事后正则无法
可靠重建这些事实。

若继续该方向，科学上应另立并预注册一个**新遥测采集研究**，让工具/环境在执行时直接返回
`entity_key`、`entity_version_before/after`、`effect_scope`、`receipt_id`、
`consumes_event_ids` 和 `invalidates_event_ids`。P51-G0 失败后，本计划不自动授权这项采集，
更不授权外部模型会话。

## 权威 artifacts

| Artifact | SHA-256 |
| --- | --- |
| 冻结配置文件 | `1fdeaa19640f7065e65dbb98f72c4888d8c39a636e9cac1d147898455ff4a177` |
| Summary embedded hash | `cbd16a6af2eb8ffae5386d2e036a3aebefdcd8f5f184f94efbe687ee4e631eb9` |
| P51-G0 gate embedded hash | `04eb9149c480a8984ba203130314f8dd435bd0b3aa8692a4dfed3d39c67083b1` |
| Run manifest embedded hash | `4bb260a1723308850c7a761c5832ace5ece2ba62064707096951d27f1426b033` |
| Audit output tree | `d0f2bb7009c3c4e03175bdc118a2fe9099e09eba2f6c56fd68fb0c1454c3bc4c` |

结果目录为 `outputs/phase5_1/e0_evidence_ceiling_v1/`，所有文件均为 create-only；旧 Phase 4、
Phase 5 和 F5 replay artifacts 未改写。
