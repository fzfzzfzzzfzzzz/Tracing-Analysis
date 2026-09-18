# -*- coding: utf-8 -*-
"""Pass-A batch_01 annotation (cases 002-006), built per instructions_pass_a.md."""
import anno_lib as A

OUT = A.WORK / "pass_a" / "batch_01.jsonl"
ANNO = "ai-draft:zcode:glm-5.2:pass-a"


def rel(cid, ids):
    """Chronologically sort a set of event ids by their position in the case prefix."""
    order = {e["source_event_id"]: i for i, e in enumerate(A.events(cid))}
    return sorted(set(ids), key=lambda x: order[x])


# ---------------------------------------------------------------- case 002
CID2 = "333b2a9bfb7bd62629746c0dfb3d2488b5f8cdbe60b04213d917328685183841"
EP2 = {
    "scope": "task_level_failure_episode",
    "failure_family": "alter_field_noop_detection_logic_error",
    "recoverability": "R2",
    "error_signature": "❌ ISSUE: Django thinks the field should be altered when only choices changed",
    "diagnostic_evidence": (
        "t0031:observation 显示复现脚本输出 Should alter field when adding choices: True 并列出建新表/"
        "INSERT/DROP/RENAME 的 SQL，即 SQLite 上仅改 choices 的 AlterField 未被判为 no-op（❌ ISSUE），构成任务级初始失败。"
        "t0021:action 的诊断思考与 t0032:observation 对 django/db/backends/base/schema.py 中 "
        "_field_should_be_altered（non_database_attrs，第1372-1398行）的查看定位根因：choices 不在 non_database_attrs "
        "列表中，仅 choices 差异也被判为需要 ALTER。"
    ),
    "recovery_sequence": (
        "1) 在 base/schema.py 的 non_database_attrs 中加入 choices（t0033:action），重跑复现脚本变为 False/GOOD"
        "（t0034:observation）；2) 新建综合脚本验证各 choices 场景为 no-op、真实 schema 变更仍生效"
        "（t0035:action→t0036:observation）；3) 官方 schema.tests 175 项通过（t0037:action）；"
        "4) migrations.test_operations 121 项与 test_alter_field_fk_attributes_noop 通过（t0038:action→t0039:observation）；"
        "5) 迁移级验证脚本首跑因 test_app 未注册报错（t0040:action→t0041:observation）；"
        "6) 修正脚本 app 注册方式并补充真实变更用例（t0042:action、t0043:action）；"
        "7) 重跑显示 choices-only 不生成 SQL、真实变更有 SQL（t0044:observation）；"
        "8) migrations.test_autodetector 143 项 OK（t0045:observation）；"
        "9) SQLite 后端套件 18 项 OK（skipped=2）（t0046:action→t0046:observation）。"
    ),
    "resolution_evidence": (
        "t0046:observation 显示官方 runtests 复跑 backends.sqlite.tests 得到 Ran 18 tests / OK (skipped=2)、"
        "exit code 0；配合 t0044:observation 中迁移级三场景 SUCCESS（choices-only 无 SQL、真实变更仍生成 SQL），"
        "确认任务级修复生效且无回归。"
    ),
    "anchor_source_event_id": "t0031:action",
    "initial_action_source_event_id": "t0031:action",
    "initial_result_source_event_ids": ["t0031:observation"],
    "repair_steps": [
        {
            "step_id": "add-choices-to-non-db-attrs",
            "decision_source_event_ids": ["t0032:observation"],
            "action_source_event_id": "t0033:action",
            "result_source_event_ids": ["t0033:observation", "t0034:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 _field_should_be_altered 的 non_database_attrs 中加入 choices，重跑复现脚本输出 False/✅ GOOD，但尚未用官方套件回归。",
        },
        {
            "step_id": "comprehensive-repro-tests",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0035:action",
            "result_source_event_ids": ["t0035:observation", "t0036:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "新建综合脚本覆盖添加/删除/修改 choices 及真实 schema 变更场景，结果各 choices 场景 no-op、真实变更仍生效，但官方套件未验证。",
        },
        {
            "step_id": "official-schema-suite",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0037:action",
            "result_source_event_ids": ["t0037:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "官方 runtests 复跑 schema.tests 共 175 项（exit code 0），未见回归，但迁移级行为尚未直接验证。",
        },
        {
            "step_id": "official-migration-suites",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0038:action",
            "result_source_event_ids": ["t0038:observation", "t0039:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "migrations.test_operations 121 项与 test_alter_field_fk_attributes_noop 通过，确认既有迁移/noop 行为未破坏。",
        },
        {
            "step_id": "migration-script-first-run",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0040:action",
            "result_source_event_ids": ["t0040:observation", "t0041:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "创建迁移级验证脚本并首跑，因 No installed app with label 'test_app' 报错退出，未能验证迁移行为。",
        },
        {
            "step_id": "fix-migration-script",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0042:action",
            "result_source_event_ids": ["t0042:observation", "t0043:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "修正脚本的 app 注册方式（t0042:action）并补充真实 schema 变更用例（t0043:action），两次编辑均确认成功，尚未复跑。",
        },
        {
            "step_id": "rerun-migration-script",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0044:action",
            "result_source_event_ids": ["t0044:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "重跑迁移脚本显示 choices-only 及反向移除 choices 均无 SQL、真实变更仍生成 SQL（三场景 ✅ SUCCESS），但官方套件最终回归未完成。",
        },
        {
            "step_id": "autodetector-regression",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0045:action",
            "result_source_event_ids": ["t0045:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "migrations.test_autodetector 143 项 OK（--keepdb），迁移自动检测无回归。",
        },
        {
            "step_id": "sqlite-backend-suite",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0046:action",
            "result_source_event_ids": ["t0046:observation"],
            "outcome": "resolved",
            "semantic_change": "最终复跑 backends.sqlite.tests，Ran 18 tests / OK (skipped=2)，任务级修复在官方测试运行时确认。",
        },
    ],
    "resolution_source_event_ids": ["t0046:observation"],
    "required_core_source_event_ids": [
        "t0031:action", "t0031:observation", "t0033:action", "t0034:observation",
        "t0044:observation", "t0046:action", "t0046:observation",
    ],
    "optional_support_source_event_ids": [
        "t0021:action", "t0032:observation", "t0033:observation", "t0035:action",
        "t0035:observation", "t0036:observation", "t0037:action", "t0037:observation",
        "t0038:action", "t0038:observation", "t0039:action", "t0039:observation",
        "t0040:action", "t0040:observation", "t0041:observation", "t0042:action",
        "t0042:observation", "t0043:action", "t0043:observation", "t0045:action",
        "t0045:observation",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "t0031:action", "t0031:observation", "t0033:action", "t0034:observation",
                "t0044:observation", "t0046:action", "t0046:observation",
            ],
            [
                "t0031:action", "t0031:observation", "t0033:action", "t0034:observation",
                "t0046:observation",
            ],
        ],
        "relevant_evidence_ids": rel(CID2, [
            "t0021:action", "t0031:action", "t0031:observation", "t0032:observation",
            "t0033:action", "t0033:observation", "t0034:observation", "t0035:action",
            "t0035:observation", "t0036:observation", "t0037:action", "t0037:observation",
            "t0038:action", "t0038:observation", "t0039:action", "t0039:observation",
            "t0040:action", "t0040:observation", "t0041:observation", "t0042:action",
            "t0042:observation", "t0043:action", "t0043:observation", "t0044:action",
            "t0044:observation", "t0045:action", "t0045:observation", "t0046:action",
            "t0046:observation",
        ]),
        "causal_paths": [
            {
                "evidence_ids": [
                    "t0031:action", "t0031:observation", "t0033:action", "t0034:observation",
                    "t0044:observation", "t0046:action", "t0046:observation",
                ],
                "constraints": [
                    ["t0031:action", "t0031:observation"],
                    ["t0031:observation", "t0033:action"],
                    ["t0033:action", "t0034:observation"],
                    ["t0034:observation", "t0044:observation"],
                    ["t0044:observation", "t0046:action"],
                    ["t0046:action", "t0046:observation"],
                ],
            },
            {
                "evidence_ids": [
                    "t0031:action", "t0031:observation", "t0033:action", "t0034:observation",
                    "t0046:observation",
                ],
                "constraints": [
                    ["t0031:action", "t0031:observation"],
                    ["t0031:observation", "t0033:action"],
                    ["t0033:action", "t0034:observation"],
                    ["t0034:observation", "t0046:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                ["t0033:action", "t0034:observation", "t0046:observation"],
                ["t0033:action", "t0044:observation", "t0046:action", "t0046:observation"],
            ],
            "relevant_evidence_ids": rel(CID2, [
                "t0032:observation", "t0033:action", "t0033:observation", "t0034:observation",
                "t0035:action", "t0036:observation", "t0037:action", "t0037:observation",
                "t0040:action", "t0041:observation", "t0042:action", "t0043:action",
                "t0044:observation", "t0045:action", "t0045:observation", "t0046:action",
                "t0046:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0033:action", "t0034:observation", "t0046:observation"],
                    "constraints": [
                        ["t0033:action", "t0034:observation"],
                        ["t0034:observation", "t0046:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0033:action", "t0044:observation", "t0046:action", "t0046:observation"],
                    "constraints": [
                        ["t0033:action", "t0044:observation"],
                        ["t0044:observation", "t0046:action"],
                        ["t0046:action", "t0046:observation"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0032:action", "t0032:observation", "t0046:action", "t0046:observation"],
                ["t0037:action", "t0046:observation"],
            ],
            "relevant_evidence_ids": rel(CID2, [
                "t0031:action", "t0031:observation", "t0032:action", "t0032:observation",
                "t0037:action", "t0037:observation", "t0046:action", "t0046:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0032:action", "t0032:observation", "t0046:action", "t0046:observation"],
                    "constraints": [
                        ["t0032:action", "t0032:observation"],
                        ["t0032:observation", "t0046:action"],
                        ["t0046:action", "t0046:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0037:action", "t0046:observation"],
                    "constraints": [["t0037:action", "t0046:observation"]],
                },
            ],
        },
    },
}

# ---------------------------------------------------------------- case 003
CID3 = "4fd98262cfefc35521e9fa7b682b24f22b5e2a8e91f7b26d2ea1c458faf42201"
EP3 = {
    "scope": "task_level_failure_episode",
    "failure_family": "codegen_signature_type_logic_error",
    "recoverability": "R1",
    "error_signature": "✗ FAIL: TypeError: only length-1 arrays can be converted to Python scalars",
    "diagnostic_evidence": (
        "t0028:observation 首次运行复现脚本即出现 ✗ FAIL: TypeError: only length-1 arrays can be converted to "
        "Python scalars：cython 后端 autowrap 在 MatrixSymbol 参数未出现在表达式中时把数组参数按标量生成 C 签名。"
        "t0029:action 的修复思考、t0077:observation（该参数 NOT found in name_arg_dict，将被创建为无 dimensions 的 "
        "InputArgument）把问题定位到 codegen.py CodeGen.routine 的 argument_sequence 分支；t0196:observation 进一步"
        "揭示复现时实际导入的是 /testbed/sympy 而非被修改的 /workspace 副本，解释了前期补丁看似不生效的现象。"
    ),
    "recovery_sequence": (
        "1) 在 codegen.py 的 KeyError 分支为 MatrixSymbol 附加 dimensions 并经 sed 推广到各语言生成器"
        "（t0033:action→t0064:observation 确认 4 处在位）；2) 复现脚本重跑仍 ✗ FAIL（t0065:observation）；"
        "3) 多轮调试后回滚调试代码并以 sed 重打干净补丁，复现仍失败（t0184:action→t0188:observation）；"
        "4) debug_autowrap 揭示导入自 /testbed，将 workspace 副本置于 sys.path 首位后 C 原型变为 double *x"
        "（t0196:action→t0199:observation）；5) 复现脚本加入 sys.path.insert 后原始用例 PASS、Result: 1.0"
        "（t0200:action→t0201:observation）；6) 综合脚本 6 类场景全部 PASS（t0206:action→t0207:observation）；"
        "7) sympy.test 运行 test_autowrap.py 12 项全部通过（t0209:observation）；"
        "8) test_codegen.py 55 项全部通过（t0210:action→t0210:observation）。"
    ),
    "resolution_evidence": (
        "t0210:observation 显示 sympy/utilities/tests/test_codegen.py[55] 全部用例通过（55 个通过标记、exit code 0），"
        "配合 t0209:observation 的 test_autowrap.py[12] 通过与 t0201:observation 的原始用例 ✓ PASS，确认修复有效且无回归。"
    ),
    "anchor_source_event_id": "t0028:action",
    "initial_action_source_event_id": "t0028:action",
    "initial_result_source_event_ids": ["t0028:observation"],
    "repair_steps": [
        {
            "step_id": "apply-matrixsymbol-dims-fix",
            "decision_source_event_ids": ["t0029:action"],
            "action_source_event_id": "t0033:action",
            "result_source_event_ids": ["t0033:observation", "t0064:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 CodeGen.routine 的 argument_sequence 分支为 MatrixSymbol 附加 dimensions 元数据（t0051/t0058/t0063 的 sed 推广到 Julia/Octave/Rust，t0064 确认 4 处在位），但尚未端到端验证。",
        },
        {
            "step_id": "rerun-repro-still-fails",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0065:action",
            "result_source_event_ids": ["t0065:observation", "t0067:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "清理 pyc 后重跑复现脚本仍 ✗ FAIL: TypeError，修复未在运行时生效（原因当时未知）。",
        },
        {
            "step_id": "reset-and-clean-reapply",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0184:action",
            "result_source_event_ids": ["t0184:observation", "t0185:observation", "t0186:observation", "t0188:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "git checkout 回滚 codegen.py/autowrap.py 中累积的调试改动后，用 sed 重新应用干净的 MatrixSymbol dimensions 修复（t0185），grep 确认补丁在位，但复现脚本仍失败。",
        },
        {
            "step_id": "discover-testbed-import",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0196:action",
            "result_source_event_ids": ["t0196:observation", "t0197:observation", "t0199:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "定位关键根因：复现进程导入的 codegen 模块来自 /testbed/sympy 而非被修补的 /workspace 副本（t0196、t0197 的 sys.path）；将 workspace 副本置于 sys.path 首位后 C 原型变为 double autofunc(double *x)，证明修复逻辑本身有效（t0199）。",
        },
        {
            "step_id": "repoint-repro-to-workspace",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0200:action",
            "result_source_event_ids": ["t0200:observation", "t0201:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "复现脚本加入 sys.path.insert(0, '/workspace/sympy__sympy__1.5') 后重跑，原始用例 ✓ PASS、Result: 1.0，但综合与官方验证尚未完成。",
        },
        {
            "step_id": "comprehensive-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0206:action",
            "result_source_event_ids": ["t0206:observation", "t0207:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "新建综合脚本覆盖未出现/出现/多个/混合标量与矩阵/不同形状/方阵 6 类场景，全部 ✓ PASS，官方套件未跑。",
        },
        {
            "step_id": "official-autowrap-suite",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0209:action",
            "result_source_event_ids": ["t0209:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "sympy.test 运行 test_autowrap.py，12 项全部通过（pytest 缺失后改用 sympy.test），codegen 套件尚未验证。",
        },
        {
            "step_id": "official-codegen-suite",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0210:action",
            "result_source_event_ids": ["t0210:observation"],
            "outcome": "resolved",
            "semantic_change": "sympy.test 运行 test_codegen.py，55 项全部通过，任务级修复经官方套件确认。",
        },
    ],
    "resolution_source_event_ids": ["t0210:observation"],
    "required_core_source_event_ids": [
        "t0028:action", "t0028:observation", "t0033:action", "t0199:observation",
        "t0201:observation", "t0210:action", "t0210:observation",
    ],
    "optional_support_source_event_ids": [
        "t0029:action", "t0033:observation", "t0064:observation", "t0065:action",
        "t0065:observation", "t0067:observation", "t0184:action", "t0184:observation",
        "t0185:action", "t0185:observation", "t0186:action", "t0186:observation",
        "t0188:action", "t0188:observation", "t0196:action", "t0196:observation",
        "t0197:observation", "t0200:action", "t0200:observation", "t0206:action",
        "t0206:observation", "t0207:observation", "t0209:action", "t0209:observation",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "t0028:action", "t0028:observation", "t0033:action", "t0199:observation",
                "t0201:observation", "t0210:action", "t0210:observation",
            ],
            [
                "t0028:action", "t0028:observation", "t0033:action", "t0201:observation",
                "t0210:observation",
            ],
        ],
        "relevant_evidence_ids": rel(CID3, [
            "t0028:action", "t0028:observation", "t0029:action", "t0033:action",
            "t0033:observation", "t0064:observation", "t0065:action", "t0065:observation",
            "t0067:observation", "t0184:action", "t0184:observation", "t0185:action",
            "t0185:observation", "t0186:action", "t0186:observation", "t0188:action",
            "t0188:observation", "t0196:action", "t0196:observation", "t0197:observation",
            "t0199:observation", "t0200:action", "t0200:observation", "t0201:observation",
            "t0206:action", "t0206:observation", "t0207:observation", "t0209:action",
            "t0209:observation", "t0210:action", "t0210:observation",
        ]),
        "causal_paths": [
            {
                "evidence_ids": [
                    "t0028:action", "t0028:observation", "t0033:action", "t0199:observation",
                    "t0201:observation", "t0210:action", "t0210:observation",
                ],
                "constraints": [
                    ["t0028:action", "t0028:observation"],
                    ["t0028:observation", "t0033:action"],
                    ["t0033:action", "t0199:observation"],
                    ["t0199:observation", "t0201:observation"],
                    ["t0201:observation", "t0210:action"],
                    ["t0210:action", "t0210:observation"],
                ],
            },
            {
                "evidence_ids": [
                    "t0028:action", "t0028:observation", "t0033:action", "t0201:observation",
                    "t0210:observation",
                ],
                "constraints": [
                    ["t0028:action", "t0028:observation"],
                    ["t0028:observation", "t0033:action"],
                    ["t0033:action", "t0201:observation"],
                    ["t0201:observation", "t0210:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                ["t0033:action", "t0201:observation", "t0210:observation"],
                ["t0185:action", "t0196:observation", "t0201:observation", "t0210:observation"],
            ],
            "relevant_evidence_ids": rel(CID3, [
                "t0029:action", "t0033:action", "t0033:observation", "t0064:observation",
                "t0065:observation", "t0184:action", "t0185:action", "t0188:observation",
                "t0196:observation", "t0197:observation", "t0199:observation", "t0200:action",
                "t0201:observation", "t0206:action", "t0207:observation", "t0209:observation",
                "t0210:action", "t0210:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0033:action", "t0201:observation", "t0210:observation"],
                    "constraints": [
                        ["t0033:action", "t0201:observation"],
                        ["t0201:observation", "t0210:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0185:action", "t0196:observation", "t0201:observation", "t0210:observation"],
                    "constraints": [
                        ["t0185:action", "t0196:observation"],
                        ["t0196:observation", "t0201:observation"],
                        ["t0201:observation", "t0210:observation"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0199:observation", "t0210:action", "t0210:observation"],
                ["t0196:action", "t0206:action", "t0207:observation"],
            ],
            "relevant_evidence_ids": rel(CID3, [
                "t0028:action", "t0028:observation", "t0196:action", "t0196:observation",
                "t0199:observation", "t0206:action", "t0207:observation", "t0210:action",
                "t0210:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0199:observation", "t0210:action", "t0210:observation"],
                    "constraints": [
                        ["t0199:observation", "t0210:action"],
                        ["t0210:action", "t0210:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0196:action", "t0206:action", "t0207:observation"],
                    "constraints": [
                        ["t0196:action", "t0206:action"],
                        ["t0206:action", "t0207:observation"],
                    ],
                },
            ],
        },
    },
}

# ---------------------------------------------------------------- case 004
CID4 = "52eaf9c8ce0ed2d3c1c8c14ea4da0b5cf2d766c47a802c93dc503aeb828b1614"
EP4 = {
    "scope": "task_level_failure_episode",
    "failure_family": "quote_escaping_logic_error",
    "recoverability": "R0",
    "error_signature": "Length 65: False",
    "diagnostic_evidence": (
        "t0005:observation 的复现脚本输出 Length 65: False（以及 67/68/69 等长度 False）：包含双单引号 '' 的长字符串经 "
        "Card 序列化再 fromstring 解析后 '' 被折叠为 '，往返不一致，构成任务级初始失败。t0022:action 的诊断思考、"
        "t0021:observation/t0027:observation 的调试与 t0025:observation 对 astropy/io/fits/card.py 第870-877行的查看"
        "定位根因：_split() 重组 CONTINUE 卡时 valuecomment 直接 ''.join(values)，未对值中的单引号做转义。"
    ),
    "recovery_sequence": (
        "1) 在 _split() 的 CONTINUE 分支加入 joined_values/escaped_values 转义（t0029:action），重跑复现脚本仍 65 False"
        "（t0030:observation）；2) 扫描显示尾部/内嵌场景在特定长度仍失败（t0036:action→t0043:observation）；"
        "3) 综合脚本显示原始失败长度 65/67/68/69 均未修复（t0050:action→t0051:observation）；"
        "4) 加调试输出后单用例 Match True 但批量脚本仍失败（t0058:action→t0061:observation）；"
        "5) final_verification 单进程批量验证显示 0/4 修复（t0071:action→t0072:observation）；"
        "6) 精确复刻 issue 的单进程循环脚本仍 65/67/68/69 False（t0074:action→t0075:observation）；"
        "7) 再次修改 CONTINUE 分支（带 Fix executed 调试输出）后单用例 65 Match True"
        "（t0076:action→t0077:observation、t0078:observation）；"
        "8) 移除调试输出（t0079:action）后按用例独立进程验证，原始 4 个失败长度全部 Match True（t0081:observation）；"
        "9) 13 个内嵌用例逐用例独立进程验证全部 Match True（t0082:action→t0082:observation）。"
    ),
    "resolution_evidence": (
        "t0082:observation 显示 55-63、65、67、68、69 共 13 个内嵌双单引号用例逐用例独立进程验证全部 Match: True，"
        "配合 t0081:observation 中原始失败长度 65/67/68/69 亦全部 Match: True，确认往返折叠问题已修复。"
    ),
    "anchor_source_event_id": "t0005:action",
    "initial_action_source_event_id": "t0005:action",
    "initial_result_source_event_ids": ["t0005:observation"],
    "repair_steps": [
        {
            "step_id": "escape-joined-values",
            "decision_source_event_ids": ["t0022:action"],
            "action_source_event_id": "t0029:action",
            "result_source_event_ids": ["t0029:observation", "t0030:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 card.py _split() 的 CONTINUE 分支为合并后的值加入单引号转义（escaped_values），编辑成功但复现脚本重跑仍报 65 False。",
        },
        {
            "step_id": "partial-fix-scan",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0036:action",
            "result_source_event_ids": ["t0036:observation", "t0043:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "逐长度扫描显示修复只对部分长度生效：字符串尾部仍有 65/67/68/69 失败，内嵌场景残留 [64, 66] 两个失败长度。",
        },
        {
            "step_id": "comprehensive-still-failing",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0050:action",
            "result_source_event_ids": ["t0050:observation", "t0051:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "综合测试脚本显示原始失败长度 65/67/68/69 依旧全部 FAIL（Fixed: set()），单进程循环视角下修复未达成。",
        },
        {
            "step_id": "debug-edit-single-case",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0058:action",
            "result_source_event_ids": ["t0058:observation", "t0059:observation", "t0061:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 CONTINUE 分支加调试输出后，独立单用例 65 显示 Match: True（转义逻辑被触发），但 final_test 批量脚本仍显示 13 例失败。",
        },
        {
            "step_id": "final-verification-loop",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0071:action",
            "result_source_event_ids": ["t0071:observation", "t0072:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "final_verification.py 单进程批量验证显示原始 4 个失败长度 ❌ STILL FAILING（0/4 cases fixed），单进程循环内状态导致结果与独立进程不一致。",
        },
        {
            "step_id": "exact-original-loop",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0074:action",
            "result_source_event_ids": ["t0074:observation", "t0075:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "精确复刻 issue 的单进程循环脚本仍输出 65/67/68/69 False，确认单进程批量视角下失败可稳定复现。",
        },
        {
            "step_id": "final-escape-edit",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0076:action",
            "result_source_event_ids": ["t0076:observation", "t0077:observation", "t0078:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "再次修改 CONTINUE 分支（带 DEBUG: Fix executed 输出）后，独立进程单用例 65 连续两次 Match: True，转义修复在新进程中生效。",
        },
        {
            "step_id": "per-case-original-verify",
            "decision_source_event_ids": ["t0080:action"],
            "action_source_event_id": "t0081:action",
            "result_source_event_ids": ["t0081:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "移除调试输出（t0079:action）后按用例各自独立进程验证，原始失败长度 65/67/68/69 全部 Match: True，内嵌场景待验证。",
        },
        {
            "step_id": "per-case-embedded-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0082:action",
            "result_source_event_ids": ["t0082:observation"],
            "outcome": "resolved",
            "semantic_change": "13 个内嵌双单引号用例逐用例独立进程验证全部 Match: True，任务级修复确认。",
        },
    ],
    "resolution_source_event_ids": ["t0082:observation"],
    "required_core_source_event_ids": [
        "t0005:action", "t0005:observation", "t0029:action", "t0076:action",
        "t0077:observation", "t0081:observation", "t0082:observation",
    ],
    "optional_support_source_event_ids": [
        "t0022:action", "t0029:observation", "t0030:observation", "t0036:action",
        "t0036:observation", "t0043:observation", "t0050:action", "t0050:observation",
        "t0051:observation", "t0058:action", "t0058:observation", "t0059:observation",
        "t0061:observation", "t0065:action", "t0065:observation", "t0071:action",
        "t0071:observation", "t0072:observation", "t0074:action", "t0074:observation",
        "t0075:observation", "t0076:observation", "t0078:observation", "t0079:action",
        "t0079:observation", "t0080:action", "t0082:action",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "t0005:action", "t0005:observation", "t0029:action", "t0076:action",
                "t0077:observation", "t0081:observation", "t0082:observation",
            ],
            [
                "t0005:action", "t0005:observation", "t0029:action", "t0076:action",
                "t0082:observation",
            ],
        ],
        "relevant_evidence_ids": rel(CID4, [
            "t0005:action", "t0005:observation", "t0022:action", "t0029:action",
            "t0029:observation", "t0030:observation", "t0036:action", "t0036:observation",
            "t0043:observation", "t0050:action", "t0050:observation", "t0051:observation",
            "t0058:action", "t0058:observation", "t0059:observation", "t0061:observation",
            "t0065:action", "t0065:observation", "t0071:action", "t0071:observation",
            "t0072:observation", "t0074:action", "t0074:observation", "t0075:observation",
            "t0076:action", "t0076:observation", "t0077:observation", "t0078:observation",
            "t0079:action", "t0079:observation", "t0080:action", "t0081:observation",
            "t0082:action", "t0082:observation",
        ]),
        "causal_paths": [
            {
                "evidence_ids": [
                    "t0005:action", "t0005:observation", "t0029:action", "t0076:action",
                    "t0077:observation", "t0081:observation", "t0082:observation",
                ],
                "constraints": [
                    ["t0005:action", "t0005:observation"],
                    ["t0005:observation", "t0029:action"],
                    ["t0029:action", "t0076:action"],
                    ["t0076:action", "t0077:observation"],
                    ["t0077:observation", "t0081:observation"],
                    ["t0081:observation", "t0082:observation"],
                ],
            },
            {
                "evidence_ids": [
                    "t0005:action", "t0005:observation", "t0029:action", "t0076:action",
                    "t0082:observation",
                ],
                "constraints": [
                    ["t0005:action", "t0005:observation"],
                    ["t0005:observation", "t0029:action"],
                    ["t0029:action", "t0076:action"],
                    ["t0076:action", "t0082:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                ["t0029:action", "t0058:action", "t0076:action", "t0082:observation"],
                ["t0076:action", "t0077:observation", "t0082:observation"],
            ],
            "relevant_evidence_ids": rel(CID4, [
                "t0022:action", "t0029:action", "t0029:observation", "t0030:observation",
                "t0043:observation", "t0051:observation", "t0058:action", "t0059:observation",
                "t0072:observation", "t0076:action", "t0076:observation", "t0077:observation",
                "t0078:observation", "t0079:action", "t0081:observation", "t0082:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0029:action", "t0058:action", "t0076:action", "t0082:observation"],
                    "constraints": [
                        ["t0029:action", "t0058:action"],
                        ["t0058:action", "t0076:action"],
                        ["t0076:action", "t0082:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0076:action", "t0077:observation", "t0082:observation"],
                    "constraints": [
                        ["t0076:action", "t0077:observation"],
                        ["t0077:observation", "t0082:observation"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0074:action", "t0075:observation", "t0082:action", "t0082:observation"],
                ["t0065:action", "t0081:observation"],
            ],
            "relevant_evidence_ids": rel(CID4, [
                "t0005:action", "t0005:observation", "t0065:action", "t0065:observation",
                "t0074:action", "t0075:observation", "t0081:action", "t0081:observation",
                "t0082:action", "t0082:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0074:action", "t0075:observation", "t0082:action", "t0082:observation"],
                    "constraints": [
                        ["t0074:action", "t0075:observation"],
                        ["t0075:observation", "t0082:action"],
                        ["t0082:action", "t0082:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0065:action", "t0081:observation"],
                    "constraints": [["t0065:action", "t0081:observation"]],
                },
            ],
        },
    },
}

# ---------------------------------------------------------------- case 005
CID5 = "6f8e78bc51196f62f3869793ea649e327684aab5841452aa0dce6ed97ad61202"
EP5 = {
    "scope": "task_level_failure_episode",
    "failure_family": "ordering_lookup_validation_logic_error",
    "recoverability": "R0",
    "error_signature": "❌ ISSUE REPRODUCED: Found models.E015 errors:",
    "diagnostic_evidence": (
        "t0040:observation 的复现脚本输出 ❌ ISSUE REPRODUCED: Found models.E015 errors，即 Meta.ordering 使用 "
        "'supply__product__parent__isnull' 这类合法 lookup 被系统检查误判为不存在的字段（models.E015）。"
        "t0020:action 的诊断思考与 t0038:observation 对 django/db/models/base.py _check_ordering 相关代码"
        "（第1749-1758行）的查看定位根因：except (FieldDoesNotExist, AttributeError) 分支只检查 "
        "fld.get_transform(part) 是否为 None，未考虑 isnull 这类内置 lookup。"
    ),
    "recovery_sequence": (
        "1) 在 workspace 副本 base.py 中把报错条件扩展为 get_transform 与 get_lookup 均为 None（t0062:action），"
        "复现脚本 E015 消失（t0063:observation）；2) 综合脚本验证 isnull/exact/lower 等场景（t0064:action→t0072:observation）；"
        "3) 在 workspace 测试新增 test_ordering_allows_builtin_lookups 后 runtests 仍 FAILED（t0076:action→t0077:observation）；"
        "4) 诊断发现 runtests 实际导入 /testbed/django 而补丁只在 /workspace 副本（t0085:action→t0103:observation）；"
        "5) 将同一修复应用到 /testbed/django/db/models/base.py（t0104:action）；"
        "6) 把新用例加入 /testbed 测试文件（t0106:action）；7) 新用例 ok、OtherModelTests 37 项与 invalid_models_tests "
        "全套通过（t0107:action→t0109:observation）；8) 从 /testbed 复跑复现与综合脚本均通过"
        "（t0110:action→t0111:observation）；9) check_framework 套件通过（t0112:observation）；"
        "10) custom_lookups 26 项 OK（t0113:action→t0113:observation）。"
    ),
    "resolution_evidence": (
        "t0113:observation 显示 custom_lookups 套件 Ran 26 tests / OK (skipped=4) 且 exit code 0，配合 "
        "t0110:observation 中 E015 消失、t0107:observation 新增官方用例 ok，确认修复在 /testbed 正式运行时生效且无回归。"
    ),
    "anchor_source_event_id": "t0040:action",
    "initial_action_source_event_id": "t0040:action",
    "initial_result_source_event_ids": ["t0040:observation"],
    "repair_steps": [
        {
            "step_id": "apply-workspace-fix",
            "decision_source_event_ids": ["t0020:action"],
            "action_source_event_id": "t0062:action",
            "result_source_event_ids": ["t0062:observation", "t0063:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 /workspace 副本 base.py 的 _check_ordering 中将条件改为 get_transform(part) 与 get_lookup(part) 均为 None 才报 E015，复现脚本显示 No models.E015 errors found，但官方 runtests 环境尚未生效。",
        },
        {
            "step_id": "comprehensive-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0064:action",
            "result_source_event_ids": ["t0064:observation", "t0065:observation", "t0072:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "新建综合脚本覆盖 isnull/exact/lower/不存在字段等场景，首跑因脚本自身用例构造问题退出码 1（t0065），修正脚本（t0069-t0071）后全部验证通过（t0072）。",
        },
        {
            "step_id": "add-official-test-workspace",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0076:action",
            "result_source_event_ids": ["t0076:observation", "t0077:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 workspace 测试文件新增 test_ordering_allows_builtin_lookups（ordering 使用 parent__isnull 应通过检查），但 runtests 运行该用例 FAILED (failures=1)。",
        },
        {
            "step_id": "diagnose-module-source",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0085:action",
            "result_source_event_ids": ["t0085:observation", "t0100:observation", "t0103:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "定位关键差异：独立 python 导入 /workspace 副本（t0085），而 runtests 导入 /testbed/django/db/models/base.py（t0100），且 /testbed 中仍是未修复的旧逻辑（t0103）。",
        },
        {
            "step_id": "apply-fix-to-testbed",
            "decision_source_event_ids": ["t0103:observation"],
            "action_source_event_id": "t0104:action",
            "result_source_event_ids": ["t0104:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "将 get_transform/get_lookup 双重检查的同一修复应用到官方运行时 /testbed/django/db/models/base.py，编辑确认成功，尚未验证。",
        },
        {
            "step_id": "add-official-test-testbed",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0106:action",
            "result_source_event_ids": ["t0106:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "把 test_ordering_allows_builtin_lookups 新用例同步加入 /testbed/tests/invalid_models_tests/test_models.py。",
        },
        {
            "step_id": "verify-testbed-suites",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0107:action",
            "result_source_event_ids": ["t0107:observation", "t0108:observation", "t0109:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 /testbed 下运行新用例 ok，OtherModelTests 37 项 OK，invalid_models_tests 全套通过，修复在官方运行时生效。",
        },
        {
            "step_id": "rerun-repro-comprehensive",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0110:action",
            "result_source_event_ids": ["t0110:observation", "t0111:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "从 /testbed 复跑原始复现脚本（E015 消失）与综合脚本（isnull/exact/lower 等场景全部通过），回归套件待跑。",
        },
        {
            "step_id": "check-framework-regression",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0112:action",
            "result_source_event_ids": ["t0112:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "check_framework 套件在 /testbed 下通过（exit code 0），系统检查框架无回归。",
        },
        {
            "step_id": "custom-lookups-regression",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0113:action",
            "result_source_event_ids": ["t0113:observation"],
            "outcome": "resolved",
            "semantic_change": "custom_lookups 套件 Ran 26 tests / OK (skipped=4)，任务级修复经多套官方回归确认。",
        },
    ],
    "resolution_source_event_ids": ["t0113:observation"],
    "required_core_source_event_ids": [
        "t0040:action", "t0040:observation", "t0062:action", "t0063:observation",
        "t0104:action", "t0107:observation", "t0113:action", "t0113:observation",
    ],
    "optional_support_source_event_ids": [
        "t0020:action", "t0044:observation", "t0053:observation", "t0055:observation",
        "t0062:observation", "t0064:action", "t0064:observation", "t0065:observation",
        "t0072:observation", "t0076:action", "t0076:observation", "t0077:observation",
        "t0085:action", "t0085:observation", "t0100:observation", "t0103:action",
        "t0103:observation", "t0104:observation", "t0106:action", "t0106:observation",
        "t0107:action", "t0108:observation", "t0109:observation", "t0110:action",
        "t0110:observation", "t0111:observation", "t0112:action", "t0112:observation",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "t0040:action", "t0040:observation", "t0062:action", "t0063:observation",
                "t0104:action", "t0107:observation", "t0113:action", "t0113:observation",
            ],
            [
                "t0040:action", "t0040:observation", "t0104:action", "t0107:observation",
                "t0113:observation",
            ],
        ],
        "relevant_evidence_ids": rel(CID5, [
            "t0020:action", "t0040:action", "t0040:observation", "t0044:observation",
            "t0053:observation", "t0055:observation", "t0062:action", "t0062:observation",
            "t0063:observation", "t0064:action", "t0064:observation", "t0065:observation",
            "t0072:observation", "t0076:action", "t0076:observation", "t0077:observation",
            "t0085:action", "t0085:observation", "t0100:observation", "t0103:action",
            "t0103:observation", "t0104:action", "t0104:observation", "t0106:action",
            "t0106:observation", "t0107:action", "t0107:observation", "t0108:observation",
            "t0109:observation", "t0110:action", "t0110:observation", "t0111:observation",
            "t0112:action", "t0112:observation", "t0113:action", "t0113:observation",
        ]),
        "causal_paths": [
            {
                "evidence_ids": [
                    "t0040:action", "t0040:observation", "t0062:action", "t0063:observation",
                    "t0104:action", "t0107:observation", "t0113:action", "t0113:observation",
                ],
                "constraints": [
                    ["t0040:action", "t0040:observation"],
                    ["t0040:observation", "t0062:action"],
                    ["t0062:action", "t0063:observation"],
                    ["t0063:observation", "t0104:action"],
                    ["t0104:action", "t0107:observation"],
                    ["t0107:observation", "t0113:action"],
                    ["t0113:action", "t0113:observation"],
                ],
            },
            {
                "evidence_ids": [
                    "t0040:action", "t0040:observation", "t0104:action", "t0107:observation",
                    "t0113:observation",
                ],
                "constraints": [
                    ["t0040:action", "t0040:observation"],
                    ["t0040:observation", "t0104:action"],
                    ["t0104:action", "t0107:observation"],
                    ["t0107:observation", "t0113:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                ["t0062:action", "t0104:action", "t0107:observation", "t0113:observation"],
                ["t0104:action", "t0111:observation", "t0113:observation"],
            ],
            "relevant_evidence_ids": rel(CID5, [
                "t0020:action", "t0062:action", "t0062:observation", "t0063:observation",
                "t0072:observation", "t0077:observation", "t0085:observation",
                "t0100:observation", "t0103:observation", "t0104:action", "t0104:observation",
                "t0106:action", "t0107:action", "t0107:observation", "t0108:observation",
                "t0109:observation", "t0110:observation", "t0111:observation",
                "t0113:action", "t0113:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0062:action", "t0104:action", "t0107:observation", "t0113:observation"],
                    "constraints": [
                        ["t0062:action", "t0104:action"],
                        ["t0104:action", "t0107:observation"],
                        ["t0107:observation", "t0113:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0104:action", "t0111:observation", "t0113:observation"],
                    "constraints": [
                        ["t0104:action", "t0111:observation"],
                        ["t0111:observation", "t0113:observation"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0053:action", "t0053:observation", "t0113:action", "t0113:observation"],
                ["t0107:action", "t0113:observation"],
            ],
            "relevant_evidence_ids": rel(CID5, [
                "t0040:action", "t0040:observation", "t0053:action", "t0053:observation",
                "t0107:action", "t0107:observation", "t0110:action", "t0110:observation",
                "t0112:action", "t0113:action", "t0113:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0053:action", "t0053:observation", "t0113:action", "t0113:observation"],
                    "constraints": [
                        ["t0053:action", "t0053:observation"],
                        ["t0053:observation", "t0113:action"],
                        ["t0113:action", "t0113:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0107:action", "t0113:observation"],
                    "constraints": [["t0107:action", "t0113:observation"]],
                },
            ],
        },
    },
}

# ---------------------------------------------------------------- case 006
CID6 = "9a912678cf56d0aee7ca4bb0e943369b2f992af179782f5805f5f71a2157b8db"
EP6 = {
    "scope": "task_level_failure_episode",
    "failure_family": "validation_error_message_missing_value",
    "recoverability": "R2",
    "error_signature": "Select a valid choice. That choice is not one of the available choices.",
    "diagnostic_evidence": (
        "t0027:observation 的复现脚本显示 ModelChoiceField 的报错为 Select a valid choice. That choice is not one of "
        "the available choices.，不含非法值，与 ChoiceField/ModelMultipleChoiceField 的行为不一致，构成任务级初始失败。"
        "t0032:action 的诊断思考定位到 /workspace/django__django__4.0/django/forms/models.py 的两处缺陷："
        "ModelChoiceField.default_error_messages 模板缺少 %(value)s，且 to_python 中抛出 ValidationError 时未传 "
        "params。"
    ),
    "recovery_sequence": (
        "1) 完善复现脚本后确认不一致仍在（t0028:action→t0031:observation）；"
        "2) 将 default_error_messages 模板改为含 %(value)s（t0033:action）；"
        "3) to_python 的 ValidationError 增加 params={'value': value}（t0034:action），复跑显示 ModelChoiceField "
        "Error includes invalid value: True（t0035:observation）；"
        "4) 修正脚本 summary 逻辑后输出所有字段类型一致包含非法值（t0036:action→t0037:observation）；"
        "5) 边界用例（整数/字符串/to_field_name/特殊字符/Unicode）全部通过（t0038:action→t0039:observation）；"
        "6) 官方 model_forms.test_modelchoicefield 套件通过（t0040:action）；"
        "7) 核查 assertRaisesMessage 兼容性，test_basics/test_clean_model_instance、choicefield 9 项及 "
        "forms_tests/model_forms 相关套件通过（t0054:action→t0074:observation）；"
        "8) 最终复跑复现脚本全部一致（t0075:observation）；9) 边界用例最终复跑全部通过"
        "（t0076:action→t0076:observation）。"
    ),
    "resolution_evidence": (
        "t0076:observation 显示 5 类边界值（999、nonexistent、invalid_slug、test@#$%^&*()、测试用 Unicode 串）全部输出"
        "含非法值的报错且 All edge case tests passed!，配合 t0075:observation 的所有字段类型一致输出，确认任务达成。"
    ),
    "anchor_source_event_id": "t0027:action",
    "initial_action_source_event_id": "t0027:action",
    "initial_result_source_event_ids": ["t0027:observation"],
    "repair_steps": [
        {
            "step_id": "refine-repro",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0028:action",
            "result_source_event_ids": ["t0028:observation", "t0031:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "修正复现脚本对 ChoiceField 的断言方式（比较插值后的 messages），重跑确认 ModelChoiceField 仍为 Error includes invalid value: False，不一致仍在。",
        },
        {
            "step_id": "fix-error-template",
            "decision_source_event_ids": ["t0032:action"],
            "action_source_event_id": "t0033:action",
            "result_source_event_ids": ["t0033:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "将 ModelChoiceField.default_error_messages 的 invalid_choice 模板改为 Select a valid choice. %(value)s is not one of the available choices.，尚未修改抛错处。",
        },
        {
            "step_id": "fix-validationerror-params",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0034:action",
            "result_source_event_ids": ["t0034:observation", "t0035:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 to_python 的 ValidationError 调用中加入 params={'value': value}，复跑复现脚本 ModelChoiceField 的 messages 已插值出非法值（Error includes invalid value: True），但脚本 summary 文案仍写死为未修复。",
        },
        {
            "step_id": "fix-repro-summary",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0036:action",
            "result_source_event_ids": ["t0036:observation", "t0037:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "把复现脚本的 summary 改为按实际结果输出，重跑显示 All field types now consistently include the invalid value in error messages!，边界情形尚未验证。",
        },
        {
            "step_id": "edge-case-tests",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0038:action",
            "result_source_event_ids": ["t0038:observation", "t0039:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "新建边界用例脚本覆盖整数、字符串、to_field_name、特殊字符、Unicode 五类非法值，全部输出含值的报错并通过，官方套件回归未完成。",
        },
        {
            "step_id": "official-suite-regression",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0040:action",
            "result_source_event_ids": ["t0040:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "官方 runtests 运行 model_forms.test_modelchoicefield 套件通过（exit code 0），既有 ModelChoiceField 测试未破坏。",
        },
        {
            "step_id": "verify-test-compat",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0054:action",
            "result_source_event_ids": ["t0054:observation", "t0072:observation", "t0074:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "核查 assertRaisesMessage 按子串匹配的兼容性：test_basics/test_clean_model_instance、choicefield 9 项以及 forms_tests/model_forms 相关套件均通过。",
        },
        {
            "step_id": "final-repro-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0075:action",
            "result_source_event_ids": ["t0075:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "最终复跑复现脚本：ChoiceField/ModelChoiceField/ModelMultipleChoiceField 全部一致包含非法值，边界脚本待最终复跑。",
        },
        {
            "step_id": "final-edge-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0076:action",
            "result_source_event_ids": ["t0076:observation"],
            "outcome": "resolved",
            "semantic_change": "边界用例脚本最终复跑：5 类非法值全部输出含值报错，All edge case tests passed!，任务级修复确认。",
        },
    ],
    "resolution_source_event_ids": ["t0076:observation"],
    "required_core_source_event_ids": [
        "t0027:action", "t0027:observation", "t0033:action", "t0034:action",
        "t0037:observation", "t0075:observation", "t0076:action", "t0076:observation",
    ],
    "optional_support_source_event_ids": [
        "t0031:observation", "t0032:action", "t0033:observation", "t0034:observation",
        "t0035:observation", "t0036:action", "t0036:observation", "t0038:action",
        "t0038:observation", "t0039:observation", "t0040:action", "t0040:observation",
        "t0049:observation", "t0054:action", "t0054:observation", "t0062:observation",
        "t0072:action", "t0072:observation", "t0074:action", "t0074:observation",
        "t0077:action", "t0077:observation",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "t0027:action", "t0027:observation", "t0033:action", "t0034:action",
                "t0037:observation", "t0075:observation", "t0076:action", "t0076:observation",
            ],
            [
                "t0027:action", "t0027:observation", "t0033:action", "t0034:action",
                "t0076:observation",
            ],
        ],
        "relevant_evidence_ids": rel(CID6, [
            "t0027:action", "t0027:observation", "t0028:action", "t0028:observation",
            "t0031:observation", "t0032:action",
            "t0033:action", "t0033:observation", "t0034:action", "t0034:observation",
            "t0035:observation", "t0036:action", "t0036:observation", "t0037:observation",
            "t0038:action", "t0038:observation", "t0039:observation", "t0040:action",
            "t0040:observation", "t0049:observation", "t0054:action", "t0054:observation",
            "t0062:observation", "t0072:action", "t0072:observation", "t0074:action",
            "t0074:observation", "t0075:observation", "t0076:action", "t0076:observation",
            "t0077:action", "t0077:observation",
        ]),
        "causal_paths": [
            {
                "evidence_ids": [
                    "t0027:action", "t0027:observation", "t0033:action", "t0034:action",
                    "t0037:observation", "t0075:observation", "t0076:action", "t0076:observation",
                ],
                "constraints": [
                    ["t0027:action", "t0027:observation"],
                    ["t0027:observation", "t0033:action"],
                    ["t0033:action", "t0034:action"],
                    ["t0034:action", "t0037:observation"],
                    ["t0037:observation", "t0075:observation"],
                    ["t0075:observation", "t0076:action"],
                    ["t0076:action", "t0076:observation"],
                ],
            },
            {
                "evidence_ids": [
                    "t0027:action", "t0027:observation", "t0033:action", "t0034:action",
                    "t0076:observation",
                ],
                "constraints": [
                    ["t0027:action", "t0027:observation"],
                    ["t0027:observation", "t0033:action"],
                    ["t0033:action", "t0034:action"],
                    ["t0034:action", "t0076:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                ["t0033:action", "t0034:action", "t0037:observation", "t0076:observation"],
                ["t0034:action", "t0075:observation", "t0076:observation"],
            ],
            "relevant_evidence_ids": rel(CID6, [
                "t0032:action", "t0033:action", "t0033:observation", "t0034:action",
                "t0034:observation", "t0035:observation", "t0037:observation",
                "t0039:observation", "t0040:observation", "t0054:observation",
                "t0072:observation", "t0074:observation", "t0075:observation",
                "t0076:action", "t0076:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0033:action", "t0034:action", "t0037:observation", "t0076:observation"],
                    "constraints": [
                        ["t0033:action", "t0034:action"],
                        ["t0034:action", "t0037:observation"],
                        ["t0037:observation", "t0076:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0034:action", "t0075:observation", "t0076:observation"],
                    "constraints": [
                        ["t0034:action", "t0075:observation"],
                        ["t0075:observation", "t0076:observation"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0075:action", "t0075:observation", "t0076:action", "t0076:observation"],
                ["t0040:action", "t0072:observation"],
            ],
            "relevant_evidence_ids": rel(CID6, [
                "t0027:action", "t0027:observation", "t0040:action", "t0040:observation",
                "t0072:action", "t0072:observation", "t0075:action", "t0075:observation",
                "t0076:action", "t0076:observation",
            ]),
            "causal_paths": [
                {
                    "evidence_ids": ["t0075:action", "t0075:observation", "t0076:action", "t0076:observation"],
                    "constraints": [
                        ["t0075:action", "t0075:observation"],
                        ["t0075:observation", "t0076:action"],
                        ["t0076:action", "t0076:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0040:action", "t0072:observation"],
                    "constraints": [["t0040:action", "t0072:observation"]],
                },
            ],
        },
    },
}


def main() -> None:
    for cid, ep in [(CID2, EP2), (CID3, EP3), (CID4, EP4), (CID5, EP5), (CID6, EP6)]:
        A.check_episode(ep, cid)
        A.write_row(OUT, cid, annotator=ANNO, status="annotated", episode=ep)
    errors = A.check_file(OUT)
    for e in errors:
        print("ERROR", e)
    print("checked", OUT, "->", len(errors), "errors")


if __name__ == "__main__":
    main()
