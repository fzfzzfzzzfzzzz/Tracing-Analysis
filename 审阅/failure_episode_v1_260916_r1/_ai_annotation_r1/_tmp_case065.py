import sys
sys.path.insert(0, r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1")
import anno_lib as A

CID = "70ba2297e45f080fefffa54e835e321d34e26e690f33b2e8543bc10f0dc49a20"
OUT = r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1\_ai_annotation_r1\pass_b\batch_05.jsonl"

A.write_row(
    OUT, CID,
    annotator="ai-draft:zcode:glm-5.2:pass-b",
    status="rejected",
    rejection_reason=(
        "iterative__dvc-4125（.dvcignore 导致 PermissionError）任务最终未成功："
        "复现脚本 m0015 首跑因 .dvc 已存在 InitError 失败，改为 Repo() 后 m0019 与 m0057 两次运行"
        "均以正则编译错误 redefinition of group name 'ps_d' 崩溃（exit code 1）；"
        "代理始终未能定位 DVC 源码（m0003/m0007 等多次列出 /workspace/iterative__dvc__1.1 均显示目录为空，"
        "m0009/m0025/m0027/m0031 等反复出现 view/view_range 局部错误），未做任何源码修复，"
        "结尾 m0060 只是口头总结并建议简化 pattern，甚至未执行 finish。"
        "不存在最终成功被直接观察的事件，无法构成完整任务级 episode，故拒绝。"
    ),
)
print("case065 OK")
