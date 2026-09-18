# -*- coding: utf-8 -*-
"""Pass-A batch_07 annotation (cases 032-036), built per instructions_pass_a.md."""
import anno_lib as A

OUT = A.WORK / "pass_a" / "batch_07.jsonl"
ANNO = "ai-draft:zcode:glm-5.2:pass-a"


def rel(cid, ids):
    """Chronologically sort a set of event ids by their position in the case prefix."""
    order = {e["source_event_id"]: i for i, e in enumerate(A.events(cid))}
    return sorted(set(ids), key=lambda x: order[x])


# ---------------------------------------------------------------- case 032
CID32 = "bfb3100ab32151544e905e1cb7d79edd251b957908cc842141e92b5c2ba59d71"


def case032():
    A.write_row(OUT, CID32, annotator=ANNO, status="rejected",
                rejection_reason=(
                    "NetHack(minihack)游戏轨迹：任务以角色死亡告终且从未成功。t0063:action 向东移动后，"
                    "t0063:observation 显示死亡画面（Burned by molten lava / You made the top ten list），"
                    "即最终结果为角色被熔岩烧死，episode 就此终止，轨迹中不存在任何任务级成功的直接观察；"
                    "过程中的局部小失败（t0008/t0014/t0021/t0024 撞墙 It's a wall、t0030/t0031 攻击落空、"
                    "t0037 无物可捡）都不构成任务级失败→修复→成功的闭环，没有可锚定的任务级初始失败与修复阶段。"))


# ---------------------------------------------------------------- case 034
CID34 = "c0a4617d921350d82ef173e26f495b82bf1d91f8389f88cfc7e8fc53df121f06"


def case034():
    A.write_row(OUT, CID34, annotator=ANNO, status="rejected",
                rejection_reason=(
                    "GAIA 研究型任务，无法定位任务级初始失败：轨迹按 Step 0-4 顺序推进（t0010/t0028/t0037/t0044/t0057 五次 "
                    "mark_step 均为 completed），并在 t0058 提交 FINAL_ANSWER: Li Peng，全程不存在一个"
                    "『整个轨迹要解决』的任务级失败动作及其直接失败结果。候选失败均为探索/验证期的局部子失败且随即被绕开："
                    "t0025/t0048 抓取 opencv.org 被 JS/Cookie 拦截（Just a moment...）、t0049 raw.githubusercontent 返回 404: Not Found、"
                    "t0050 只返回跳转提示、t0053 取回的是 Wiki 首页而非 ChangeLog——这些都只是证据获取受阻，"
                    "当时任务级答案（Li Peng）在 t0044 已得出，Step 4 只是验证；且 t0058:observation 为空字符串，"
                    "最终提交没有直接的成败观察。不满足初始失败→修复→最终成功的 episode 语义。"))


# ---------------------------------------------------------------- case 035
CID35 = "c867e48aaa351f31ced8df98a6021aff3dbb0fadbc2d67cf5de4d73fed2d41dd"


def case035():
    A.write_row(OUT, CID35, annotator=ANNO, status="rejected",
                rejection_reason=(
                    "Crafter 游戏轨迹：任务以角色死亡告终且从未成功。t0170–t0174 连续 Sleep（观察显示 zombie 从 "
                    "3 步外逼近到 1 步外且 You are sleeping, and will not be able take actions），醒后 t0175–t0178 "
                    "连续 Do 但始终面向 path 而非 zombie，最终 t0179:observation 直接给出 You died.，episode 就此终止，"
                    "轨迹中不存在任何任务级成功的直接观察；过程中的局部小失败（如 t0000/t0006/t0013 的 Do 因未面向目标"
                    "而无效果）属于探索期局部行为失误，均不构成任务级失败→修复→成功的闭环，无可用锚点与修复阶段。"))


# ---------------------------------------------------------------- case 036
CID36 = "d4e7c0c0c500cbd962cba7fc0dafa772645d921480a8c1cae7df67dde1419fb1"


def case036():
    A.write_row(OUT, CID36, annotator=ANNO, status="rejected",
                rejection_reason=(
                    "Crafter 游戏轨迹，任务从未成功且轨迹在睡眠状态中截断：结尾 t0271:action Sleep 后 t0272–t0274 连续 "
                    "Noop，最终 t0274:observation 仍为 You are sleeping, and will not be able take actions until energy "
                    "is full.，且 skeleton 已逼近至 2 步（east），既无死亡也无任何任务级成功的直接观察，episode 未闭合；"
                    "全程为探索、采集与恢复生存属性的操作，无任务级失败→修复→成功的闭环。t0193–t0229 长期 "
                    "You see nothing 的来回震荡移动属于低效探索行为，不是任务级失败锚点；多次 Sleep/Noop 恢复能量"
                    "（t0154、t0243、t0251、t0271）是游戏内常规状态管理而非对某次任务级失败的修复。"))


# ---------------------------------------------------------------- case 033
CID33 = "bfc56d5320a3b3433b881c8415606f4f898bdc2728d6ab9afd6a10f31bbf2964"
EP33 = {
    "scope": "task_level_failure_episode",
    "failure_family": "timeout_resource",
    "recoverability": "R1",
    "error_signature": "Read timed out. (read timeout=30)",
    "diagnostic_evidence": (
        "t0007:action 首次用单个多 CTE 连接 + Haversine 的巨型 SQL 一次性计算 Abakan 最长航线距离，"
        "t0007:observation 返回 API error: Read timed out. (read timeout=30)，即执行环境的 30 秒 HTTP 读超时，"
        "构成任务级初始失败：任务要求的数值无法通过该单体查询获得。"
        "此前 t0002–t0005 已完成 schema 探索、t0006 已确认 Abakan 的 airport_code='ABA'，"
        "说明失败根源是查询形态（一次性重连接聚合）触发超时，而非数据缺失。"
    ),
    "recovery_sequence": (
        "1) 拆解查询：t0008:action 改为仅从 FLIGHTS 过滤 departure/arrival='ABA' 的轻量 DISTINCT 查询，"
        "t0008:observation 成功返回 12 条 ABA 航线对（ABA-GRV、ABA-DME 等），绕开超时；"
        "2) t0009:action 取这 7 个机场的坐标，t0009:observation 返回经纬度；"
        "3) t0010:action 用硬编码坐标在 SQL 内做 Haversine 计算，t0010:observation 列出各航线距离，"
        "最大为 ABA-GRV 3484.1502587948157；"
        "4) t0011:action 改为在 SQL 中用 SPLIT_PART 从 AIRPORTS_DATA.coordinates 解析坐标并按航线取 AVG，"
        "t0011:observation 给出 ABA,GRV,3484.1504600096，消除手抄坐标的精度差；"
        "5) t0012:action 在同一 CTE 链上改为 SELECT MAX(avg_distance_km) as longest_route_distance_km，"
        "t0012:observation 返回 LONGEST_ROUTE_DISTANCE_KM=3484.1504600096，任务级结果获得直接观察。"
    ),
    "resolution_evidence": (
        "t0012:observation 显示 Query executed successfully 且结果集 LONGEST_ROUTE_DISTANCE_KM=3484.1504600096，"
        "与 t0010:observation（硬编码坐标）和 t0011:observation（SQL 解析坐标）两次独立计算的 ABA-GRV 距离一致，"
        "确认超时被规避且最终数值已得到验证。"
    ),
    "anchor_source_event_id": "t0007:action",
    "initial_action_source_event_id": "t0007:action",
    "initial_result_source_event_ids": ["t0007:observation"],
    "repair_steps": [
        {
            "step_id": "decompose-routes",
            "decision_source_event_ids": [],
            "action_source_event_id": "t0008:action",
            "result_source_event_ids": ["t0008:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "放弃单体大连接查询，改为轻量 DISTINCT 查询直接从 FLIGHTS 取 ABA 相关航线对，t0008:observation 成功列出 12 条航线（含 ABA-GRV），但尚未计算任何距离，任务结果仍未得到。",
        },
        {
            "step_id": "fetch-coords",
            "decision_source_event_ids": ["t0008:observation"],
            "action_source_event_id": "t0009:action",
            "result_source_event_ids": ["t0009:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "按 t0008 得到的航线端点用 IN ('ABA','TOF','KYZ','GRV','ARH','OVB','DME') 查询 AIRPORTS_DATA 坐标，t0009:observation 返回 7 个机场经纬度，为距离计算准备输入，但距离尚未算出。",
        },
        {
            "step_id": "hardcoded-haversine",
            "decision_source_event_ids": ["t0009:observation"],
            "action_source_event_id": "t0010:action",
            "result_source_event_ids": ["t0010:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "将手抄（四舍五入）坐标硬编码进 SQL 并计算各航线 Haversine 距离，t0010:observation 显示最大为 ABA-GRV 3484.1502587948157，得到初步答案但依赖人工转录坐标，尚未形成最终查询形态。",
        },
        {
            "step_id": "sql-parsed-coords",
            "decision_source_event_ids": ["t0010:observation"],
            "action_source_event_id": "t0011:action",
            "result_source_event_ids": ["t0011:observation"],
            "outcome": "intermediate_failure",
            "semantic_change": "改为在 SQL 内用 SPLIT_PART+REPLACE 从 AIRPORTS_DATA.coordinates 解析高精度坐标并按城市对取 AVG 距离，t0011:observation 给出 ABA,GRV,3484.1504600096，消除硬编码精度差，但还未输出最终的单值最长距离。",
        },
        {
            "step_id": "final-max",
            "decision_source_event_ids": ["t0011:observation"],
            "action_source_event_id": "t0012:action",
            "result_source_event_ids": ["t0012:observation"],
            "outcome": "resolved",
            "semantic_change": "在同一 CTE 链末端改为 SELECT MAX(avg_distance_km) as longest_route_distance_km，t0012:observation 返回 LONGEST_ROUTE_DISTANCE_KM=3484.1504600096，任务级结果以纯 SQL 形式得到直接观察，超时失败被完全规避。",
        },
    ],
    "resolution_source_event_ids": ["t0012:observation"],
    "required_core_source_event_ids": [
        "t0007:action", "t0007:observation", "t0010:action", "t0010:observation",
        "t0012:action", "t0012:observation",
    ],
    "optional_support_source_event_ids": [
        "t0008:action", "t0008:observation", "t0009:action", "t0009:observation",
        "t0011:action", "t0011:observation",
    ],
    "chain_policy": {
        "alternative_evidence_sets": [
            ["t0007:action", "t0007:observation", "t0010:action", "t0010:observation",
             "t0012:action", "t0012:observation"],
            ["t0007:action", "t0007:observation", "t0011:action", "t0011:observation",
             "t0012:observation"],
        ],
        "relevant_evidence_ids": [
            "t0007:action", "t0007:observation", "t0008:action", "t0008:observation",
            "t0009:action", "t0009:observation", "t0010:action", "t0010:observation",
            "t0011:action", "t0011:observation", "t0012:action", "t0012:observation",
        ],
        "causal_paths": [
            {
                "evidence_ids": ["t0007:action", "t0007:observation", "t0010:action",
                                 "t0010:observation", "t0012:action", "t0012:observation"],
                "constraints": [
                    ["t0007:action", "t0007:observation"],
                    ["t0007:observation", "t0010:action"],
                    ["t0010:action", "t0010:observation"],
                    ["t0010:observation", "t0012:action"],
                    ["t0012:action", "t0012:observation"],
                ],
            },
            {
                "evidence_ids": ["t0007:action", "t0007:observation", "t0011:action",
                                 "t0011:observation", "t0012:observation"],
                "constraints": [
                    ["t0007:action", "t0007:observation"],
                    ["t0007:observation", "t0011:action"],
                    ["t0011:action", "t0011:observation"],
                    ["t0011:observation", "t0012:observation"],
                ],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "alternative_evidence_sets": [
                ["t0008:action", "t0010:action", "t0010:observation", "t0012:observation"],
                ["t0008:action", "t0011:action", "t0012:action", "t0012:observation"],
            ],
            "relevant_evidence_ids": [
                "t0008:action", "t0008:observation", "t0009:action", "t0009:observation",
                "t0010:action", "t0010:observation", "t0011:action", "t0011:observation",
                "t0012:action", "t0012:observation",
            ],
            "causal_paths": [
                {
                    "evidence_ids": ["t0008:action", "t0010:action", "t0010:observation",
                                     "t0012:observation"],
                    "constraints": [
                        ["t0008:action", "t0010:action"],
                        ["t0010:action", "t0010:observation"],
                        ["t0010:observation", "t0012:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0008:action", "t0011:action", "t0012:action",
                                     "t0012:observation"],
                    "constraints": [
                        ["t0008:action", "t0011:action"],
                        ["t0011:action", "t0012:action"],
                        ["t0012:action", "t0012:observation"],
                    ],
                },
            ],
        },
        "interactive_reacquisition": {
            "alternative_evidence_sets": [
                ["t0007:action", "t0007:observation", "t0008:action", "t0008:observation"],
                ["t0007:action", "t0007:observation", "t0009:observation"],
            ],
            "relevant_evidence_ids": [
                "t0007:action", "t0007:observation", "t0008:action", "t0008:observation",
                "t0009:action", "t0009:observation",
            ],
            "causal_paths": [
                {
                    "evidence_ids": ["t0007:action", "t0007:observation", "t0008:action",
                                     "t0008:observation"],
                    "constraints": [
                        ["t0007:action", "t0007:observation"],
                        ["t0007:observation", "t0008:action"],
                        ["t0008:action", "t0008:observation"],
                    ],
                },
                {
                    "evidence_ids": ["t0007:action", "t0007:observation", "t0009:observation"],
                    "constraints": [
                        ["t0007:action", "t0007:observation"],
                        ["t0007:observation", "t0009:observation"],
                    ],
                },
            ],
        },
    },
}


def case033():
    A.write_row(OUT, CID33, annotator=ANNO, status="annotated", episode=EP33)


if __name__ == "__main__":
    case032()
    case033()
    case034()
    case035()
    case036()
    print(A.check_file(OUT) or "file check OK")
