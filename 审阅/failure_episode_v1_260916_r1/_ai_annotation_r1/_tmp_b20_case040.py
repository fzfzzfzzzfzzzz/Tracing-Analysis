import anno_lib as A

CID = "e374ac5be6713fee72b52f08b029e5e180712f13c25cb26352ef94ef7f37fd4f"
OUT = r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1\pass_b\batch_20.jsonl"

EPISODE = {
    "scope": "task_level_failure_episode",
    "failure_family": "web_access_blocked",
    "recoverability": "R1",
    "error_signature": "'text_content': '', 'images': [], 'background_images': [], 'total_images': 0",
    "diagnostic_evidence": (
        "任务需要扫描 Eva Draconis 个人网站 orionmindproject.com 顶部横幅并辨识其中唯一带弧线的符号。"
        "t0010:action 首次用 fetch_website_content_with_images 抓取站点，t0010:observation 显示"
        "text_content 为空、total_images 为 0，横幅扫描这一任务级目标直接失败。"
        "随后 t0011:observation/t0018:observation 证明换用 http/https、有无 www、images-only 等"
        "变体仍为空；t0035:observation 通过内页 reticuli.html 目录确认横幅资产为 pic/spacelogo4.bmp；"
        "t0039:observation 显示直接下载该 bmp 被 465 Client Error 拒绝，t0040:observation 显示"
        "加 UA/Referer 头重试变 404；t0041:observation 将 step 1 标记 blocked，"
        "诊断结论是站点服务器端屏蔽了直接抓取，必须换数据源。"
    ),
    "recovery_sequence": (
        "1) 更换 URL 变体（http/https、www/非 www、images-only）反复抓取，全部为空"
        "（t0011:action→t0011:observation、t0018:observation）；"
        "2) 依据 reticuli.html 的图片目录（t0035:observation）用 execute_code 直接下载"
        "spacelogo4.bmp，遭 465，加浏览器 UA/Referer 重试仍 404（t0039:action→t0039:observation、"
        "t0040:observation）；"
        "3) 搜索定位 Wayback 2024-03-24 快照（t0050:observation），抓取快照页恢复站点内容与"
        "16 个图片引用（t0051:action→t0051:observation）；"
        "4) 经快照 im_ 地址直接取回 spacelogo4.bmp 位图二进制（t0053:action→t0053:observation）；"
        "5) 结合内页 Reticulan writing 词典交叉验证（t0087:observation），确认唯一的非圆弧线符号"
        "（U 形碗）含义为 container，完成验证记录（t0089:action→t0089:observation），"
        "随后提交最终答案（t0090:action）。"
    ),
    "resolution_evidence": (
        "t0089:observation 直接记录了最终结论：on-site 页面 reticulanwriting.html 定义 U 形碗/容器"
        "表示 container，唯一的非圆弧线横幅符号含义被验证为 container，最终答案已按要求准备并提交"
        "（t0090:action FINAL_ANSWER: container）。"
    ),
    "anchor_source_event_id": "t0010:action",
    "initial_action_source_event_id": "t0010:action",
    "initial_result_source_event_ids": ["t0010:observation"],
    "repair_steps": [
        {
            "step_id": "retry-fetch-variants",
            "decision_source_event_ids": ["t0009:observation"],
            "action_source_event_id": "t0011:action",
            "result_source_event_ids": ["t0011:observation", "t0018:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "换用 http/https、www/非 www 与 images-only 等变体反复抓取站点，观测仍为空文本、0 图片，横幅依旧无法扫描。",
        },
        {
            "step_id": "direct-asset-download",
            "decision_source_event_ids": ["t0035:observation"],
            "action_source_event_id": "t0039:action",
            "result_source_event_ids": ["t0039:observation", "t0040:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "从内页图片目录锁定横幅资产 pic/spacelogo4.bmp 并用 execute_code 直接下载，先遭 465 Client Error，加浏览器 UA/Referer 头重试又变 404，直接下载路径被服务器配置封死。",
        },
        {
            "step_id": "wayback-snapshot",
            "decision_source_event_ids": ["t0050:observation"],
            "action_source_event_id": "t0051:action",
            "result_source_event_ids": ["t0051:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "转向 Wayback Machine，抓取 2024-03-24 快照成功恢复站点文本内容与 16 个图片引用，站点内容可读，但弧线符号的具体含义仍未确定。",
        },
        {
            "step_id": "fetch-banner-binary",
            "decision_source_event_ids": ["t0051:observation"],
            "action_source_event_id": "t0053:action",
            "result_source_event_ids": ["t0053:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "经快照 im_ 地址直接取回 spacelogo4.bmp 的位图二进制（BM 头原始字节流），横幅资产本体已重获，但二进制无法直接读出符号含义。",
        },
        {
            "step_id": "derive-validate-submit",
            "decision_source_event_ids": ["t0087:observation"],
            "action_source_event_id": "t0089:action",
            "result_source_event_ids": ["t0089:observation"],
            "outcome": "resolved",
            "semantic_change": "用内页 Reticulan writing 词典交叉验证，确认横幅中唯一非圆弧线符号（U 形碗）的含义为 container，step 4 完成、结论与最终答案直接记录在案。",
        },
    ],
    "resolution_source_event_ids": ["t0089:observation"],
    "required_core_source_event_ids": [
        "t0010:action",
        "t0010:observation",
        "t0039:action",
        "t0039:observation",
        "t0051:action",
        "t0051:observation",
        "t0089:action",
        "t0089:observation",
    ],
    "optional_support_source_event_ids": [
        "t0009:observation",
        "t0011:action",
        "t0011:observation",
        "t0018:observation",
        "t0035:observation",
        "t0040:observation",
        "t0041:observation",
        "t0050:observation",
        "t0053:action",
        "t0053:observation",
        "t0087:observation",
        "t0090:action",
        "t0090:observation",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            [
                "t0010:action", "t0010:observation", "t0039:action", "t0039:observation",
                "t0051:action", "t0051:observation", "t0089:action", "t0089:observation",
            ],
            [
                "t0010:action", "t0010:observation", "t0051:action", "t0051:observation",
                "t0089:observation",
            ],
        ],
        "relevant_evidence_ids": [
            "t0009:observation", "t0010:action", "t0010:observation", "t0011:action",
            "t0011:observation", "t0018:observation", "t0035:observation", "t0039:action",
            "t0039:observation", "t0040:observation", "t0041:observation", "t0050:observation",
            "t0051:action", "t0051:observation", "t0053:action", "t0053:observation",
            "t0087:observation", "t0089:action", "t0089:observation", "t0090:action",
            "t0090:observation",
        ],
        "causal_paths": [
            {
                "evidence_ids": [
                    "t0010:action", "t0010:observation", "t0039:action", "t0039:observation",
                    "t0051:action", "t0051:observation", "t0089:action", "t0089:observation",
                ],
                "constraints": [
                    ["t0010:action", "t0010:observation"],
                    ["t0010:observation", "t0039:action"],
                    ["t0039:action", "t0039:observation"],
                    ["t0039:observation", "t0051:action"],
                    ["t0051:action", "t0051:observation"],
                    ["t0051:observation", "t0089:action"],
                    ["t0089:action", "t0089:observation"],
                ],
            },
            {
                "evidence_ids": [
                    "t0010:action", "t0010:observation", "t0051:action", "t0051:observation",
                    "t0089:observation",
                ],
                "constraints": [
                    ["t0010:action", "t0010:observation"],
                    ["t0010:observation", "t0051:action"],
                    ["t0051:action", "t0051:observation"],
                    ["t0051:observation", "t0089:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                [
                    "t0039:action", "t0039:observation", "t0051:action", "t0051:observation",
                    "t0089:observation",
                ],
                ["t0050:observation", "t0051:action", "t0051:observation"],
            ],
            "relevant_evidence_ids": [
                "t0011:action", "t0011:observation", "t0018:observation", "t0035:observation",
                "t0039:action", "t0039:observation", "t0040:observation", "t0041:observation",
                "t0050:observation", "t0051:action", "t0051:observation", "t0053:action",
                "t0053:observation", "t0087:observation", "t0089:action", "t0089:observation",
            ],
            "causal_paths": [
                {
                    "evidence_ids": [
                        "t0039:action", "t0039:observation", "t0051:action", "t0051:observation",
                        "t0089:observation",
                    ],
                    "constraints": [
                        ["t0039:action", "t0039:observation"],
                        ["t0039:observation", "t0051:action"],
                        ["t0051:action", "t0051:observation"],
                        ["t0051:observation", "t0089:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0050:observation", "t0051:action", "t0051:observation"],
                    "constraints": [
                        ["t0050:observation", "t0051:action"],
                        ["t0051:action", "t0051:observation"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0051:action", "t0051:observation", "t0053:action", "t0053:observation"],
                ["t0050:observation", "t0051:action", "t0051:observation"],
            ],
            "relevant_evidence_ids": [
                "t0035:observation", "t0050:observation", "t0051:action", "t0051:observation",
                "t0053:action", "t0053:observation", "t0077:observation",
            ],
            "causal_paths": [
                {
                    "evidence_ids": ["t0051:action", "t0051:observation", "t0053:action", "t0053:observation"],
                    "constraints": [
                        ["t0051:action", "t0051:observation"],
                        ["t0051:observation", "t0053:action"],
                        ["t0053:action", "t0053:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0050:observation", "t0051:action", "t0051:observation"],
                    "constraints": [
                        ["t0050:observation", "t0051:action"],
                        ["t0051:action", "t0051:observation"],
                    ],
                },
            ],
        },
    },
}

if __name__ == "__main__":
    A.check_episode(EPISODE, CID)
    A.write_row(OUT, CID, annotator="ai-draft:zcode:glm-5.2:pass-b", status="annotated", episode=EPISODE)
    print("case 040 OK")
