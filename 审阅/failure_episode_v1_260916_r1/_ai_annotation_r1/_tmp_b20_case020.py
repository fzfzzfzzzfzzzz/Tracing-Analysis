import anno_lib as A

CID = "f9e1b1410798b28b9b451477881c005ff21b2854206afbfd3e99af600a273dbf"
OUT = r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1\pass_b\batch_20.jsonl"

EPISODE = {
    "scope": "task_level_failure_episode",
    "failure_family": "parameter_schema",
    "recoverability": "R0",
    "error_signature": "TypeError: missing a required argument: 'spatial_dims'",
    "diagnostic_evidence": (
        "任务要求为 bundle config 增加 _post_action_ 实例化后执行逻辑，代理已在 "
        "m0018:call/m0024:call 修改 monai/bundle/config_item.py 并用 m0026:call 创建 "
        "reproduce_error.py 验证脚本。m0028:call 首次运行该脚本，m0029:result 直接给出"
        "任务级初始失败：UNet 组件实例化抛出 TypeError: missing a required argument: "
        "'spatial_dims'。m0030:message 诊断出脚本配置里把必填参数误写为 dimensions；"
        "后续 m0031:result、m0035:result、m0039:result 逐次暴露还缺 channels 与 strides，"
        "说明失败根因是验证脚本对 monai.networks.nets.UNet 参数 schema 的错误假设，"
        "而非 _post_action_ 实现本身。"
    ),
    "recovery_sequence": (
        "1) 按诊断将配置参数名 dimensions 改为 spatial_dims 并重跑，仍报缺少 channels"
        "（m0030:call→m0031:result→m0033:result）；"
        "2) 配置中补上 channels=(16,32) 并重跑，仍报缺少 strides"
        "（m0034:call→m0035:result→m0037:result）；"
        "3) 再补 strides=(1,1) 并重跑，脚本以 exit code 0 成功运行，输出 Running "
        "destroy_ddp_group on UNet(...)，post_action 在实例化后被执行"
        "（m0038:call→m0039:result→m0041:result）。"
    ),
    "resolution_evidence": (
        "m0041:result 显示复现脚本最终运行成功（[Command finished with exit code 0]），"
        "并直接观察到 dummy_ddp_init 的事前要求和 dummy_destroy_ddp_group 的 "
        "_post_action_ 都按预期执行（Running destroy_ddp_group on UNet(...)），"
        "验证了 bundle config 逻辑增强在 /testbed 运行时生效。"
    ),
    "anchor_source_event_id": "m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm",
    "initial_action_source_event_id": "m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm",
    "initial_result_source_event_ids": ["m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm"],
    "repair_steps": [
        {
            "step_id": "fix-param-name",
            "decision_source_event_ids": ["m0030:message"],
            "action_source_event_id": "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
            "result_source_event_ids": [
                "m0031:result:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr",
            ],
            "outcome": "intermediate_failure",
            "semantic_change": "将复现脚本中 UNet 配置的错误参数名 dimensions 改为 spatial_dims，重跑后实例化仍失败，新报缺少必填参数 channels。",
        },
        {
            "step_id": "add-channels",
            "decision_source_event_ids": ["m0034:message"],
            "action_source_event_id": "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
            "result_source_event_ids": [
                "m0035:result:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2",
            ],
            "outcome": "intermediate_failure",
            "semantic_change": "在配置中补充 channels=(16,32)，重跑后 UNet 实例化仍失败，继续报缺少必填参数 strides。",
        },
        {
            "step_id": "add-strides-and-verify",
            "decision_source_event_ids": ["m0038:message"],
            "action_source_event_id": "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
            "result_source_event_ids": [
                "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1",
                "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
            ],
            "outcome": "resolved",
            "semantic_change": "在配置中补充 strides=(1,1) 后重跑，脚本 exit code 0 成功，输出 Running destroy_ddp_group on UNet(...)，_post_action_ 增强在正式运行时得到直接确认。",
        },
    ],
    "resolution_source_event_ids": ["m0041:result:call_gUREFk68phtf2DnN71YHuwyz"],
    "required_core_source_event_ids": [
        "m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm",
        "m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm",
        "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
        "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr",
        "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
        "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2",
        "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
        "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
    ],
    "optional_support_source_event_ids": [
        "m0018:call:call_4nyJdPAVAF4gWKmNTdtC5XLw",
        "m0019:result:call_4nyJdPAVAF4gWKmNTdtC5XLw",
        "m0024:call:call_6C7hY4cJslBrudglkvnBrCHH",
        "m0025:result:call_6C7hY4cJslBrudglkvnBrCHH",
        "m0030:message",
        "m0034:message",
        "m0038:message",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm",
                "m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm",
                "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr",
                "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2",
                "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
            ],
            [
                "m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm",
                "m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm",
                "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
            ],
        ],
        "relevant_evidence_ids": [
            "m0018:call:call_4nyJdPAVAF4gWKmNTdtC5XLw",
            "m0019:result:call_4nyJdPAVAF4gWKmNTdtC5XLw",
            "m0024:call:call_6C7hY4cJslBrudglkvnBrCHH",
            "m0025:result:call_6C7hY4cJslBrudglkvnBrCHH",
            "m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm",
            "m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm",
            "m0030:message",
            "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
            "m0031:result:call_yXyu1uIaVtS6CQfZDMfkY4h1",
            "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr",
            "m0034:message",
            "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
            "m0035:result:call_AsS9FX6QZuE9pd4TT2CFN4ok",
            "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2",
            "m0038:message",
            "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
            "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1",
            "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
        ],
        "causal_paths": [
            {
                "evidence_ids": [
                    "m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm",
                    "m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm",
                    "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                    "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr",
                    "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                    "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2",
                    "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                    "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                ],
                "constraints": [
                    ["m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm", "m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm"],
                    ["m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm", "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1"],
                    ["m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1", "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr"],
                    ["m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr", "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok"],
                    ["m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok", "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2"],
                    ["m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2", "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1"],
                    ["m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1", "m0041:result:call_gUREFk68phtf2DnN71YHuwyz"],
                ],
            },
            {
                "evidence_ids": [
                    "m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm",
                    "m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm",
                    "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                    "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                    "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                    "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                ],
                "constraints": [
                    ["m0028:call:call_tzUWtfcgwKga6aTs5pGIe3pm", "m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm"],
                    ["m0029:result:call_tzUWtfcgwKga6aTs5pGIe3pm", "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1"],
                    ["m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1", "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok"],
                    ["m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok", "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1"],
                    ["m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1", "m0041:result:call_gUREFk68phtf2DnN71YHuwyz"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                [
                    "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                    "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr",
                    "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                    "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2",
                    "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                    "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                ],
                [
                    "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                    "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                    "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                ],
            ],
            "relevant_evidence_ids": [
                "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                "m0031:result:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr",
                "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                "m0035:result:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2",
                "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1",
                "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
            ],
            "causal_paths": [
                {
                    "evidence_ids": [
                        "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                        "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr",
                        "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                        "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2",
                        "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                        "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                    ],
                    "constraints": [
                        ["m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1", "m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr"],
                        ["m0033:result:call_Lxo5zubnvtB81KshSC1LXTvr", "m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok"],
                        ["m0034:call:call_AsS9FX6QZuE9pd4TT2CFN4ok", "m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2"],
                        ["m0037:result:call_1zGAIrXhsnqLjStfQ6Vvl1O2", "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1"],
                        ["m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1", "m0041:result:call_gUREFk68phtf2DnN71YHuwyz"],
                    ],
                },
                {
                    "evidence_ids": [
                        "m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                        "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1",
                        "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                    ],
                    "constraints": [
                        ["m0030:call:call_yXyu1uIaVtS6CQfZDMfkY4h1", "m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1"],
                        ["m0038:call:call_05FS6LQRy39Ri2uvcTVcLkK1", "m0041:result:call_gUREFk68phtf2DnN71YHuwyz"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                [
                    "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1",
                    "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                ],
                [
                    "m0031:result:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                    "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1",
                    "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                ],
            ],
            "relevant_evidence_ids": [
                "m0031:result:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                "m0035:result:call_AsS9FX6QZuE9pd4TT2CFN4ok",
                "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1",
                "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
            ],
            "causal_paths": [
                {
                    "evidence_ids": [
                        "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1",
                        "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                    ],
                    "constraints": [
                        ["m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1", "m0041:result:call_gUREFk68phtf2DnN71YHuwyz"],
                    ],
                },
                {
                    "evidence_ids": [
                        "m0031:result:call_yXyu1uIaVtS6CQfZDMfkY4h1",
                        "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1",
                        "m0041:result:call_gUREFk68phtf2DnN71YHuwyz",
                    ],
                    "constraints": [
                        ["m0031:result:call_yXyu1uIaVtS6CQfZDMfkY4h1", "m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1"],
                        ["m0039:result:call_05FS6LQRy39Ri2uvcTVcLkK1", "m0041:result:call_gUREFk68phtf2DnN71YHuwyz"],
                    ],
                },
            ],
        },
    },
}

if __name__ == "__main__":
    A.check_episode(EPISODE, CID)
    A.write_row(OUT, CID, annotator="ai-draft:zcode:glm-5.2:pass-b", status="annotated", episode=EPISODE)
    print("case 020 OK")
