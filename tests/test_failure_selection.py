import unittest

from tracegraph.failure_selection import analyze_failure_rich_tasks


def _simulation(
    task_id: str,
    trial: int,
    *,
    errors: int = 0,
    retry: bool = False,
    success: bool = False,
) -> dict:
    messages = []
    for index in range(errors):
        call_id = f"{task_id}-{trial}-{index}"
        call = {
            "id": call_id,
            "function": {"name": "lookup", "arguments": {"id": index}},
        }
        messages.extend(
            (
                {"role": "assistant", "tool_calls": [call]},
                {
                    "role": "tool",
                    "id": call_id,
                    "content": "Error: temporary",
                    "error": True,
                },
            )
        )
        if retry and index == 0:
            retry_id = f"{call_id}-retry"
            retry_call = {**call, "id": retry_id}
            messages.extend(
                (
                    {"role": "assistant", "tool_calls": [retry_call]},
                    {
                        "role": "tool",
                        "id": retry_id,
                        "content": {"ok": True},
                        "error": False,
                    },
                )
            )
    return {
        "task_id": task_id,
        "trial": trial,
        "reward_info": {"reward": 1.0 if success else 0.0},
        "termination_reason": "user_stop",
        "messages": messages,
    }


class FailureSelectionTests(unittest.TestCase):
    def test_ranks_retry_then_error_rich_tasks(self):
        payload = {
            "simulations": [
                _simulation("10", 0, errors=1, retry=True, success=True),
                _simulation("10", 1, errors=0, success=False),
                _simulation("11", 0, errors=2, success=False),
                _simulation("11", 1, errors=2, success=False),
                _simulation("12", 0, errors=0, success=True),
                _simulation("12", 1, errors=0, success=True),
            ]
        }
        report = analyze_failure_rich_tasks(
            {"retail": payload},
            split_membership={
                "retail": {
                    "train": {"10", "12"},
                    "test": {"11"},
                }
            },
            top_per_domain=2,
        )
        self.assertEqual(report["selected_tasks"]["retail"], ["10", "11"])
        tasks = report["domains"]["retail"]["tasks"]
        self.assertEqual(tasks[0]["retry_count"], 1)
        self.assertEqual(tasks[0]["resolve_count"], 1)
        self.assertEqual(tasks[0]["split"], "train")
        self.assertEqual(tasks[1]["error_count"], 4)
        self.assertEqual(report["domains"]["retail"]["failure_rich_task_count"], 2)

    def test_requires_positive_selection_size(self):
        with self.assertRaises(ValueError):
            analyze_failure_rich_tasks({"retail": {"simulations": []}}, top_per_domain=0)

    def test_normalizes_json_arguments_and_nested_tool_messages(self):
        payload = {
            "simulations": [
                {
                    "task_id": "create_task_1",
                    "trial": 0,
                    "trajectory": [
                        {
                            "tool_messages": [
                                {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "id": "call_1",
                                            "function": {
                                                "name": "lookup",
                                                "arguments": '{"b": 2, "a": 1}',
                                            },
                                        }
                                    ],
                                },
                                {
                                    "role": "tool",
                                    "tool_call_id": "call_1",
                                    "content": '{"error": "temporary"}',
                                },
                                {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "id": "call_2",
                                            "function": {
                                                "name": "lookup",
                                                "arguments": {"a": 1, "b": 2},
                                            },
                                        }
                                    ],
                                },
                                {
                                    "role": "tool",
                                    "tool_call_id": "call_2",
                                    "content": '{"ok": true}',
                                },
                            ]
                        }
                    ],
                    "reward": 1,
                }
            ]
        }

        report = analyze_failure_rich_tasks({"mock": payload}, top_per_domain=1)

        task = report["domains"]["mock"]["tasks"][0]
        self.assertEqual(task["task_id"], "create_task_1")
        self.assertEqual(task["retry_count"], 1)
        self.assertEqual(task["resolve_count"], 1)


if __name__ == "__main__":
    unittest.main()
