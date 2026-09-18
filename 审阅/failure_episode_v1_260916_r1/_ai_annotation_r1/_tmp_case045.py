import sys
sys.path.insert(0, r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1")
import anno_lib as A

CID = "062ff973c6675d3cbebfa5bb0ce0faa70b26ca8b485aff9dc19a41039b8d9624"
OUT = r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1\pass_b\batch_05.jsonl"

A.write_row(
    OUT, CID,
    annotator="ai-draft:zcode:glm-5.2:pass-b",
    status="rejected",
    rejection_reason=(
        "轨迹是 hydra-1905（为 instantiate 增加 _partial_ 部分实例化）的一次性成功实现："
        "代理在 m0017-m0022 三次编辑 _instantiate2.py 后，m0025 运行自建测试脚本一次即通过"
        "（m0026 输出 Hello-World / PARTIAL END，exit code 0），随后 finish。"
        "全程不存在任何任务级初始失败可作锚点——没有失败测试、没有报错演示特性缺失；"
        "仅有的 m0012 grep 通配路径错误属探索期局部错误，不符合锚点语义。"
        "缺少\"初始失败→修复→成功\"闭环，无法构造任务级失败 episode，故拒绝。"
    ),
)
print("case045 OK")
