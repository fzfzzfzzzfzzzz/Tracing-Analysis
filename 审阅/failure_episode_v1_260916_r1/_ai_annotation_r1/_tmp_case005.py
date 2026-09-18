import sys
sys.path.insert(0, r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1")
import anno_lib as A

CID = "6f8e78bc51196f62f3869793ea649e327684aab5841452aa0dce6ed97ad61202"
OUT = r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1\pass_b\batch_05.jsonl"

EPISODE = {
    "scope": "task_level_failure_episode",
    "failure_family": "validation_logic_error",
    "recoverability": "R0",
    "error_signature": "❌ ISSUE REPRODUCED: Found models.E015 errors:",
    "diagnostic_evidence": (
        "t0040:observation 复现脚本输出显示 ordering 使用合法 lookup（如 supply__product__parent__isnull）时 "
        "系统检查错误地报 models.E015（'ordering' refers to the nonexistent field, related field, or lookup），"
        "构成任务级初始失败。根因：django/db/models/base.py 的 _check_ordering_item 在 except (FieldDoesNotExist, AttributeError) "
        "分支只检查 fld.get_transform(part)，未检查 get_lookup，导致 isnull 这类内建 lookup 被当作不存在的字段；"
        "t0055:observation（order_by('parent__isnull') 报 Cannot resolve keyword 'isnull'，而 filter(parent__isnull=True) 成功）"
        "与 t0067:observation（get_lookup('isnull') 返回 IsNull 类而 get_transform('isnull') 返回 None）支持该结论。"
    ),
    "recovery_sequence": (
        "1) 改造复现脚本并同时验证系统检查与实际查询后重跑，仍复现 models.E015（t0041→t0044）；"
        "2) 在 /workspace 副本 base.py 的异常分支加入 get_lookup 判断，复现脚本不再报 E015（t0062→t0063）；"
        "3) 新建综合测试首跑因未注册 lookup/transform 出现失败（t0064→t0065）；"
        "4) 依据 get_lookup/get_transform 语义调试后改用 register_lookup，综合测试全部通过（t0067→t0069→t0072）；"
        "5) 向 workspace 测试文件新增 test_ordering_allows_builtin_lookups，经 runtests 运行 FAIL（t0076→t0077）；"
        "6) 排查发现 runtests 实际导入 /testbed/django 而非 /workspace（t0100:observation、t0103:observation 确认 /testbed 未修复），"
        "将同一修复应用到 /testbed/django/db/models/base.py 并补充官方测试，新测试 OK（t0104→t0107）；"
        "7) 在 /testbed 正式环境复跑 check_framework 与 custom_lookups 回归套件全部 OK，任务级修复确认（t0112→t0113）。"
    ),
    "resolution_evidence": (
        "t0113:observation 显示对 /testbed 正式环境复跑 custom_lookups 套件 Ran 26 tests / OK (skipped=4, exit code 0)，"
        "t0112:observation 显示 check_framework 套件亦通过（exit code 0），确认修复未破坏 lookup 相关官方功能。"
    ),
    "anchor_source_event_id": "t0040:action",
    "initial_action_source_event_id": "t0040:action",
    "initial_result_source_event_ids": ["t0040:observation"],
    "repair_steps": [
        {
            "step_id": "rework-repro-rerun",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0041:action",
            "result_source_event_ids": ["t0041:observation", "t0044:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "调整复现脚本使其同时验证系统检查与实际查询后重跑，输出仍为 ISSUE REPRODUCED 的 models.E015 错误，问题未消除。",
        },
        {
            "step_id": "fix-workspace-base",
            "decision_source_event_ids": ["t0055:observation"],
            "action_source_event_id": "t0062:action",
            "result_source_event_ids": ["t0062:observation", "t0063:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "在 /workspace 副本 django/db/models/base.py 的 except 分支加入 fld.get_lookup(part) 判断，复现脚本显示 No models.E015 errors found，但官方测试环境 /testbed 尚未应用修复且综合测试未通过。",
        },
        {
            "step_id": "comprehensive-test-first-run",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0064:action",
            "result_source_event_ids": ["t0065:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "新建覆盖 __isnull/__exact/__lower 等组合的综合测试并首跑，部分用例校验失败（exit code 1），修复的正确性尚未确认。",
        },
        {
            "step_id": "fix-comprehensive-test",
            "decision_source_event_ids": ["t0067:observation"],
            "action_source_event_id": "t0069:action",
            "result_source_event_ids": ["t0072:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "依据 get_lookup 与 get_transform 的语义差异改用 register_lookup 修正综合测试，全部用例通过（exit code 0），但仅验证了 /workspace 环境。",
        },
        {
            "step_id": "add-official-test-fails",
            "decision_source_event_ids": ["t0073:observation"],
            "action_source_event_id": "t0076:action",
            "result_source_event_ids": ["t0077:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "向测试文件新增 test_ordering_allows_builtin_lookups 回归测试，经 runtests 运行 FAIL（failures=1），提示官方运行环境未生效。",
        },
        {
            "step_id": "fix-testbed-base",
            "decision_source_event_ids": ["t0100:observation", "t0103:observation"],
            "action_source_event_id": "t0104:action",
            "result_source_event_ids": ["t0104:observation", "t0107:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "发现 runtests 实际导入 /testbed/django 后，将同样的 get_lookup 修复应用到 /testbed/django/db/models/base.py 并在官方测试文件补充回归用例，新测试 OK，但尚未做全量回归确认。",
        },
        {
            "step_id": "final-regression",
            "decision_source_event_ids": ["t0111:observation"],
            "action_source_event_id": "t0112:action",
            "result_source_event_ids": ["t0112:observation", "t0113:observation"],
            "outcome": "resolved",
            "semantic_change": "在 /testbed 正式环境依次复跑 check_framework 与 custom_lookups 回归套件均通过（exit code 0），任务级修复在官方运行时确认。",
        },
    ],
    "resolution_source_event_ids": ["t0112:observation", "t0113:observation"],
    "required_core_source_event_ids": [
        "t0040:action", "t0040:observation", "t0062:action", "t0063:observation",
        "t0104:action", "t0107:observation", "t0113:observation",
    ],
    "optional_support_source_event_ids": [
        "t0044:observation", "t0055:observation", "t0067:observation", "t0077:observation",
        "t0100:observation", "t0103:observation", "t0110:observation", "t0111:observation",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "t0040:action", "t0040:observation", "t0062:action", "t0063:observation",
                "t0104:action", "t0107:observation", "t0113:observation",
            ],
            ["t0040:action", "t0040:observation", "t0104:action", "t0113:observation"],
        ],
        "relevant_evidence_ids": [
            "t0040:action", "t0040:observation", "t0041:action", "t0041:observation",
            "t0044:observation", "t0055:observation", "t0062:action", "t0062:observation",
            "t0063:observation", "t0064:action", "t0065:observation", "t0067:observation",
            "t0069:action", "t0072:observation", "t0073:observation", "t0076:action",
            "t0077:observation", "t0100:observation", "t0103:observation", "t0104:action",
            "t0104:observation", "t0107:observation", "t0110:observation", "t0111:observation",
            "t0112:action", "t0112:observation", "t0113:observation",
        ],
        "causal_paths": [
            {
                "evidence_ids": [
                    "t0040:action", "t0040:observation", "t0062:action", "t0063:observation",
                    "t0104:action", "t0107:observation", "t0113:observation",
                ],
                "constraints": [
                    ["t0040:action", "t0040:observation"],
                    ["t0040:observation", "t0062:action"],
                    ["t0062:action", "t0063:observation"],
                    ["t0063:observation", "t0104:action"],
                    ["t0104:action", "t0107:observation"],
                    ["t0107:observation", "t0113:observation"],
                ],
            },
            {
                "evidence_ids": ["t0040:action", "t0040:observation", "t0104:action", "t0113:observation"],
                "constraints": [
                    ["t0040:action", "t0040:observation"],
                    ["t0040:observation", "t0104:action"],
                    ["t0104:action", "t0113:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                ["t0062:action", "t0063:observation", "t0104:action", "t0107:observation"],
                ["t0062:action", "t0100:observation", "t0104:action", "t0113:observation"],
            ],
            "relevant_evidence_ids": [
                "t0055:observation", "t0062:action", "t0062:observation", "t0063:observation",
                "t0067:observation", "t0077:observation", "t0100:observation", "t0103:observation",
                "t0104:action", "t0104:observation", "t0107:observation", "t0112:action",
                "t0113:observation",
            ],
            "causal_paths": [
                {
                    "evidence_ids": ["t0062:action", "t0063:observation", "t0104:action", "t0107:observation"],
                    "constraints": [
                        ["t0062:action", "t0063:observation"],
                        ["t0063:observation", "t0104:action"],
                        ["t0104:action", "t0107:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0062:action", "t0100:observation", "t0104:action", "t0113:observation"],
                    "constraints": [
                        ["t0062:action", "t0100:observation"],
                        ["t0100:observation", "t0104:action"],
                        ["t0104:action", "t0113:observation"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0107:observation", "t0111:observation", "t0113:observation"],
                ["t0110:observation", "t0112:observation", "t0113:observation"],
            ],
            "relevant_evidence_ids": [
                "t0103:observation", "t0107:observation", "t0110:observation", "t0111:observation",
                "t0112:observation", "t0113:observation",
            ],
            "causal_paths": [
                {
                    "evidence_ids": ["t0107:observation", "t0111:observation", "t0113:observation"],
                    "constraints": [
                        ["t0107:observation", "t0111:observation"],
                        ["t0111:observation", "t0113:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0110:observation", "t0112:observation", "t0113:observation"],
                    "constraints": [
                        ["t0110:observation", "t0112:observation"],
                        ["t0112:observation", "t0113:observation"],
                    ],
                },
            ],
        },
    },
}

A.check_episode(EPISODE, CID)
A.write_row(OUT, CID, annotator="ai-draft:zcode:glm-5.2:pass-b", status="annotated", episode=EPISODE)
print("case005 OK")
