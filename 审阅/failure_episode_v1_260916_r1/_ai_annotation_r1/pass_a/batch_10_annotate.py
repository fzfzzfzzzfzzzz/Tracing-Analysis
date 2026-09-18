# -*- coding: utf-8 -*-
"""Pass A annotations for batch_10 (cases 47-51). Writes pass_a/batch_10.jsonl."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anno_lib as A

OUT = Path(__file__).parent / "batch_10.jsonl"
ANNO = "ai-draft:zcode:glm-5.2:pass-a"

C47 = "1c91155cfe7ded2c8b5656060f8311679f9fdc4a5237fc100a93990e171b5c8f"
C48 = "1f3abaffe3b1257e16134eb7d270835cab39bdd545bdd3c3b8977753746ff848"
C49 = "239fe999d3911906cf28e46353c6811e9bdd5027a327208c93ea75eeec13e273"
C50 = "296308d848cd59d7d1e29a0d624bcb12dcb75ab531d5e6c1546afc17a51ea537"
C51 = "2c3b865e65c2e7ad72d7c588959d3b8ff1873c362386719f3147fbf48f28dbe9"

# ---------------- case 47 (conan: feature verify script fails with TypeError) ----------------
INIT47 = "m0040:call:call_OXmIm3cvPBV9yfJr0YLwa8Jl"
IRES47 = "m0041:result:call_OXmIm3cvPBV9yfJr0YLwa8Jl"
CREATED47 = "m0039:result:call_3xyX7EXYeLvp9qoQ9RgRYwmt"
DIAG47 = "m0042:message"
FIX47 = "m0042:call:call_mRbxxXl2sVwxakXtBEu6nfWA"
FIXRES47 = "m0043:result:call_mRbxxXl2sVwxakXtBEu6nfWA"
RERUN47 = "m0044:call:call_VMTA8ywYX6B0yXWPv8m56owO"
OK47 = "m0045:result:call_VMTA8ywYX6B0yXWPv8m56owO"

EP47 = {
    "scope": "task_level_failure_episode",
    "failure_family": "parameter_schema",
    "recoverability": "R0",
    "error_signature": "TypeError: ConanApp.__init__() missing 1 required positional argument: 'cache_folder'",
    "diagnostic_evidence": (
        "m0041:result 显示自建验证脚本 test_upload_metadata.py 首次运行即抛 "
        "TypeError: ConanApp.__init__() missing 1 required positional argument: 'cache_folder'"
        "（exit code 1），刚实现的 --metadata=\"\" 跳过上传功能无法被验证，构成任务级初始失败。"
        "根因在验证脚本而非功能代码：m0042:message 诊断出脚本第 19 行以无参方式实例化 ConanApp，"
        "而 conan.internal.conan_app.ConanApp 的构造函数要求必填的 cache_folder。"
    ),
    "recovery_sequence": (
        "1) 修改测试脚本，把 ConanApp() 改为 ConanApp(cache_folder=\"/tmp/mock_cache\")"
        "（m0042:call→m0043:result）；"
        "2) 重新运行 test_upload_metadata.py（m0044:call），exit code 0，"
        "输出 Skipping metadata upload as per user request. 与 Test passed: Metadata upload was skipped."
        "（m0045:result），跳过元数据上传的行为得到验证。"
    ),
    "resolution_evidence": (
        "m0045:result：重跑验证脚本 exit code 0，直接观察到 Skipping metadata upload as per user request. "
        "与 Test passed: Metadata upload was skipped.，确认 UploadExecutor 在 metadata 为空字符串时跳过上传，任务完成。"
    ),
    "anchor_source_event_id": INIT47,
    "initial_action_source_event_id": INIT47,
    "initial_result_source_event_ids": [IRES47],
    "repair_steps": [
        {
            "step_id": "fix-test-script",
            "decision_source_event_ids": [DIAG47],
            "action_source_event_id": FIX47,
            "result_source_event_ids": [FIXRES47],
            "outcome": "intermediate_failure",
            "semantic_change": "将验证脚本中的 ConanApp() 改为 ConanApp(cache_folder=\"/tmp/mock_cache\")（m0042:call→m0043:result），消除 TypeError 的直接原因，但功能行为尚未重新验证。",
        },
        {
            "step_id": "rerun-verify",
            "decision_source_event_ids": [],
            "action_source_event_id": RERUN47,
            "result_source_event_ids": [OK47],
            "outcome": "resolved",
            "semantic_change": "重新运行 test_upload_metadata.py（m0044:call），exit code 0 并输出 Skipping metadata upload as per user request. 与 Test passed: Metadata upload was skipped.（m0045:result），--metadata=\"\" 跳过上传的功能验证通过。",
        },
    ],
    "resolution_source_event_ids": [OK47],
    "required_core_source_event_ids": [INIT47, IRES47, FIX47, RERUN47, OK47],
    "optional_support_source_event_ids": [CREATED47, DIAG47, FIXRES47],
    "chain_policy": {
        "alternative_evidence_sets": [
            [INIT47, IRES47, FIX47, RERUN47, OK47],
            [INIT47, IRES47, RERUN47, OK47],
        ],
        "relevant_evidence_ids": [CREATED47, INIT47, IRES47, DIAG47, FIX47, FIXRES47, RERUN47, OK47],
        "causal_paths": [
            {
                "evidence_ids": [INIT47, IRES47, FIX47, RERUN47, OK47],
                "constraints": [
                    [INIT47, IRES47],
                    [IRES47, FIX47],
                    [FIX47, RERUN47],
                    [RERUN47, OK47],
                ],
            },
            {
                "evidence_ids": [INIT47, IRES47, RERUN47, OK47],
                "constraints": [
                    [INIT47, IRES47],
                    [IRES47, RERUN47],
                    [RERUN47, OK47],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                [FIX47, FIXRES47, OK47],
                [DIAG47, FIX47, OK47],
            ],
            "relevant_evidence_ids": [DIAG47, FIX47, FIXRES47, RERUN47, OK47],
            "causal_paths": [
                {
                    "evidence_ids": [FIX47, FIXRES47, OK47],
                    "constraints": [
                        [FIX47, FIXRES47],
                        [FIXRES47, OK47],
                    ],
                },
                {
                    "evidence_ids": [DIAG47, FIX47, OK47],
                    "constraints": [
                        [DIAG47, FIX47],
                        [FIX47, OK47],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                [RERUN47, OK47],
                [INIT47, RERUN47, OK47],
            ],
            "relevant_evidence_ids": [INIT47, IRES47, RERUN47, OK47],
            "causal_paths": [
                {
                    "evidence_ids": [RERUN47, OK47],
                    "constraints": [[RERUN47, OK47]],
                },
                {
                    "evidence_ids": [INIT47, RERUN47, OK47],
                    "constraints": [
                        [INIT47, RERUN47],
                        [RERUN47, OK47],
                    ],
                },
            ],
        },
    },
}

# ---------------- case 48 (mypy: __qualname__ not defined) ----------------
INIT48 = "m0012:call:call_P2ebbaqk61h0Zv73GOvBayWL"
IRES48 = "m0013:result:call_P2ebbaqk61h0Zv73GOvBayWL"
REPRO48 = "m0014:message"
LOC48 = "m0044:message"
LOC48b = "m0050:message"
FIX48 = "m0050:call:call_lKhJWyLRhvW8zxpS5eVQ5eEg"
FIXRES48 = "m0051:result:call_lKhJWyLRhvW8zxpS5eVQ5eEg"
RERUN48 = "m0052:call:call_EgHDpS0hgU4YyuN0xplYznPY"
OK48 = "m0053:result:call_EgHDpS0hgU4YyuN0xplYznPY"
SUMMARY48 = "m0054:message"

EP48 = {
    "scope": "task_level_failure_episode",
    "failure_family": "missing_implicit_module_attr",
    "recoverability": "R0",
    "error_signature": "Found 1 error in 1 file (checked 1 source file)",
    "diagnostic_evidence": (
        "m0013:result 显示 mypy 对 test1.py（class A: NAME = __qualname__）报告 "
        "error: Name \"__qualname__\" is not defined  [name-defined]，Found 1 error in 1 file，exit code 1，"
        "即 PR 所述 mypy 不识别 __qualname__ 的任务级初始失败；m0014:message 确认已复现。"
        "根因由 m0044:message 与 m0050:message 定位：mypy/nodes.py 的 implicit_module_attrs 字典"
        "未注册 __qualname__，语义分析的隐式模块属性表因此缺少该名字。"
    ),
    "recovery_sequence": (
        "1) 在 mypy/nodes.py 的 implicit_module_attrs 字典中新增 \"__qualname__\": \"__builtins__.str\""
        "（m0050:call→m0051:result）；"
        "2) 重新运行 mypy test1.py（m0052:call），输出 Success: no issues found in 1 source file、exit code 0"
        "（m0053:result），__qualname__ 未定义错误消除。"
    ),
    "resolution_evidence": (
        "m0053:result：同一命令复跑返回 Success: no issues found in 1 source file 且 exit code 0，"
        "__qualname__ 被识别为合法隐式模块属性，任务级失败在当前实现中被最终确认修复。"
    ),
    "anchor_source_event_id": INIT48,
    "initial_action_source_event_id": INIT48,
    "initial_result_source_event_ids": [IRES48],
    "repair_steps": [
        {
            "step_id": "add-qualname-attr",
            "decision_source_event_ids": [LOC48, LOC48b],
            "action_source_event_id": FIX48,
            "result_source_event_ids": [FIXRES48],
            "outcome": "intermediate_failure",
            "semantic_change": "在 mypy/nodes.py 的 implicit_module_attrs 中新增 \"__qualname__\": \"__builtins__.str\"（m0050:call→m0051:result），使 __qualname__ 进入隐式模块属性表，但尚未重新运行 mypy 验证。",
        },
        {
            "step_id": "rerun-mypy",
            "decision_source_event_ids": [],
            "action_source_event_id": RERUN48,
            "result_source_event_ids": [OK48],
            "outcome": "resolved",
            "semantic_change": "重新运行 mypy test1.py（m0052:call），Success: no issues found in 1 source file、exit code 0（m0053:result），__qualname__ 误报消除。",
        },
    ],
    "resolution_source_event_ids": [OK48],
    "required_core_source_event_ids": [INIT48, IRES48, FIX48, RERUN48, OK48],
    "optional_support_source_event_ids": [REPRO48, LOC48, FIXRES48, SUMMARY48],
    "chain_policy": {
        "alternative_evidence_sets": [
            [INIT48, IRES48, FIX48, RERUN48, OK48],
            [INIT48, IRES48, FIX48, OK48],
        ],
        "relevant_evidence_ids": [
            INIT48, IRES48, REPRO48, LOC48, LOC48b, FIX48, FIXRES48, RERUN48, OK48, SUMMARY48,
        ],
        "causal_paths": [
            {
                "evidence_ids": [INIT48, IRES48, FIX48, RERUN48, OK48],
                "constraints": [
                    [INIT48, IRES48],
                    [IRES48, FIX48],
                    [FIX48, RERUN48],
                    [RERUN48, OK48],
                ],
            },
            {
                "evidence_ids": [INIT48, IRES48, FIX48, OK48],
                "constraints": [
                    [INIT48, IRES48],
                    [IRES48, FIX48],
                    [FIX48, OK48],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                [FIX48, FIXRES48, OK48],
                [LOC48b, FIX48, OK48],
            ],
            "relevant_evidence_ids": [LOC48, LOC48b, FIX48, FIXRES48, RERUN48, OK48],
            "causal_paths": [
                {
                    "evidence_ids": [FIX48, FIXRES48, OK48],
                    "constraints": [
                        [FIX48, FIXRES48],
                        [FIXRES48, OK48],
                    ],
                },
                {
                    "evidence_ids": [LOC48b, FIX48, OK48],
                    "constraints": [
                        [LOC48b, FIX48],
                        [FIX48, OK48],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                [RERUN48, OK48],
                [INIT48, RERUN48, OK48],
            ],
            "relevant_evidence_ids": [INIT48, IRES48, RERUN48, OK48],
            "causal_paths": [
                {
                    "evidence_ids": [RERUN48, OK48],
                    "constraints": [[RERUN48, OK48]],
                },
                {
                    "evidence_ids": [INIT48, RERUN48, OK48],
                    "constraints": [
                        [INIT48, RERUN48],
                        [RERUN48, OK48],
                    ],
                },
            ],
        },
    },
}

# ---------------- case 50 (pandas: non-nano + freq='D' ValueError) ----------------
INIT50 = "m0008:call:call_eE9sWQxBylVaPnDjHXllUEXm"
IRES50 = "m0009:result:call_eE9sWQxBylVaPnDjHXllUEXm"
REPRO50 = "m0010:message"
VIEWFAIL50 = "m0011:result:call_DfNmzrdc93tpDlHUtP3kdH1w"
D50A = "m0012:message"
D50B = "m0014:message"
A50A = "m0014:call:call_ubDs5AKZ5hPWXuIJ0BK7NX9X"
R50A = "m0015:result:call_ubDs5AKZ5hPWXuIJ0BK7NX9X"
D50C = "m0016:message"
A50B = "m0016:call:call_ZOlhoEvryM7Qri9TF3cWSuUc"
R50B = "m0017:result:call_ZOlhoEvryM7Qri9TF3cWSuUc"
D50D = "m0018:message"
VIEW50 = "m0018:call:call_4WpqkPPpFSBevVWTW8kfDwEC"
VIEWRES50 = "m0019:result:call_4WpqkPPpFSBevVWTW8kfDwEC"
D50E = "m0020:message"
A50C = "m0020:call:call_cjxF2johgAG1dtQHjrOZp8dc"
R50C = "m0021:result:call_cjxF2johgAG1dtQHjrOZp8dc"
D50F = "m0022:message"
RERUN50 = "m0022:call:call_zsC1OX5c1LWteqnzImJkNr8M"
OK50 = "m0023:result:call_zsC1OX5c1LWteqnzImJkNr8M"

EP50 = {
    "scope": "task_level_failure_episode",
    "failure_family": "freq_validation_logic_error",
    "recoverability": "R0",
    "error_signature": "Inferred frequency None from passed values does not conform to passed frequency D",
    "diagnostic_evidence": (
        "m0009:result 显示 reproduce_error.py（DatetimeIndex(date_range('2000', periods=2).as_unit('s')"
        ".normalize(), freq='D')）在 /testbed/pandas/core/arrays/datetimelike.py 第 1914 行 _validate_frequency "
        "抛出 ValueError: Inferred frequency None from passed values does not conform to passed frequency D"
        "（exit code 1），即 PR 所述 non-nano 值配 freq='D' 误报的任务级初始失败；m0010:message 确认复现，"
        "m0012:message 与 m0014:message 定位到 _validate_frequency 对秒级（'s'）单位推断频率与 'D' 的校验分支。"
    ),
    "recovery_sequence": (
        "1) 首次 str_replace 因 old_str `raise ValueError(` 在文件多处出现被拒绝（m0014:call→m0015:result）；"
        "2) 第二次替换 old_str 与文件文本不逐字匹配再次失败（m0016:call→m0017:result）；"
        "3) 查看 1900-1920 行精确文本（m0018:call→m0019:result）后，以带缩进上下文的 str_replace 在 "
        "_validate_frequency 中加入 's' 单位与 'D' 的 86400 秒兼容分支并成功落盘（m0020:call→m0021:result）；"
        "4) 重跑 reproduce_error.py，exit code 0 且无异常（m0022:call→m0023:result）。"
    ),
    "resolution_evidence": (
        "m0023:result：同一复现脚本复跑 exit code 0、无任何 traceback 输出，non-nano（'s'）DatetimeIndex "
        "配 freq='D' 不再抛 ValueError，修复在当前环境被直接观察。"
    ),
    "anchor_source_event_id": INIT50,
    "initial_action_source_event_id": INIT50,
    "initial_result_source_event_ids": [IRES50],
    "repair_steps": [
        {
            "step_id": "patch-attempt-ambiguous-oldstr",
            "decision_source_event_ids": [D50A, D50B],
            "action_source_event_id": A50A,
            "result_source_event_ids": [R50A],
            "outcome": "intermediate_failure",
            "semantic_change": "首次尝试向 _validate_frequency 插入兼容分支，但 old_str `    raise ValueError(` 在 datetimelike.py 中有 11 处匹配，替换被拒绝（m0015:result），补丁未落盘。",
        },
        {
            "step_id": "patch-attempt-nonverbatim-oldstr",
            "decision_source_event_ids": [D50C],
            "action_source_event_id": A50B,
            "result_source_event_ids": [R50B],
            "outcome": "intermediate_failure",
            "semantic_change": "第二次替换把 old_str 扩展为多行 raise ValueError 块，但与文件实际文本不逐字匹配，仍未替换（m0017:result）。",
        },
        {
            "step_id": "patch-validate-frequency",
            "decision_source_event_ids": [D50D, VIEWRES50, D50E],
            "action_source_event_id": A50C,
            "result_source_event_ids": [R50C],
            "outcome": "intermediate_failure",
            "semantic_change": "基于 1900-1920 行精确文本（m0019:result），以带正确缩进上下文的 str_replace 在 _validate_frequency 中加入 's' 单位与 'D' 的 86400 秒兼容判断，替换成功落盘（m0021:result），但行为尚未验证。",
        },
        {
            "step_id": "rerun-verify",
            "decision_source_event_ids": [D50F],
            "action_source_event_id": RERUN50,
            "result_source_event_ids": [OK50],
            "outcome": "resolved",
            "semantic_change": "重跑 reproduce_error.py（m0022:call），exit code 0 且无 traceback（m0023:result），non-nano + freq='D' 的 ValueError 消除。",
        },
    ],
    "resolution_source_event_ids": [OK50],
    "required_core_source_event_ids": [INIT50, IRES50, A50C, RERUN50, OK50],
    "optional_support_source_event_ids": [REPRO50, VIEWFAIL50, R50A, R50B, VIEWRES50, R50C],
    "chain_policy": {
        "alternative_evidence_sets": [
            [INIT50, IRES50, A50C, RERUN50, OK50],
            [INIT50, IRES50, A50C, OK50],
        ],
        "relevant_evidence_ids": [
            INIT50, IRES50, REPRO50, VIEWFAIL50, D50A, D50B, A50A, R50A, D50C, A50B, R50B,
            D50D, VIEWRES50, D50E, A50C, R50C, D50F, RERUN50, OK50,
        ],
        "causal_paths": [
            {
                "evidence_ids": [INIT50, IRES50, A50C, RERUN50, OK50],
                "constraints": [
                    [INIT50, IRES50],
                    [IRES50, A50C],
                    [A50C, RERUN50],
                    [RERUN50, OK50],
                ],
            },
            {
                "evidence_ids": [INIT50, IRES50, A50C, OK50],
                "constraints": [
                    [INIT50, IRES50],
                    [IRES50, A50C],
                    [A50C, OK50],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                [A50A, R50A, A50C, R50C, RERUN50, OK50],
                [A50C, R50C, OK50],
            ],
            "relevant_evidence_ids": [
                D50A, D50B, A50A, R50A, D50C, A50B, R50B, D50D, VIEWRES50, D50E, A50C, R50C,
                D50F, RERUN50, OK50,
            ],
            "causal_paths": [
                {
                    "evidence_ids": [A50A, R50A, A50C, R50C, RERUN50, OK50],
                    "constraints": [
                        [A50A, R50A],
                        [R50A, A50C],
                        [A50C, R50C],
                        [R50C, RERUN50],
                        [RERUN50, OK50],
                    ],
                },
                {
                    "evidence_ids": [A50C, R50C, OK50],
                    "constraints": [
                        [A50C, R50C],
                        [R50C, OK50],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                [VIEW50, VIEWRES50, RERUN50, OK50],
                [RERUN50, OK50],
            ],
            "relevant_evidence_ids": [INIT50, IRES50, VIEW50, VIEWRES50, RERUN50, OK50],
            "causal_paths": [
                {
                    "evidence_ids": [VIEW50, VIEWRES50, RERUN50, OK50],
                    "constraints": [
                        [VIEW50, VIEWRES50],
                        [VIEWRES50, RERUN50],
                        [RERUN50, OK50],
                    ],
                },
                {
                    "evidence_ids": [RERUN50, OK50],
                    "constraints": [[RERUN50, OK50]],
                },
            ],
        },
    },
}


def main() -> None:
    A.write_row(OUT, C47, annotator=ANNO, status="annotated", episode=EP47)
    A.write_row(OUT, C48, annotator=ANNO, status="annotated", episode=EP48)
    A.write_row(
        OUT, C49, annotator=ANNO, status="rejected",
        rejection_reason=(
            "轨迹在探索阶段即截断：除对根目录的重复 view 外，仅有两次 bokeh 子目录不存在的局部小失败"
            "（m0005:result、m0007:result），且最后一个事件 m0014:call 没有对应结果；"
            "全程没有任务级失败、修复动作或最终成功观察，无法构成完整 episode。"
        ),
    )
    A.write_row(OUT, C50, annotator=ANNO, status="annotated", episode=EP50)
    A.write_row(
        OUT, C51, annotator=ANNO, status="rejected",
        rejection_reason=(
            "初始任务级失败可定位（m0023:result 显示 float[pyarrow] 输入的 DataFrame.dot/Series.dot "
            "输出退化为 object dtype，bug 已复现），但轨迹在修复开始处截断：唯一的源码修复动作 m0095:call"
            "（修改 pandas/core/internals/ops.py 的 operate_blockwise 以保留 Arrow dtype）是最后一个事件，"
            "无结果、无验证运行，最终成功从未被观察到。"
        ),
    )
    errs = A.check_file(OUT)
    for e in errs:
        print("ERROR", e)
    print("checked", OUT, "->", len(errs), "errors")


if __name__ == "__main__":
    main()
