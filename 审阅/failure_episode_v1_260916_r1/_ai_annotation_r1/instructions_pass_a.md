# 标注指南（pass A）— failure_episode_gold_v1

你是一名独立标注者，为 compression_audit 的任务级失败 episode 金标做盲标。你只能依据包内 `cases.jsonl` 的 `prefix.events` 判断，不得引用任何外部知识。

## 严格边界（盲标纪律）

- 允许读取：本工作目录（`_ai_annotation_r1`）下的 `dossiers/`、`anno_lib.py`、`exemplar_case001.py`、你的批次文件。
- 禁止读取：本目录之外的任何项目文件；`data/`、`outputs/`、`../reviewer_b/`、其他批次输出、任何旧标注或 gold 文件。不得联网。
- 每条判断必须落在真实事件的文本证据上；`environment_snapshot.qa_pairs` 只能用来定向，不能作为证据来源。

## 每个案例的产出

一行 JSON（用 `anno_lib.write_row` 写入你的批次输出文件），两种状态：

- `annotated`：轨迹支持完整的任务级 episode（初始失败 → ≥1 个修复阶段 → 最终成功被观察到）。
- `rejected`：无法支持完整 episode（如：最终从未成功、失败无法定位、轨迹截断）。填写具体中文 `rejection_reason`，不要猜测凑数。

## episode 语义（最重要）

1. **初始失败 = 任务级**：选定第一个构成任务级失败的动作（tool_call）及其直接结果。它是整个轨迹要解决的那个失败。不要把探索期的局部小失败（如 `pytest: No module named pytest`）当成锚点；也不要把修复过程中产生的局部子失败当成锚点。
2. **error_signature**：从初始失败结果的 JSON 序列化文本中**逐字复制**的最小稳定错误签名（≤120 字符）。复制 dossier 里 `C:` 行中的连续子串，不要自己改写、不要跨行拼接换行。
3. **repair_steps** 按时间排列：每步 = 一个修复阶段。`action_source_event_id` 必须是 tool_call（通常是编辑或命令）；`decision_source_event_ids` 是该步之前的决策证据（如 think 记录、诊断观察），可为空；`result_source_event_ids` 是该步之后能说明该步效果的结果（含验证运行）。只有最后一步 `outcome=resolved` 且其结果中有最终成功的直接观察；其余步 `outcome=intermediate_failure`。
4. **resolution_source_event_ids** ⊆ 最后一步的结果。
5. **required_core_source_event_ids**：首选最小闭环（≥4 条，按时间排序）：初始动作、初始失败结果、关键修复动作、关键效果、最终成功观察。
6. **optional_support_source_event_ids**：有帮助但非必需（与 core 不相交）。
7. 三个 policy（`chain_policy`、`query_policies.audit_recovery`、`query_policies.interactive_reacquisition`）：
   - `relevant_evidence_ids`：该 policy 关心的全部相关事件。**三者并集必须覆盖 episode 中引用过的所有事件**（初始、各步、resolution、core、optional）。最稳妥做法：`chain_policy.relevant` = 全部被引用事件。
   - `alternative_evidence_sets`：≥1 个合法最小证据集合（每个非空、彼此不同、⊆ relevant）。第一个放 required_core（chain_policy）或其领域等价物。
   - `causal_paths`：与 alternative 一一对应；每条 `evidence_ids` 的集合 = 对应 alternative 的集合；`constraints` 是 [前因, 后果] 边，两端都在该 path 内且前因严格早于后果。单元素集合的 path 用空 `constraints`。
   - `audit_recovery` 聚焦"如何被修复"的证据；`interactive_reacquisition` 聚焦"事后可用工具重获"的事件（如重新查看文件、重跑测试）。

## recoverability 定级（按"初始失败事实能否在当前环境用工具重获"）

- R0：事实只在历史里（状态已被修复改变，且无法只读复现）。
- R1：一次只读调用即可重获。
- R2：需要多步环境检查/复现/日志调查才能重建。
- R3：原动作不可安全重复（有副作用/非幂等），重获只能模拟。

## failure_family

简短 snake_case 失败类别。优先沿用受控词表（shell_syntax、parameter_schema、permission_policy、stale_state、dependency_version、patch_test、path_environment、timeout_resource、partial_side_effect、multi_failure_recovery）；不贴切时自造精确类别（如 `token_invalidation_logic_error`、`missing_import`、`wrong_api_usage`）。

## 文本字段

`diagnostic_evidence`、`recovery_sequence`、`resolution_evidence`、`semantic_change` 用中文，具体、引用事件 id，不空话。

## 工作流程（每个案例）

1. 读 dossier 全文，理解任务与轨迹走向；`!!` 标记是可疑失败结果。
2. 定位初始任务级失败与最终成功观察；用 `python -c "import anno_lib as A; A.show('<cid>', '<eid>')"` 或 `A.show_range('<cid>', start, end)` 查看完整文本，确认 error_signature 逐字可用。
3. 构造 episode dict（照抄 `exemplar_case001.py` 的结构与风格），调用：
   ```python
   import anno_lib as A
   A.write_row(r'<你的输出文件>', '<candidate_id>', annotator='ai-draft:zcode:glm-5.2:pass-a',
               status='annotated', episode=EPISODE)
   ```
4. 全部写完后运行 `python anno_lib.py <你的输出文件>` 自检；修复所有 ERROR 直到 0 错误。
5. 最终回复：一段话汇报 annotated/rejected 数量、reject 理由摘要、不确定的案例。不要在回复里粘贴标注 JSON。

## 机械规则速查（校验器强制）

anchor==initial 且为 tool_call；初始结果晚于初始动作；signature 是初始结果 dump 的逐字子串；步与步之间动作严格晚于上一步最大结果；步内 decision<action<result；仅末步 resolved；resolution⊆末步结果；core≥4 且时间有序；core∩optional=∅；每 policy：relevant 非空、alternatives 非空且互异、path 与 alternative 一一对应且集合相等、边严格保序、无重复边；三 policy relevant 并集 ⊇ 全部被引用事件。
