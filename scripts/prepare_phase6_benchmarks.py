"""离线检查 MemGym-CodeQA 和 SWE-Gym 是否已经可以开始试验。"""

from __future__ import annotations

import json
from pathlib import Path

from tracegraph.phase6_benchmarks import BudgetInput, prepare_benchmarks
from tracegraph.plain_cli import PlainArgumentParser, run_cli


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "phase6_benchmark_prepare_v2.json",
        help="写明官方来源、固定版本和本地位置的配置文件",
    )
    parser.add_argument("--output", type=Path, help="结果保存位置；默认使用配置中的位置")
    parser.add_argument(
        "--benchmark",
        action="append",
        help="只检查指定公开测试任务，可重复填写",
    )
    parser.add_argument("--task-count", type=int, help="只用于试算费用的任务数量")
    parser.add_argument("--methods-per-task", type=int, help="每个任务要比较的办法数量")
    parser.add_argument("--trials-per-method", type=int, help="每种办法在每个任务上运行几次")
    parser.add_argument(
        "--requests-per-trial",
        type=int,
        help="每次任务运行最多会向模型发送多少次请求",
    )
    parser.add_argument(
        "--input-tokens-per-request",
        type=int,
        help="估计每次请求最多会有多少输入 token（模型计量单位）",
    )
    parser.add_argument(
        "--output-tokens-per-request",
        type=int,
        help="估计每次请求最多会有多少输出 token（模型计量单位）",
    )
    parser.add_argument("--maximum-cost-cny", type=float, help="允许的最高人民币费用")
    parser.add_argument(
        "--model",
        default=None,
        help="只用于费用试算的模型；默认是 qwen3.8-27b",
    )
    args = parser.parse_args()
    report = prepare_benchmarks(
        args.config,
        workspace=ROOT,
        output_root=args.output,
        benchmark_ids=set(args.benchmark) if args.benchmark else None,
        budget_input=BudgetInput(
            task_count=args.task_count,
            methods_per_task=args.methods_per_task,
            trials_per_method=args.trials_per_method,
            requests_per_trial=args.requests_per_trial,
            input_tokens_per_request=args.input_tokens_per_request,
            output_tokens_per_request=args.output_tokens_per_request,
            maximum_cost_cny=args.maximum_cost_cny,
            model=args.model,
        ),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "ready":
        print("准备检查尚未通过：请先处理上面列出的缺失数据、版本或任务划分。")
        return 2
    print("准备检查通过；这条命令没有调用任何模型。")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
