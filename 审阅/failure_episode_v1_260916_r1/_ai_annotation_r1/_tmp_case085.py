import sys
sys.path.insert(0, r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1")
import anno_lib as A

CID = "64fb898da19248371205d0ba5a15ecbfe58134731aec239b0c802777a82db12f"
OUT = r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1\pass_b\batch_05.jsonl"

A.write_row(
    OUT, CID,
    annotator="ai-draft:zcode:glm-5.2:pass-b",
    status="rejected",
    rejection_reason=(
        "gaia_level1 任务 69（以 Kipchoge 马拉松配速计算跑到月球最近点需要多少千小时）为一次性成功轨迹："
        "t0000-t0004 搜索并抓取 Wikipedia 获得 42.195 km、2:01:09、356400 km 三个输入，"
        "t0008/t0014 两次 execute_code 顺利得出 20.897 km/h 与 17054.888 小时，"
        "t0019 按千小时取整为 17，t0022 提交 FINAL_ANSWER: 17。"
        "全部 46 个事件中没有任何失败的工具结果、错误输出或返工重试，"
        "不存在任务级初始失败可作锚点，无法构造失败-修复 episode，故拒绝。"
    ),
)
print("case085 OK")
