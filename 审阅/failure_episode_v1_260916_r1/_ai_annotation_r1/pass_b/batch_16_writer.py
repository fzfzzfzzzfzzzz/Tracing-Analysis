"""Pass-B batch_16 annotation writer (annotator B, cases 16/36/56/76/96).

Run from anywhere:  python pass_b/batch_16_writer.py
Then self-check:    python anno_lib.py pass_b/batch_16.jsonl
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anno_lib as A

OUT = Path(__file__).resolve().parent / "batch_16.jsonl"
ANNO = "ai-draft:zcode:glm-5.2:pass-b"


# ---------------------------------------------------------------- case 16 ---
# MONAI-6736: CropForeground margin>0 only applied on one side (52 vs expected 54).
C16 = "ac20aae1a649cfc9f84793cf7b2d44b9e6ecf710f89221fdc38f0e1bff3b1b1d"

IA16 = "m0034:call:call_yWQ6lplxlgsCv1TH3sMYTawW"
IR16 = "m0035:result:call_yWQ6lplxlgsCv1TH3sMYTawW"
E16 = "m0038:call:call_O0NGn44YyrWrb1Xn4JiRD6O3"
ER16 = "m0039:result:call_O0NGn44YyrWrb1Xn4JiRD6O3"
R16 = "m0040:call:call_kOT9VU0QcmpR6HpJyo6TwKqY"
RR16 = "m0041:result:call_kOT9VU0QcmpR6HpJyo6TwKqY"
CR16 = "m0032:call:call_Arw5b5QSNxP25zkSCyVYr94i"
CR16R = "m0033:result:call_Arw5b5QSNxP25zkSCyVYr94i"

EP16 = {
    "scope": "task_level_failure_episode",
    "failure_family": "crop_margin_logic_error",
    "recoverability": "R0",
    "error_signature": "cropped size:  torch.Size([1, 52, 100])",
    "diagnostic_evidence": (
        "m0034:call 首次运行 PR 复现脚本 reproduce_issue.py（m0032:call 创建，mask 前景占 50 行、"
        "margin=2，两侧各扩 2 行应得 54 行），m0035:result 直接输出 cropped size: torch.Size([1, 52, 100])，"
        "margin 只在一侧生效，构成任务级初始失败；m0036:message 判定问题出在 margin 未正确作用于 "
        "CropForeground 的 bounding box 两侧。"
    ),
    "recovery_sequence": (
        "1) m0038:call 在 /workspace/Project-MONAI__MONAI__1.2/monai/transforms/croppad/array.py 的 "
        "compute_bounding_box 中把 margin 直接加到 box_start_/box_end_ 并以图像边界约束"
        "（m0039:result 确认编辑成功）；"
        "2) m0040:call 复跑 reproduce_issue.py，m0041:result 输出 cropped size: torch.Size([1, 54, 100])，"
        "50 行前景加两侧各 2 行 margin，修复被直接观察；m0042:message 总结任务完成。"
    ),
    "resolution_evidence": (
        "m0041:result 为最终成功的直接观察：复跑 reproduce_issue.py 输出 cropped size: "
        "torch.Size([1, 54, 100])（exit code 0），与 50+2+2 的预期一致，margin 双侧生效；"
        "m0042:message 亦确认 CropForeground 问题已解决。"
    ),
    "anchor_source_event_id": IA16,
    "initial_action_source_event_id": IA16,
    "initial_result_source_event_ids": [IR16],
    "repair_steps": [
        {
            "step_id": "apply-margin-fix",
            "decision_source_event_ids": ["m0036:message", "m0038:message"],
            "action_source_event_id": E16,
            "result_source_event_ids": [ER16],
            "outcome": "intermediate_failure",
            "semantic_change": (
                "在 compute_bounding_box 中将 margin 直接加到 box_start_/box_end_ 并约束在图像边界内"
                "（m0038:call→m0039:result 编辑成功），但尚未复跑脚本验证效果。"
            ),
        },
        {
            "step_id": "rerun-repro-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": R16,
            "result_source_event_ids": [RR16],
            "outcome": "resolved",
            "semantic_change": (
                "复跑 reproduce_issue.py（m0040:call），m0041:result 输出 cropped size: "
                "torch.Size([1, 54, 100])，margin 双侧生效，任务级修复在正式环境中确认。"
            ),
        },
    ],
    "resolution_source_event_ids": [RR16],
    "required_core_source_event_ids": [IA16, IR16, E16, R16, RR16],
    "optional_support_source_event_ids": [
        CR16, CR16R, "m0036:message", "m0038:message", ER16, "m0042:message",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [IA16, IR16, E16, R16, RR16],
            [IA16, IR16, RR16],
        ],
        "relevant_evidence_ids": [
            CR16, CR16R, IA16, IR16, "m0036:message", "m0038:message", E16, ER16,
            R16, RR16, "m0042:message",
        ],
        "causal_paths": [
            {
                "evidence_ids": [IA16, IR16, E16, R16, RR16],
                "constraints": [
                    [IA16, IR16],
                    [IR16, E16],
                    [E16, R16],
                    [R16, RR16],
                ],
            },
            {
                "evidence_ids": [IA16, IR16, RR16],
                "constraints": [
                    [IA16, IR16],
                    [IR16, RR16],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                [E16, R16, RR16],
                ["m0038:message", E16, RR16],
            ],
            "relevant_evidence_ids": [
                "m0036:message", "m0038:message", E16, ER16, R16, RR16,
            ],
            "causal_paths": [
                {
                    "evidence_ids": [E16, R16, RR16],
                    "constraints": [
                        [E16, R16],
                        [R16, RR16],
                    ],
                },
                {
                    "evidence_ids": ["m0038:message", E16, RR16],
                    "constraints": [
                        ["m0038:message", E16],
                        [E16, RR16],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                [CR16, CR16R, RR16],
                [CR16, RR16],
            ],
            "relevant_evidence_ids": [
                CR16, CR16R, IA16, IR16, RR16, "m0042:message",
            ],
            "causal_paths": [
                {
                    "evidence_ids": [CR16, CR16R, RR16],
                    "constraints": [
                        [CR16, CR16R],
                        [CR16R, RR16],
                    ],
                },
                {
                    "evidence_ids": [CR16, RR16],
                    "constraints": [
                        [CR16, RR16],
                    ],
                },
            ],
        },
    },
}


# ---------------------------------------------------------------- case 36 ---
# Crafter task 9: agent goes blind ("You see nothing") and oscillates, escapes east.
C36 = "d4e7c0c0c500cbd962cba7fc0dafa772645d921480a8c1cae7df67dde1419fb1"

EP36 = {
    "scope": "task_level_failure_episode",
    "failure_family": "stuck_blind_navigation",
    "recoverability": "R3",
    "error_signature": "You see nothing away from you.",
    "diagnostic_evidence": (
        "t0192:observation 时智能体仍可见 water/sand/tree 等环境参照物；t0193:action 向西移动后，"
        "t0193:observation 变为 'You see nothing away from you. You face nothing at your front.'，"
        "随后 t0194:observation 至 t0227:observation 期间（南北往返、间或东西移动与 Noop）观察始终为空，"
        "智能体进入无参照物区域后陷入盲目振荡、无法推进任务，构成任务级初始失败。"
    ),
    "recovery_sequence": (
        "1) t0194:action 起以南北往返为主尝试脱困，t0194:observation、t0199:observation、t0202:observation "
        "仍为 You see nothing away from you；"
        "2) t0203:action 起转向向西/向东探测并穿插 Noop，t0203:observation、t0209:observation、"
        "t0220:observation 仍看不到任何物体；"
        "3) t0228:action 向东移动后 t0228:observation 重新列出 sand/grass/tree/arrow/path/skeleton/stone "
        "等物体（含 skeleton 2 steps to your east），环境感知恢复，失明状态解除。"
    ),
    "resolution_evidence": (
        "t0228:observation 为最终成功的直接观察：输出恢复为可见环境清单"
        "（'You see:\\n- sand 3 steps to your north\\n- grass 1 step to your west ... "
        "- skeleton 2 steps to your east ... You face path at your front.'），智能体重获导航参照。"
    ),
    "anchor_source_event_id": "t0193:action",
    "initial_action_source_event_id": "t0193:action",
    "initial_result_source_event_ids": ["t0193:observation"],
    "repair_steps": [
        {
            "step_id": "oscillate-north-south",
            "decision_source_event_ids": ["t0193:observation"],
            "action_source_event_id": "t0194:action",
            "result_source_event_ids": [
                "t0194:observation", "t0199:observation", "t0202:observation",
            ],
            "outcome": "intermediate_failure",
            "semantic_change": (
                "以南北往返为主尝试脱困（t0194:action 起），t0194/t0199/t0202:observation 仍为 "
                "You see nothing away from you，位置在小范围内振荡而无可见进展。"
            ),
        },
        {
            "step_id": "probe-west-east-noop",
            "decision_source_event_ids": ["t0202:observation"],
            "action_source_event_id": "t0203:action",
            "result_source_event_ids": [
                "t0203:observation", "t0209:observation", "t0220:observation",
            ],
            "outcome": "intermediate_failure",
            "semantic_change": (
                "转向向西/向东探测并穿插 Noop（t0203:action 起），t0203/t0209/t0220:observation "
                "仍看不到任何物体，未走出无参照物区域。"
            ),
        },
        {
            "step_id": "escape-east",
            "decision_source_event_ids": ["t0227:observation"],
            "action_source_event_id": "t0228:action",
            "result_source_event_ids": ["t0228:observation"],
            "outcome": "resolved",
            "semantic_change": (
                "向东移动（t0228:action）走出无参照物区域，t0228:observation 重新看到 "
                "sand/grass/tree/arrow/path/skeleton/stone，恢复可导航状态。"
            ),
        },
    ],
    "resolution_source_event_ids": ["t0228:observation"],
    "required_core_source_event_ids": [
        "t0193:action", "t0193:observation", "t0194:action", "t0203:action",
        "t0228:action", "t0228:observation",
    ],
    "optional_support_source_event_ids": [
        "t0192:observation", "t0194:observation", "t0199:observation", "t0202:observation",
        "t0203:observation", "t0209:observation", "t0220:observation", "t0227:observation",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "t0193:action", "t0193:observation", "t0194:action", "t0203:action",
                "t0228:action", "t0228:observation",
            ],
            ["t0193:action", "t0193:observation", "t0228:action", "t0228:observation"],
        ],
        "relevant_evidence_ids": [
            "t0192:observation", "t0193:action", "t0193:observation", "t0194:action",
            "t0194:observation", "t0199:observation", "t0202:observation", "t0203:action",
            "t0203:observation", "t0209:observation", "t0220:observation", "t0227:observation",
            "t0228:action", "t0228:observation",
        ],
        "causal_paths": [
            {
                "evidence_ids": [
                    "t0193:action", "t0193:observation", "t0194:action", "t0203:action",
                    "t0228:action", "t0228:observation",
                ],
                "constraints": [
                    ["t0193:action", "t0193:observation"],
                    ["t0193:observation", "t0194:action"],
                    ["t0194:action", "t0203:action"],
                    ["t0203:action", "t0228:action"],
                    ["t0228:action", "t0228:observation"],
                ],
            },
            {
                "evidence_ids": ["t0193:action", "t0193:observation", "t0228:action", "t0228:observation"],
                "constraints": [
                    ["t0193:action", "t0193:observation"],
                    ["t0193:observation", "t0228:action"],
                    ["t0228:action", "t0228:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                ["t0194:action", "t0203:action", "t0228:action", "t0228:observation"],
                ["t0203:action", "t0227:observation", "t0228:action"],
            ],
            "relevant_evidence_ids": [
                "t0193:observation", "t0194:action", "t0194:observation", "t0202:observation",
                "t0203:action", "t0220:observation", "t0227:observation", "t0228:action",
                "t0228:observation",
            ],
            "causal_paths": [
                {
                    "evidence_ids": ["t0194:action", "t0203:action", "t0228:action", "t0228:observation"],
                    "constraints": [
                        ["t0194:action", "t0203:action"],
                        ["t0203:action", "t0228:action"],
                        ["t0228:action", "t0228:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0203:action", "t0227:observation", "t0228:action"],
                    "constraints": [
                        ["t0203:action", "t0227:observation"],
                        ["t0227:observation", "t0228:action"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0192:observation", "t0193:observation", "t0228:observation"],
                ["t0193:action", "t0228:action", "t0228:observation"],
            ],
            "relevant_evidence_ids": [
                "t0192:observation", "t0193:action", "t0193:observation",
                "t0228:action", "t0228:observation",
            ],
            "causal_paths": [
                {
                    "evidence_ids": ["t0192:observation", "t0193:observation", "t0228:observation"],
                    "constraints": [
                        ["t0192:observation", "t0193:observation"],
                        ["t0193:observation", "t0228:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0193:action", "t0228:action", "t0228:observation"],
                    "constraints": [
                        ["t0193:action", "t0228:action"],
                        ["t0228:action", "t0228:observation"],
                    ],
                },
            ],
        },
    },
}


# ---------------------------------------------------------------- case 56 ---
# mypy-11912: type alias for NoReturn no longer valid as annotation.
C56 = "3ead3175d756d3b0d07e875c76751bb9e54a86a44dd174114156be6e7e05faff"

IA56 = "m0022:call:call_kC6Znao9hP2pPS5UrW58NqXs"
IR56 = "m0023:result:call_kC6Znao9hP2pPS5UrW58NqXs"
E1_56 = "m0036:call:call_bTvQ0OEF7HDHpLHQIPd6Lfxc"
E1R1_56 = "m0037:result:call_bTvQ0OEF7HDHpLHQIPd6Lfxc"
E1R2_56 = "m0039:result:call_E1iMiqQutFzStK5tRlk4HSCc"
E2_56 = "m0042:call:call_blJ0gVebcVj86nfpOpDf5RJp"
E2R1_56 = "m0043:result:call_blJ0gVebcVj86nfpOpDf5RJp"
E2R2_56 = "m0045:result:call_fjkWZxfN8yhQdacYd3a53RyG"
E3_56 = "m0048:call:call_tGJMGvnGZYxI50PvSfA6c7EL"
E3R_56 = "m0049:result:call_tGJMGvnGZYxI50PvSfA6c7EL"
F_56 = "m0050:call:call_J7khIFAsBMjrIWZ9KygO3Cw6"
FR1_56 = "m0051:result:call_J7khIFAsBMjrIWZ9KygO3Cw6"
FR2_56 = "m0052:result:call_SHavBFxcc61NCzELjQR5lX9O"

EP56 = {
    "scope": "task_level_failure_episode",
    "failure_family": "type_alias_regression",
    "recoverability": "R1",
    "error_signature": 'error: Variable \\"reproduce_error.Never\\" is not valid as a type',
    "diagnostic_evidence": (
        "m0022:call 用 mypy 检查复现脚本（Never = NoReturn 后用作类型标注），m0023:result 报 "
        "'error: Variable \"reproduce_error.Never\" is not valid as a type'（Found 1 error in 1 file，"
        "exit code 1），复现 PR 描述的回归，构成任务级初始失败；m0024:message 确认回归并指向 NoReturn "
        "的别名处理，m0030:message 定位 typeanal.py 的 typing.NoReturn 分支与 types.py 的 "
        "TypeAliasType/_expand_once 扩展逻辑。"
    ),
    "recovery_sequence": (
        "1) m0036:call 在 typeanal.py 的 NoReturn 分支加入 'main.Never'（m0037:result 编辑成功），"
        "m0039:result 复跑 mypy 仍报同一错误；"
        "2) m0042:call 修改 types.py 的 _expand_once 直接解析 NoReturn 别名（m0043:result 编辑成功），"
        "m0045:result 复跑仍报错；"
        "3) m0048:call 把硬编码别名改为 'reproduce_error.Never'（m0049:result），匹配复现脚本的实际别名全名；"
        "4) m0050:call 复跑 mypy 与 python，m0051:result 输出 Success: no issues found in 1 source file，"
        "m0052:result 脚本运行正常，m0053:message 总结回归已消除。"
    ),
    "resolution_evidence": (
        "m0051:result 为最终成功的直接观察：mypy /workspace/python__mypy__0.940/reproduce_error.py 输出 "
        "'Success: no issues found in 1 source file'（exit code 0）；m0052:result 显示脚本本身亦正常运行。"
    ),
    "anchor_source_event_id": IA56,
    "initial_action_source_event_id": IA56,
    "initial_result_source_event_ids": [IR56],
    "repair_steps": [
        {
            "step_id": "patch-typeanal-add-never",
            "decision_source_event_ids": ["m0024:message"],
            "action_source_event_id": E1_56,
            "result_source_event_ids": [E1R1_56, E1R2_56],
            "outcome": "intermediate_failure",
            "semantic_change": (
                "在 typeanal.py 的 NoReturn 分支加入 'main.Never' 别名（m0036:call→m0037:result 编辑成功），"
                "但复跑 mypy（m0039:result）仍报 reproduce_error.Never is not valid as a type，未命中真实别名全名。"
            ),
        },
        {
            "step_id": "patch-types-expand-once",
            "decision_source_event_ids": ["m0040:message"],
            "action_source_event_id": E2_56,
            "result_source_event_ids": [E2R1_56, E2R2_56],
            "outcome": "intermediate_failure",
            "semantic_change": (
                "在 types.py 的 _expand_once 中对 NoReturn 别名直接返回 UninhabitedType"
                "（m0042:call→m0043:result 编辑成功），复跑 mypy（m0045:result）仍报同一错误，修复未生效。"
            ),
        },
        {
            "step_id": "fix-alias-fullname",
            "decision_source_event_ids": ["m0046:message"],
            "action_source_event_id": E3_56,
            "result_source_event_ids": [E3R_56],
            "outcome": "intermediate_failure",
            "semantic_change": (
                "把 typeanal.py 中硬编码的 'main.Never' 改为 'reproduce_error.Never'"
                "（m0048:call→m0049:result），与复现脚本中的实际别名全名一致，但尚未验证。"
            ),
        },
        {
            "step_id": "final-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": F_56,
            "result_source_event_ids": [FR1_56, FR2_56],
            "outcome": "resolved",
            "semantic_change": (
                "复跑 mypy 与 python（m0050:call），m0051:result 显示 Success: no issues found in 1 source file"
                "（exit 0），m0052:result 脚本运行正常，NoReturn 别名回归消除。"
            ),
        },
    ],
    "resolution_source_event_ids": [FR1_56],
    "required_core_source_event_ids": [IA56, IR56, E1_56, E2_56, E3_56, F_56, FR1_56],
    "optional_support_source_event_ids": [
        "m0024:message", "m0030:message", E1R2_56, "m0040:message", E2R2_56,
        "m0046:message", FR2_56, "m0053:message",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [IA56, IR56, E1_56, E2_56, E3_56, F_56, FR1_56],
            [IA56, IR56, E3_56, F_56, FR1_56],
        ],
        "relevant_evidence_ids": [
            IA56, IR56, "m0024:message", "m0030:message", E1_56, E1R1_56, E1R2_56,
            "m0040:message", E2_56, E2R1_56, E2R2_56, "m0046:message", E3_56, E3R_56,
            F_56, FR1_56, FR2_56, "m0053:message",
        ],
        "causal_paths": [
            {
                "evidence_ids": [IA56, IR56, E1_56, E2_56, E3_56, F_56, FR1_56],
                "constraints": [
                    [IA56, IR56],
                    [IR56, E1_56],
                    [E1_56, E2_56],
                    [E2_56, E3_56],
                    [E3_56, F_56],
                    [F_56, FR1_56],
                ],
            },
            {
                "evidence_ids": [IA56, IR56, E3_56, F_56, FR1_56],
                "constraints": [
                    [IA56, IR56],
                    [IR56, E3_56],
                    [E3_56, F_56],
                    [F_56, FR1_56],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                [E1_56, E2_56, E3_56, F_56, FR1_56],
                ["m0046:message", E3_56, FR1_56],
            ],
            "relevant_evidence_ids": [
                "m0024:message", E1_56, E1R1_56, E1R2_56, "m0040:message", E2_56, E2R1_56,
                E2R2_56, "m0046:message", E3_56, E3R_56, F_56, FR1_56,
            ],
            "causal_paths": [
                {
                    "evidence_ids": [E1_56, E2_56, E3_56, F_56, FR1_56],
                    "constraints": [
                        [E1_56, E2_56],
                        [E2_56, E3_56],
                        [E3_56, F_56],
                        [F_56, FR1_56],
                    ],
                },
                {
                    "evidence_ids": ["m0046:message", E3_56, FR1_56],
                    "constraints": [
                        ["m0046:message", E3_56],
                        [E3_56, FR1_56],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                [IA56, IR56, F_56, FR1_56],
                ["m0030:message", FR1_56, "m0053:message"],
            ],
            "relevant_evidence_ids": [
                IA56, IR56, "m0030:message", F_56, FR1_56, FR2_56, "m0053:message",
            ],
            "causal_paths": [
                {
                    "evidence_ids": [IA56, IR56, F_56, FR1_56],
                    "constraints": [
                        [IA56, IR56],
                        [IR56, F_56],
                        [F_56, FR1_56],
                    ],
                },
                {
                    "evidence_ids": ["m0030:message", FR1_56, "m0053:message"],
                    "constraints": [
                        ["m0030:message", FR1_56],
                        [FR1_56, "m0053:message"],
                    ],
                },
            ],
        },
    },
}


# ---------------------------------------------------------------- case 76 ---
# hydra-1006: string interpolation broken in instantiate (init: ${foo}).
C76 = "c44bdab1f8db4205299077eebf9c1fde9d83432525699508289e9cb630451218"

IA76 = "m0040:call:call_ab5qhfPjhPtgCJdH3mMvEqD1"
IR76 = "m0041:result:call_ab5qhfPjhPtgCJdH3mMvEqD1"
E76 = "m0050:call:call_BlvHcAd4UKiMyK78URSmEhMq"
ER76 = "m0051:result:call_BlvHcAd4UKiMyK78URSmEhMq"
R76 = "m0052:call:call_mfiHP43oEbe8YfA0Gqy1OmyS"
RR76 = "m0053:result:call_mfiHP43oEbe8YfA0Gqy1OmyS"

EP76 = {
    "scope": "task_level_failure_episode",
    "failure_family": "config_interpolation_unresolved",
    "recoverability": "R1",
    "error_signature": "init: 21 * 2 = ${foo}",
    "diagnostic_evidence": (
        "m0040:call 运行 demo.py（conf/test.yaml 中 string: \"21 * 2 = ${foo}\"），m0041:result 显示 "
        "main 已解析为 42，而 hydra.utils.instantiate 触发的 init 仍输出 'init: 21 * 2 = ${foo}'，"
        "插值在 instantiate 路径未解析，复现 hydra 1.0.2 的 bug，构成任务级初始失败；"
        "m0042:message 确认该症状，m0048:message 决定在调用侧先用 "
        "OmegaConf.to_container(cfg, resolve=True) 解析配置再交给 instantiate。"
    ),
    "recovery_sequence": (
        "1) m0050:call 修改 demo.py，在 instantiate 之前加入 OmegaConf.to_container(cfg, resolve=True)"
        "（m0051:result 编辑成功）；2) m0052:call 复跑 demo.py，m0053:result 输出 'init: 21 * 2 = 42'，"
        "插值正确解析；m0054:message 确认输出符合预期。注意修复发生在调用侧 demo.py，"
        "hydra 库本身未改动，故该失败事实仍可用一次只读调用重获（R1）。"
    ),
    "resolution_evidence": (
        "m0053:result 为最终成功的直接观察：复跑 demo.py 输出 'main: 21 * 2 = 42' 与 "
        "'init: 21 * 2 = 42'（exit code 0），instantiate 收到已解析的字符串，任务级症状消除。"
    ),
    "anchor_source_event_id": IA76,
    "initial_action_source_event_id": IA76,
    "initial_result_source_event_ids": [IR76],
    "repair_steps": [
        {
            "step_id": "resolve-config-before-instantiate",
            "decision_source_event_ids": ["m0042:message", "m0048:message"],
            "action_source_event_id": E76,
            "result_source_event_ids": [ER76],
            "outcome": "intermediate_failure",
            "semantic_change": (
                "在 demo.py 调用侧对 cfg 先执行 OmegaConf.to_container(cfg, resolve=True) 再传给 "
                "instantiate（m0050:call→m0051:result 编辑成功），尚未复跑验证。"
            ),
        },
        {
            "step_id": "rerun-demo-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": R76,
            "result_source_event_ids": [RR76],
            "outcome": "resolved",
            "semantic_change": (
                "复跑 demo.py（m0052:call），m0053:result 显示 init: 21 * 2 = 42，"
                "插值问题在调用侧解决，复现程序输出与预期一致。"
            ),
        },
    ],
    "resolution_source_event_ids": [RR76],
    "required_core_source_event_ids": [IA76, IR76, E76, R76, RR76],
    "optional_support_source_event_ids": [
        "m0017:result:call_YJWGSWbmv9mEseAxHEONVzmM", "m0034:call:call_PYWiVFkVmMka4MZHE0JR85SS",
        "m0036:call:call_HMpLwVVquS7sOJkDWDJ2OqnP", "m0038:call:call_W6q41BLKpZmLo7rreGFfqHPR",
        "m0039:result:call_W6q41BLKpZmLo7rreGFfqHPR", "m0042:message", "m0048:message",
        "m0054:message",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [IA76, IR76, E76, R76, RR76],
            [IA76, IR76, RR76],
        ],
        "relevant_evidence_ids": [
            "m0017:result:call_YJWGSWbmv9mEseAxHEONVzmM", "m0034:call:call_PYWiVFkVmMka4MZHE0JR85SS",
            "m0036:call:call_HMpLwVVquS7sOJkDWDJ2OqnP", "m0038:call:call_W6q41BLKpZmLo7rreGFfqHPR",
            "m0039:result:call_W6q41BLKpZmLo7rreGFfqHPR", IA76, IR76, "m0042:message",
            "m0048:message", E76, ER76, R76, RR76, "m0054:message",
        ],
        "causal_paths": [
            {
                "evidence_ids": [IA76, IR76, E76, R76, RR76],
                "constraints": [
                    [IA76, IR76],
                    [IR76, E76],
                    [E76, R76],
                    [R76, RR76],
                ],
            },
            {
                "evidence_ids": [IA76, IR76, RR76],
                "constraints": [
                    [IA76, IR76],
                    [IR76, RR76],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                [E76, R76, RR76],
                ["m0048:message", E76, RR76],
            ],
            "relevant_evidence_ids": [
                "m0042:message", "m0048:message", E76, ER76, R76, RR76,
            ],
            "causal_paths": [
                {
                    "evidence_ids": [E76, R76, RR76],
                    "constraints": [
                        [E76, R76],
                        [R76, RR76],
                    ],
                },
                {
                    "evidence_ids": ["m0048:message", E76, RR76],
                    "constraints": [
                        ["m0048:message", E76],
                        [E76, RR76],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["m0039:result:call_W6q41BLKpZmLo7rreGFfqHPR", IA76, IR76],
                [R76, RR76, "m0054:message"],
            ],
            "relevant_evidence_ids": [
                "m0039:result:call_W6q41BLKpZmLo7rreGFfqHPR", IA76, IR76, R76, RR76,
                "m0054:message",
            ],
            "causal_paths": [
                {
                    "evidence_ids": ["m0039:result:call_W6q41BLKpZmLo7rreGFfqHPR", IA76, IR76],
                    "constraints": [
                        ["m0039:result:call_W6q41BLKpZmLo7rreGFfqHPR", IA76],
                        [IA76, IR76],
                    ],
                },
                {
                    "evidence_ids": [R76, RR76, "m0054:message"],
                    "constraints": [
                        [R76, RR76],
                        [RR76, "m0054:message"],
                    ],
                },
            ],
        },
    },
}


# ---------------------------------------------------------------- case 96 ---
# moto-5478: mock_rds missing DbInstancePort; trajectory truncated before final verify.
C96 = "9531104da72e9843af0a7d38bd6a2a8a2d86c190862875efe6bf4d6a89570755"

REJECT96 = (
    "轨迹在最后一次验证处截断：初始失败可定位（m0048:call 运行 reproduce_error.py，"
    "m0049:result 报 AssertionError：修改端口后返回的 DbInstancePort 仍为 1234 之外/缺失），"
    "随后 m0044:call 为 Database.to_xml 增加 <DbInstancePort>、m0054:call 在 modify_db_instance "
    "中补写 database.port，两条修复动作均有结果；但末条事件 m0056:call（再次运行 "
    "reproduce_error.py 验证）之后没有任何返回事件，最终成功从未被直接观察，"
    "无法构成'初始失败-修复-成功直接观察'的完整任务级 episode，宁缺毋滥判 rejected。"
)


def main() -> None:
    A.write_row(OUT, C16, annotator=ANNO, status="annotated", episode=EP16)
    A.write_row(OUT, C36, annotator=ANNO, status="annotated", episode=EP36)
    A.write_row(OUT, C56, annotator=ANNO, status="annotated", episode=EP56)
    A.write_row(OUT, C76, annotator=ANNO, status="annotated", episode=EP76)
    A.write_row(OUT, C96, annotator=ANNO, status="rejected", rejection_reason=REJECT96)
    errors = A.check_file(OUT)
    for e in errors:
        print("ERROR", e)
    print("checked", OUT, "->", len(errors), "errors")


if __name__ == "__main__":
    main()
