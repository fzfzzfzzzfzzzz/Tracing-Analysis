# Phase 5.2：双遍伪标注与符号生命周期状态机

> 状态日期：2026-08-01  
> 性质：development evidence；不是人工 gold，不授权 hard-dead 或外部行为实验。

## 1. 本阶段要回答的问题

Phase 5.1 只在旧轨迹里寻找已经显式存在的精确关系，因此 Grade A 是严格下界，Grade B 只是“当前精确匹配候选集上限”。Phase 5.2 不再把旧全体中位数所对应的 93 个 prefix 当作生命周期建模门槛，而是直接测试：

1. 历史轨迹中究竟有多少生命周期机会；
2. “工具语义注册表 + 实体/字段状态机”能否在 held-out task 4 上精确识别这些机会；
3. 在不调用行为模型的情况下，这些机会对应多少自然分布输入成本。

Phase 4 R2/E0、Phase 5 F5-G1 和 Phase 5.1 的既有 No-Go 均保留，不追溯修改旧 artifact 或 30% 历史门槛。

## 2. 冻结人口与双遍协议

- 全部 261 个 frozen prefix 都被结构化记录；其中 185 个包含历史工具调用，76 个为“无机会”且不调用模型。
- 每遍包含 1,092 个 call/result 判断单元，共冻结 370 个 prefix-level 请求和 2,184 个 span-level 标签位置。
- Pass A 按时间顺序；Pass B 由 `SHA256(prefix_id + "pass_b")` 导出的 digest 排序打乱，并重新映射 opaque span/event ID。
- 请求只含 cutoff 前的当前目标、policy、native tool schema、调用/结果和事件关系。constraint 正文只在 policy 字段出现一次，避免重复 token。
- reward、未来事件、F5/5.1 标签、剪枝结果和 token 收益字段会在网络发送前被拒绝。
- 模型固定为 `glm-4.7-flash`，`temperature=0`、thinking disabled、`max_tokens=4096`；禁止付费、第二模型和自动 fallback。

冻结请求集的准备结果：

| 项目 | 数值 |
| --- | ---: |
| 全部 prefix | 261 |
| 有历史 span 的 prefix | 185 |
| 无历史 span 的 prefix | 76 |
| 每遍判断单元 | 1,092 |
| 两遍请求 | 370 |
| 估算输入 token | 3,528,760 |
| 输入硬上限 | 3,600,000 |
| 请求硬上限 | 400 |
| 实际输出硬上限 | 500,000 |

价格快照记录 Z.AI 官方页面在 2026-08-01 将 GLM-4.7-Flash 的 input、cached input 和 output 均列为 free；运行器要求调用时显式确认快照哈希，快照过期或价格不再免费时立即停止。

## 3. 标签、共识与保护规则

每个 span 输出 disposition、terminal reason、relation targets、obligations、evidence events 和 reactivation risk。两遍只有在 disposition、reason、targets、obligations 四项完全一致时才形成 machine consensus；否则统一成为 `uncertain`。

机器标签只用于测量机会率和比较符号状态机。即使 consensus 为 `safe_to_evict`，也不会：

- 修改 EventGraph；
- 生成 hard-dead；
- 被发送给 provider；
- 替代正式人工构念验证。

policy、confirmation、receipt 或 audit obligation 被共识标为 safe 时，结构上可以被记录，但伪标签质量门禁必须失败，不能通过重试把这一错误“洗掉”。

## 4. 方案 A：ToolEffectSpec + 实体字段状态机

`ToolEffectSpec` 覆盖当前轨迹实际出现的 15 种工具，冻结 effect type、实体键、读写字段范围、完整/局部快照、成功条件、receipt 义务和 invalidation 范围。状态机顺序重放 prefix，并生成独立的 `LifecyclePrediction`：

- 完整的同实体后续读取可以替代旧完整读取；
- 成功写入只对相交字段建立失效关系；旧读取含其他字段时闭锁为 uncertain；
- 完整单一标量结果被后续结构化参数消费时可判 consumed；
- 同工具、同实体的成功 retry 可以解决旧失败；
- write/handoff receipt、side effect 和显式 reactivation 强制保留；
- 未注册工具、复合 span 不完整或实体键缺失时 fail-closed。

状态机不修改原图。离线投影只有在同一 provider 消息中的所有 call-level span 都安全时才整组删除，始终携带 `never_send_to_provider=true`。

全 261 prefix 的无标签完整性预演已经通过：1,092 个预测；determinism、future-suffix independence、EventGraph unchanged、archive、protocol 和 projection-send-forbidden 均为 100%。这只说明实现满足工程合同，不说明预测语义正确。

## 5. 数据划分与门禁

- task 0/1/2：规则开发；
- task 3：校准并冻结规则/阈值；
- task 4：airline + retail 双域 held-out；
- 同一 task 的三个 trial 不跨集合。

先判伪标签稳定性：185/185 双遍完成、safe 二元一致率至少 80%、同模型 Cohen’s κ 至少 0.60、至少 20 个 consensus safe，且 protected consensus safe 为 0。失败即停止 Phase 5.2。

只有前门通过，才在 task 4 判状态机：safe precision 至少 0.90、live-critical recall 至少 0.95、severe false-dead 为 0、至少识别 5 个 consensus safe 且多于同测试集 Phase 5.1 Grade A，并要求所有完整性率为 100%。通过也只表示“值得做少量人工复核”。

## 6. 当前外部执行状态

首个冻结请求成功返回，7/7 标签通过 schema 和 ID 校验，usage 为 8,041 input / 554 output token。紧接的 Pass B 请求连续收到 Z.AI HTTP 429 / code 1305；官方错误表将 1305 定义为触发 rate limit。2026-08-01 再次探测时，Pass B 曾成功完成，但下一条冻结请求立即重新收到 429，说明限流仅短暂解除、尚未稳定恢复。

当前收集因此处于“可恢复暂停”，不是质量门禁失败：

- HTTP 尝试：7/400，其中 HTTP 200 为 2、HTTP 429 为 5；
- 有效请求：2/370；
- 累计 usage：16,085 input / 1,016 output token；
- 外部行为模型会话：0；
- 第二模型、付费 fallback：0；
- 已保存每次脱敏原始响应、usage 和验证结果；
- 429/网络/5xx 只暂停并计入全局 400 上限，不消耗一次结构化标签重试名额。

在账户速率限制未知时，不应反复轮询消耗仅有的 30 次失败余量。完成双遍收集前，不生成伪标签 gate、held-out 结论或生命周期有效性主张。

## 7. 主要实现与 artifact

- 配置：`configs/phase52_lifecycle_modeling.json`
- 双遍协议：`src/tracegraph/lifecycle_annotation.py`
- 状态机：`src/tracegraph/lifecycle_state_machine.py`
- 请求冻结：`scripts/build_phase52_annotation_requests.py`
- 可恢复 GLM runner：`scripts/run_phase52_glm_annotations.py`
- 追加式价格复核：`scripts/record_phase52_pricing_snapshot.py`
- 状态机预演：`scripts/preflight_phase52_state_machine.py`
- held-out 评估：`scripts/evaluate_phase52_state_machine.py`
- artifacts：`outputs/phase5_2/e0_glm_pseudolabel_v1`

P52-WP0 checkpoint 为 `outputs/phase5_2/checkpoints/p52_wp0_phase51_complete`，checkpoint SHA-256 为 `0827bc97ffe661156ab6120a0bebe8e56a5957f1104a63a8c98989cabd54f0d0`。

## 8. 恢复收集

恢复前必须重新打开配置中记录的官方定价页和模型页，确认 `GLM-4.7-Flash` 的 input、cached input、output 仍全部免费。确认后创建一份不可覆盖的新快照：

```powershell
python scripts\record_phase52_pricing_snapshot.py --checked-at YYYY-MM-DD --confirm-official-free
```

该命令会返回新文件路径和 `snapshot_sha256`。恢复 runner 时必须同时提交二者：

```powershell
$env:PYTHONPATH='src'
python scripts\run_phase52_glm_annotations.py `
  --pricing-snapshot outputs\phase5_2\e0_glm_pseudolabel_v1\pricing_snapshots\<snapshot>.json `
  --confirm-pricing-snapshot-sha256 <snapshot_sha256>
```

runner 只接受初始快照或上述目录中的追加式快照，并再次校验日期、哈希、固定模型及三项免费价格。若任一价格不再免费，必须停止并重新请求授权；不得切换模型、启用付费或自动 fallback。

## 9. GLM-5.2 付费快速试跑条件（e1）

2026-08-01，用户明确授权将 Phase 5.2 伪标注输入数据发送到 Z.AI `open.bigmodel.cn` 的 `GLM-5.2`，用于先跑 10 个请求；若无报错，再继续全量。

为避免污染原 `glm-4.7-flash` 免费条件，新增独立配置与输出目录：

- 配置：`configs/phase52_lifecycle_modeling_glm52.json`
- 输出：`outputs/phase5_2/e1_glm52_pseudolabel_v1`
- 模型：`zai/glm-5.2`
- 官方价格快照：input `1.4 USD / 1M tokens`，output `4.4 USD / 1M tokens`
- 冻结请求：370 个
- 估算输入 token：3,528,234
- provider requests before smoke：0

本地准备与离线检查已完成：

- request regeneration：370/370
- state-machine preflight：261 prefixes / 1,092 predictions
- determinism、future-suffix independence、EventGraph unchanged、archive、protocol、projection-send-forbidden：均为 100%

真实冒烟执行第一条请求即返回 HTTP 429，因此没有继续跑剩余 9 条，也没有进入全量：

- pause report：`outputs/phase5_2/e1_glm52_pseudolabel_v1/pause_reports/pause_0001.json`
- report SHA-256：`8bdf0093fa462a8e99f3b7c7b5c9c1b9d06789a85846d44487375c82dbc4f8be`
- attempts：1
- valid labels：0
- usage：0 input / 0 output token
- failure is quality No-Go：false

该结果说明 `glm-5.2` 当前也受外部限流影响；它不是标签质量失败。恢复时必须继续使用同一 frozen request set、同一 `glm-5.2` 配置，并重新核价；不得把 e1 与 e0 的标签混合作为同一伪标注条件。
