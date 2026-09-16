# AI 标注工作指南 — compression_audit_v1 reviewer A（案例级任务）

你在为一个 SWE agent 轨迹 benchmark 标注"失败→诊断→切换→成功"因果链。本文件是唯一规范；先完整读完再动手。

## 0. 输入与输出

- 工作目录：`E:\科研\Tools Tracing\data\compression_audit\real_annotations_v1\_work_ai_a`
- 轨迹数据：`..\_packet\reviewer_a\cases.jsonl`（只通过下面的脚本读取；不要读 candidates.jsonl，不要读 reviewer_b，不要参考任何启发式提示）
- 摘要文件已生成：`digests/case_<idx:03d>_<source>_<task>.txt`（每个事件一行，`!!!ERR!!!` 标记疑似错误观察）

脚本命令（在 `_work_ai_a` 目录下运行）：

```
python case_tools.py digest <idx>                     # 打印整条摘要（也可直接 Read digest 文件）
python case_tools.py zoom <idx> <eid1> <eid2> ...     # 打印指定事件完整内容
python case_tools.py args <idx> <eid> ...             # 打印动作事件的解析参数 dict（可直接用于 *_arguments 字段）
python case_tools.py meta                             # 案例索引：idx, source, repo, task, split, n_events, candidate_id 前缀
```

事件 ID 形如 `t0044:action`（AMA）或 `m0044:call:call_XYZ` / `m0045:result:call_XYZ` / `m0044:message`（SWE-Gym）。含冒号，shell 里记得加引号。

**输出**：把你负责的每个案例一行写成 JSON，追加写入 `annotations/batch_<lo>_<hi>.jsonl`（UTF-8，无多余空格无所谓）。行格式二选一：

```json
{"case_index": 12, "candidate_id": "<原样复制>", "annotation_status": "annotated", "failure_chain": {…完整链…}}
{"case_index": 12, "candidate_id": "<原样复制>", "annotation_status": "rejected", "reject_reason": "英文一句话说明为什么无法证实完整链"}
```

## 1. 任务：找一条完整因果链

在轨迹里找**一条**最清晰、最有代表性的链，六要素齐全：

失败动作(failed_action) → 失败结果(failure_result，含错误签名) → 诊断证据(diagnostic_evidence) → 切换决定(switch_decision) → 替代动作(replacement_action) → 成功证据(resolution_evidence)。

- 链不必是轨迹里最大的事件，但六个环节必须都真实存在、有事件可引。
- 优先选"失败后改变了方法/参数/目标并取得可验证成功"的链；**原样重试同一命令不算切换**（若同一策略重复失败 ≥2 次后才换，可用 multi_failure_recovery 族）。
- 微链也算：工具参数错误 → 工具报错 → 去掉错误参数重发 → 成功，这是完整的 parameter_schema 链（见 §5 例 B）。前提是成功证据真实可见。
- AMA 轨迹中 agent 的"思考"是 `think:` 动作事件（`tNNNN:action`），它可作 diagnostic/switch 证据。SWE-Gym 的思考在 `assistant_message` 事件（`mNNNN:message`）。
- 若整个轨迹找不到六要素齐全的链（例如轨迹被截断、无成功证据、只有原样重试），**必须 rejected，不许编**。reject_reason 用英文写清缺哪一环。

选链优先级：与任务目标直接相关、后果最重的链 > 中间步骤的微链。多个都完整时，选证据最清晰可引的。

## 2. failure_chain 字段规范（全部英文填写）

| 字段 | 要求 |
|---|---|
| `failure_family` | 从 §4 分类表选一个代码，不许自创 |
| `failed_action` | 实际失败的动作名：AMA 用解析出的内层动词（`execute_bash`/`str_replace_editor`/`think`/`finish`…），SWE-Gym 用 `tool_name`（`execute_bash`/`str_replace_editor`） |
| `failed_arguments` | 失败动作的真实参数 dict：直接用 `python case_tools.py args <idx> <eid>` 的输出（含 `_verb` 键也要保留） |
| `error_signature` | 稳定、最小、机器可比的一行：Python 异常取 `<ExcType>: <首行消息>`；测试失败取断言消息或 `FAILED (failures=N)`；shell 错误取首个稳定错误行；去掉时间戳、随机 token、具体数值路径可保留 |
| `diagnostic_evidence` | 1-4 句英文：失败原因是什么、轨迹里哪些观察支持这个判断（引用具体观察内容，不要泛泛而谈） |
| `switch_decision` | 1-2 句英文：agent 为什么停止原方法、决定改用什么 |
| `replacement_action` | 替代动作名（规则同 failed_action） |
| `replacement_arguments` | 替代动作的真实参数 dict（同 failed_arguments 取法） |
| `resolution_evidence` | 1-3 句英文：哪个观察证明替代成功（测试 OK/exit 0/文件创建成功/输出了期望结果），尽量带可核对细节 |
| `recoverability` | R0/R1/R2/R3，按 §4 规则 |
| `ordered_source_event_ids` | 链上关键事件 ID 列表，**≥5 个、不重复、全部存在于该案例 prefix.events、按轨迹顺序排列**。典型组成：失败动作、失败观察、诊断证据、切换证据、替代动作、成功观察 |
| `evidence_source_event_ids_by_field` | dict，**必须包含这 6 个键且值非空**：`failed_action`, `failure_cause`, `diagnostic_evidence`, `switch_decision`, `replacement_action`, `resolution_evidence`；另建议加 `failure_result`（失败观察）和 `error_signature`。每个键的值是事件 ID 数组；同一事件可服务多个字段；所有 ID 必须存在于 prefix.events |

注意：
- `failure_cause`（根因证据）≠ `failed_action`（失败的动作本身）。根因写在 failure_cause 指向的证据里。
- 事件 ID 必须逐字符精确（SWE 的含 call hash）。
- 除上述字段外不要增删 failure_chain 的键（保留模板里的全部 12 个键）。
- 不要修改 prefix.events、不要给事件标 causal_role；模板行里的其他元数据（candidate_id、split 等）原样保留。

## 3. 状态与身份

- 找到完整链：`annotation_status: "annotated"`。
- 无法证实：`"rejected"` + `reject_reason`。猜测、降级凑数都禁止。
- `annotator` 字段由组装阶段统一填，你不用写。

## 4. failure_family 分类表（选最贴切的一个）

| 代码 | 定义 | 典型信号 |
|---|---|---|
| `shell_syntax` | 命令语法错、用错 shell/工具语法、命令拼写错 | `command not found`、bash 语法错误、把 A 工具语法用在 B 上 |
| `parameter_schema` | 参数缺失/非法/类型或组合错误，或 API/工具签名用错 | 工具报 `not allowed`/`Invalid`/`required`、TypeError: missing argument、pytest 用错 flag |
| `permission_policy` | 权限/认证/作用域被拒 | `Permission denied`、403、auth error |
| `stale_state` | 基于过时状态行动：文件已被改/缓存过期/版本与假设不符 | 编辑冲突、旧输出、改动前后状态不一致 |
| `dependency_version` | 依赖版本不符：API 被移除/改名、模块缺失、版本不满足 | `No module named X`、ImportError、`attribute removed` |
| `patch_test` | 代码修改本身未达成目标：改后测试仍失败、补丁不完整、改错位置在正确文件内 | 改完后 rerun 仍 FAIL、fix 遗漏分支 |
| `path_environment` | 路径/环境错位：改了错的文件副本、路径不存在、环境变量/解释器/工作目录不对 | `No such file or directory`、改了 /workspace 而测试用 /testbed、wrong interpreter |
| `timeout_resource` | 超时或资源耗尽 | timeout、OOM、killed |
| `partial_side_effect` | 动作已产生部分副作用且不可简单重来 | 部分提交、残留文件、重复执行有害 |
| `multi_failure_recovery` | 同一策略重复失败 ≥2 次后才切换 | 相同错误签名出现多次 |

判定顺序建议：先看是不是 parameter_schema / path_environment / dependency_version 这类"调用方式错"；再看是不是 patch_test（改了但没修对）；同签名重复失败用 multi_failure_recovery；都贴不上才考虑其他。

## 5. recoverability 判定规则

问：轨迹冻结后，要把这条链的失败事实（错误签名、失败现场）重新拿回来，代价是什么？

- **R0**：环境不可用（所有 AMA 案例 `docker_replayable=false` 一律 R0），或失败依赖的瞬态已消失、即使有环境也无法复现（一次性状态、已被后续动作覆盖且不可逆）。
- **R1**：环境里一条只读命令即可复现同样的失败观察（重跑同一命令立刻复现，不改状态）。
- **R2**：复现需要多步（先恢复中间状态/先重建文件再跑、多次调查）。
- **R3**：失败动作本身已产生实质副作用，原样重复不安全或不可重复（删除/覆盖/提交类命令已执行）。

SWE 案例默认问自己：在初始快照里重放"失败动作+其依赖的中间状态"是否一步可成？是→R1；要多步→R2。只读且快照可复现的微链多为 R1。

## 6. 样例 A（AMA，标准链，已精标）

案例 0（django，改错代码树）。轨迹：agent 把 email 修复改到 /workspace 副本，runtests 仍失败；查明测试实际 import /testbed/django 且其 tokens.py 仍是旧版；改 /testbed 后测试全过。

```json
{
  "case_index": 0,
  "candidate_id": "2180355931c9563fc692c4ae0a957bdb411cfd379a742c20ee6f6d04f187f02a",
  "annotation_status": "annotated",
  "failure_chain": {
    "failure_family": "path_environment",
    "failed_action": "execute_bash",
    "failed_arguments": {"_verb": "execute_bash", "command": "cd /workspace/django__django__3.2/tests && python runtests.py auth_tests.test_email_invalidation -v 2"},
    "error_signature": "AssertionError: True is not false (FAILED (failures=3), exit code 1)",
    "diagnostic_evidence": "After the fix was applied, the Django test runner still reports the three email-change token tests failing with 'AssertionError: True is not false' while standalone scripts pass. A python -c probe prints 'Django location: /testbed/django/__init__.py', and viewing /testbed/django/contrib/auth/tokens.py lines 79-97 shows _make_hash_value still returns str(user.pk) + user.password + str(login_timestamp) + str(timestamp) with no email term, proving the edit had only been applied to the unused /workspace/django__django__3.2 copy.",
    "switch_decision": "Stop iterating on the /workspace checkout: runtests.py imports Django from /testbed/django, so the same email-hash change must be reapplied directly to /testbed/django/contrib/auth/tokens.py.",
    "replacement_action": "str_replace_editor",
    "replacement_arguments": {"_verb": "str_replace_editor", "command": "str_replace", "path": "/testbed/django/contrib/auth/tokens.py", "old_str": "<完整 old_str>", "new_str": "<完整 new_str>"},
    "resolution_evidence": "Rerunning 'python runtests.py auth_tests.test_email_invalidation -v 2' reports test_token_invalidated_on_case_change, test_token_invalidated_on_email_change and test_token_invalidated_on_empty_to_real_email_change all ok, 'Ran 4 tests', 'OK', exit code 0 (previously FAILED (failures=3)).",
    "recoverability": "R0",
    "ordered_source_event_ids": ["t0044:action", "t0044:observation", "t0045:action", "t0052:observation", "t0054:observation", "t0055:action", "t0056:observation"],
    "evidence_source_event_ids_by_field": {
      "failed_action": ["t0044:action"],
      "failure_result": ["t0044:observation"],
      "failure_cause": ["t0052:observation", "t0054:observation"],
      "error_signature": ["t0044:observation"],
      "diagnostic_evidence": ["t0044:observation", "t0045:action", "t0052:observation", "t0054:observation"],
      "switch_decision": ["t0054:observation", "t0055:action"],
      "replacement_action": ["t0055:action"],
      "resolution_evidence": ["t0056:observation"]
    }
  }
}
```

（replacement_arguments 里 <完整 old_str>/<完整 new_str> 必须用 `python case_tools.py args 0 t0055:action` 的真实全文替换，不许缩写。）

## 7. 样例 B（SWE-Gym，微链，已精标）

案例 9（modin，9 个事件）。view 目录时带了非法 view_range → 工具报错 → 去掉该参数重试 → 列目录成功。这是完整的 parameter_schema 微链。

```json
{
  "case_index": 9,
  "candidate_id": "140c1e0629907a5c515bd4c121171dbe49f0cabc6d460bd0be232e2b19ef1f1d",
  "annotation_status": "annotated",
  "failure_chain": {
    "failure_family": "parameter_schema",
    "failed_action": "str_replace_editor",
    "failed_arguments": {"command": "view", "path": "/workspace/modin-project__modin__0.25", "view_range": [1, 50]},
    "error_signature": "ERROR: The `view_range` parameter is not allowed when `path` points to a directory.",
    "diagnostic_evidence": "The tool rejects the call outright with a schema error: 'ERROR: The `view_range` parameter is not allowed when `path` points to a directory.' The earlier plain view of the same path (m0003) succeeded without view_range, isolating view_range as the only invalid argument.",
    "switch_decision": "Drop the disallowed view_range argument and repeat the directory view with the path alone.",
    "replacement_action": "str_replace_editor",
    "replacement_arguments": {"command": "view", "path": "/workspace/modin-project__modin__0.25"},
    "resolution_evidence": "The retried view succeeds and returns the two-level directory listing of /workspace/modin-project__modin__0.25.",
    "recoverability": "R1",
    "ordered_source_event_ids": ["m0002:call:call_93tjbOSNoOZNhoJqbupdHKKe", "m0003:result:call_93tjbOSNoOZNhoJqbupdHKKe", "m0004:call:call_dnlCOlKmafxjO1GfzAWpCo7h", "m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h", "m0006:call:call_qkjevQKEcMr1XGlh9YLULddt", "m0007:result:call_qkjevQKEcMr1XGlh9YLULddt"],
    "evidence_source_event_ids_by_field": {
      "failed_action": ["m0004:call:call_dnlCOlKmafxjO1GfzAWpCo7h"],
      "failure_result": ["m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h"],
      "failure_cause": ["m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h"],
      "error_signature": ["m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h"],
      "diagnostic_evidence": ["m0003:result:call_93tjbOSNoOZNhoJqbupdHKKe", "m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h"],
      "switch_decision": ["m0006:call:call_qkjevQKEcMr1XGlh9YLULddt"],
      "replacement_action": ["m0006:call:call_qkjevQKEcMr1XGlh9YLULddt"],
      "resolution_evidence": ["m0007:result:call_qkjevQKEcMr1XGlh9YLULddt"]
    }
  }
}
```

若链核心事件不足 5 个，像上面这样把邻近的真实上下文事件（如先前的成功调用）纳入 diagnostic_evidence 并加进 ordered 列表；绝不允许编造或复制 ID。若仍凑不齐 5 个真实事件，宁可 rejected。

## 8. 工作流程（每个案例）

1. Read 摘要文件（digests/ 下），通读全文定位错误窗口（`!!!ERR!!!`）与最终成功证据。
2. 判断六要素是否齐全；确定链与 family/R 等级。
3. zoom 关键事件读全文（失败观察、诊断思考、切换前后、成功观察），必要时 zoom 上下文确认没有"原样重试"陷阱。
4. `args` 取失败/替代动作参数原文。
5. 写一行 JSON 到 `annotations/batch_<lo>_<hi>.jsonl`（不存在则创建，追加）。
6. 输出前自检：ID 精确存在于摘要中、ordered ≥5 且按轨迹顺序、六个 evidence 键齐全、参数 dict 与 args 输出一致、family 在 §4 表内、R 在 R0-R3 内。

节奏建议：短案例（<40 事件）10-15 分钟级；长案例（>200 事件）先扫 `!!!ERR!!!` 再精读窗口。质量优先：不确定就 rejected，并在 reject_reason 里说清楚。
