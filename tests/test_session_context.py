"""Homework context stays bounded and grounded in one session."""

from __future__ import annotations

import json

from k12_server.session_context import format_session_context


def test_context_preserves_recent_dialogue_and_parent_review_in_time_order():
    summary = format_session_context(
        [{"created_at": 1, "question": "第三题怎么做", "response": "先找题目条件"}],
        [{"created_at": 2, "text": "我想先画图"}],
        [{"created_at": 3, "label": "有新书写", "progress": "第三题画出一条线"}],
        [
            {
                "created_at": 4,
                "subject_label": "数学",
                "page_label": "第 2 页",
                "summary": "一项建议复核",
                "checks_json": json.dumps(
                    [
                        {
                            "question": "第三题",
                            "parent_review": {
                                "status": "looks_correct",
                                "note": "原判看漏了步骤",
                                "updated_at": 5,
                            },
                        },
                    ]
                ),
            }
        ],
    )
    assert summary.index("第三题怎么做") < summary.index("我想先画图")
    assert summary.index("我想先画图") < summary.index("第三题画出一条线")
    assert "家长校对 第三题：looks_correct；说明：原判看漏了步骤" in summary


def test_grading_context_keeps_learning_process_without_old_answers_or_grades():
    summary = format_session_context(
        [{"created_at": 1, "question": "我不懂第二题", "response": "旧提示答案是四"}],
        [{"created_at": 2, "text": "我先画了图"}],
        [],
        [{"created_at": 3, "summary": "旧批改判错", "checks_json": "[]"}],
        purpose="correction",
    )
    assert "我不懂第二题" in summary and "我先画了图" in summary
    assert "旧提示答案" not in summary and "旧批改判错" not in summary


def test_context_has_a_fixed_budget_even_after_many_turns():
    hints = [
        {"created_at": n, "question": f"第{n}次提问" + "字" * 300, "response": "提示" * 200}
        for n in range(20)
    ]
    summary = format_session_context(hints, [], [], [], max_chars=500)
    assert len(summary) <= 500
    assert "第19次提问" in summary
    assert "第0次提问" not in summary
