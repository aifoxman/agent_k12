"""Course context can resolve an interpretation; provider uncertainty still gates teaching."""

import json
from types import SimpleNamespace

import pytest

from k12_server import page_prep


def run_solver(monkeypatch, raw, *, independent=False):
    payloads = []
    crops = []

    def focus(image, box):
        crops.append((image, box))
        return b"focused-question", box

    def post(_settings, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": json.dumps(raw, ensure_ascii=False)}}]}

    monkeypatch.setattr(page_prep, "focus_question_image", focus)
    monkeypatch.setattr(page_prep, "post_model", post)
    question = {
        "label": "当前题目",
        "evidence": "把一个总数拆成若干非零数相加，小提示要求按加数个数分类统计。",
        "box": [.1, .2, .6, .3],
        "read_confidence": .96,
    }
    result = page_prep.solve_question(
        SimpleNamespace(ai_model="controlled-solver"), b"whole-page", question,
        independent=independent,
    )
    return result, payloads, crops, question


def test_solver_requests_evidenced_course_conventions_instead_of_purely_formal_ambiguity(monkeypatch):
    raw = {
        "answer": "依教材的不同拆法约定统计所有分类。",
        "steps": "结合小提示按项数分类；采用交换加数顺序不另计的课程约定，固定排列次序避免重复。",
        "hint_seed": "先读小提示，再用统一顺序记录每类。",
        "confidence": .96,
        "ambiguous": False,
    }
    result, payloads, crops, question = run_solver(monkeypatch, raw)
    assert result == raw
    assert len(payloads) == 1
    instruction = payloads[0]["messages"][0]["content"]
    for requirement in (
        "印刷小提示", "可见的学段或年级", "教材章节", "常见课程约定",
        "不猜测未提供的年级", "采用的约定、支持依据及适用范围",
        "不能仅因理论上另有一种计数或解释口径", "多种同等合理",
        "缺乏选择证据", "不能为顺利辅导而强行消除真正的歧义",
    ):
        assert requirement in instruction
    assert crops == [(b"whole-page", question["box"])]
    content = payloads[0]["messages"][1]["content"]
    assert question["evidence"] in content[0]["text"]
    assert content[1] == page_prep._image_part(b"whole-page")
    assert content[2] == page_prep._image_part(b"focused-question")


def test_an_old_formal_alternative_warning_is_not_silently_overridden_after_the_prompt_fix(monkeypatch):
    raw = {
        "answer": "通常按课程约定有完整解答，按另一计数口径则不同。",
        "steps": "已经按印刷提示穷尽各类，但因为题面没有逐字排除另一口径，故标记有歧义。",
        "hint_seed": "先按提示分类。",
        "confidence": .97,
        "ambiguous": True,
    }
    result, payloads, _, question = run_solver(monkeypatch, raw)
    assert result == raw
    assert result["ambiguous"] is True
    assert result["confidence"] == .97
    assert page_prep.assess_spoken_answer(
        SimpleNamespace(ai_model="controlled-solver"), question, result, "我已经列完各类了。",
    ) is None
    assert len(payloads) == 1, "Ambiguous solutions must not authorize an assessment model call."


@pytest.mark.parametrize("reason", [
    "必要条件被遮挡，无法确认题意。",
    "可见课程语境仍支持两种同等合理的解释，且没有提示能够选择。",
])
def test_missing_conditions_and_genuinely_unresolved_interpretations_remain_ambiguous(monkeypatch, reason):
    raw = {"answer": "尚待确认", "steps": reason, "hint_seed": "", "confidence": .72,
           "ambiguous": True}
    result, _, _, _ = run_solver(monkeypatch, raw)
    assert result == raw


def test_independent_recheck_uses_the_same_context_rule_and_keeps_the_global_optimality_requirement(monkeypatch):
    raw = {"answer": "需要完整界限证明", "steps": "目前只验证了一种可行安排。",
           "hint_seed": "", "confidence": .81, "ambiguous": True}
    result, payloads, _, _ = run_solver(monkeypatch, raw, independent=True)
    instruction = payloads[0]["messages"][0]["content"]
    assert "采用的约定、支持依据及适用范围" in instruction
    assert "这次是独立复核" in instruction
    assert "不参考其他模型结论" in instruction
    assert "覆盖题目允许其他方案的界限理由" in instruction
    assert "不能只数选定布局的缺口" in instruction
    assert "不把未完成的最优性判断当成确定结论" in instruction
    assert result == raw
