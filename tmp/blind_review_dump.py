"""Compact dump of blind-review cases for reviewer reading.

Prints question, necessary facts, full answer, and records with long filler
content truncated. Answer text is always shown verbatim in full.
"""

import json
import sys
from pathlib import Path

PACKET = Path(r"E:\科研\Tools Tracing\审阅"
              r"\qwen3_14b_real_answer_blind_review_reviewer_packet_260913_r1"
              r"\reviewer_packet\cases.jsonl")


def shorten(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f" ...[+{len(text) - limit} chars]"


def main() -> None:
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    cases = [json.loads(line) for line in PACKET.open(encoding="utf-8")]
    for case in cases:
        n = int(case["blind_id"].rsplit("-", 1)[1])
        if not lo <= n <= hi:
            continue
        print("=" * 72)
        print(f"[{case['blind_id']}]  review_task: {case['review_task']}")
        print(f"QUESTION: {case['question']}")
        for key, fact in case["necessary_facts"].items():
            print(f"FACT {key}: {fact}")
        answer = case["answer"]
        print(f"ANSWER.a ({answer['t']}, s={answer['s']}):")
        print(answer["a"])
        print(f"ANSWER.e: {answer['e']}")
        print("RECORDS:")
        for record in case["records"]:
            content = record["content"]
            if isinstance(content, dict):
                content = json.dumps(content, ensure_ascii=False)
            content = shorten(content)
            print(f"  {record['record_id']} [{record['kind']}] {content}")
        print()


if __name__ == "__main__":
    main()
