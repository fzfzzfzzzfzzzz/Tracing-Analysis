import sys
sys.path.insert(0, r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1")
import anno_lib as A

CID = "8968d4e8012b2b73f85f29e1e8e0f2afbb9df0c4307c3401cb55604b16a14d86"
OUT = r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1\pass_b\batch_05.jsonl"

A.write_row(
    OUT, CID,
    annotator="ai-draft:zcode:glm-5.2:pass-b",
    status="rejected",
    rejection_reason=(
        "任务要求 star 前八个最多星标的仓库，但整条轨迹（62 事件）中从未出现任何成功的 star/unstar 操作："
        "t0014-t0025 的全局搜索均显示 \"We couldn't find any projects matching ...\"（Projects 0），"
        "结尾 t0026/t0029 只是重复点击 Dashboard 列表中的同一项目链接而无状态变化，"
        "最终 t0030:action 为 stop [Early stop: Reach max steps 30]，页面停留在 Projects · Dashboard，"
        "Starred 计数始终为 3。任务最终未成功且轨迹被步数上限截断，不存在\"初始失败→修复→最终成功被直接观察\"的完整闭环，故拒绝。"
    ),
)
print("case025 OK")
