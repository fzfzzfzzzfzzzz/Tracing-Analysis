# 标注任务书（pass B）— failure_episode_gold_v1

你将作为独立标注者 B，为 compression_audit 任务级失败 episode 金标做盲标。判断的唯一依据是包内 `cases.jsonl` 的 `prefix.events`。

## 盲标纪律

- 可读：本工作目录（`_ai_annotation_r1`）内的 `dossiers/`、`anno_lib.py`、`exemplar_case001.py`（仅作格式与粒度范例，它是 case 001 的已填标注）、`batches_pass_b.json`。
- 不可读：`pass_a/` 目录及任何其他标注产出；本目录之外的任何项目文件（`data/`、`outputs/`、`../reviewer_b/` 等）；旧版标注或 gold。不得联网。
- `environment_snapshot.qa_pairs` 仅可用于定向理解，任何字段取值都必须在真实事件文本中核实。

## 状态选择

每案例输出一行（用 `anno_lib.write_row` 追加到你的输出文件）：

- `annotated`：能给出完整任务级 episode——初始失败、至少一个修复阶段、最终成功被直接观察到。
- `rejected`：给不出完整 episode（最终未成功/失败无法定位/轨迹截断）。写明中文理由；宁缺毋滥。

## 核心语义

1. 锚点（anchor == initial_action）= 第一个任务级失败动作（tool_call），其直接结果即初始失败结果。探索期的局部错误（缺模块、路径打错等）与修复中途的局部子失败都**不是**锚点。
2. `error_signature`：初始失败结果 JSON dump 的**逐字子串**（≤120 字符），从 dossier `C:` 行里连续复制，禁止改写。
3. `repair_steps`：时间顺序的修复阶段。action 必为 tool_call；decision 在 action 前（可空）；result 在 action 后并体现该阶段效果；除最后一步 `resolved` 外全部 `intermediate_failure`；最后一步的结果必须包含最终成功的直接观察，`resolution_source_event_ids` 从中选取。
4. `required_core_source_event_ids`：最小闭环，≥4 条、时间有序。`optional_support_source_event_ids` 与之不相交。
5. 三个 policy 各需要：非空 `relevant_evidence_ids`；≥1 个互异 `alternative_evidence_sets`（⊆ relevant）；一一对应的 `causal_paths`（集合相等，边 [前因,后果] 严格保序，单元素集合 constraints 为空）。`chain_policy.relevant` 建议取全部被引用事件（初始+各步+resolution+core+optional 的并集）——校验器要求三 policy relevant 并集覆盖全部被引用事件。`audit_recovery` 偏修复链证据，`interactive_reacquisition` 偏事后可重获事件。

## recoverability（初始失败事实的可重获性）

R0 只存于历史（已修复且无法只读复现）；R1 一次只读调用可重获；R2 需多步调查重建；R3 原动作不可安全重复（副作用/非幂等）。

## failure_family

snake_case 短类别；受控词表优先（shell_syntax/parameter_schema/permission_policy/stale_state/dependency_version/patch_test/path_environment/timeout_resource/partial_side_effect/multi_failure_recovery），否则自造（如 `wrong_api_usage`、`missing_import`）。

## 叙述字段

`diagnostic_evidence`/`recovery_sequence`/`resolution_evidence`/`semantic_change`：中文、具体、带事件 id。

## 流程

1. 通读 dossier；用 `anno_lib.show(cid, eid)` / `show_range(cid, a, b)` 核对关键事件全文。
2. 填 episode（结构照 `exemplar_case001.py`），写入：
   ```python
   import anno_lib as A
   A.write_row(r'<输出文件>', '<candidate_id>', annotator='ai-draft:zcode:glm-5.2:pass-b',
               status='annotated', episode=EPISODE)
   ```
3. 批次完成后 `python anno_lib.py <输出文件>` 自检至 0 ERROR。
4. 汇报：annotated/rejected 计数、reject 理由、疑难案例。不要贴标注 JSON。

## 校验器硬规则

anchor=initial=tool_call；初始结果>初始动作；signature 为初始结果 dump 逐字子串；各步 action>上一步最大结果，decision<action<result；仅末步 resolved；resolution⊆末步结果；core≥4 时间有序；core∩optional=∅；policy 内集合/路径/边规则如上；relevant 并集覆盖全部被引用事件。
