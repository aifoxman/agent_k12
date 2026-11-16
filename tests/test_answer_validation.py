"""Reject arithmetic contradictions without confusing negative-choice questions."""
import pytest

from k12_server.answer_validation import arithmetic_conflict, arithmetic_value
from k12_server.ai import grounded_check_status, normalized_solution_fields
from k12_server.teaching_ink import grounded_teaching_solution


@pytest.mark.parametrize("expression,result", [
    (" 0.1 + 0.2 ", "0.3"), ("720000÷10000", "72"),
    ("(12−3)×2", "18"), ("25%*80", "20"), ("1/3+1/6", "1/2"),
])
def test_exact_arithmetic_does_not_use_binary_float_rounding(expression, result):
    from fractions import Fraction
    assert arithmetic_value(expression) == Fraction(result)


@pytest.mark.parametrize("expression", ["2**100000", "__import__('os')", "x+2", "9//2", "True", "1e1000"])
def test_arithmetic_never_evaluates_unsupported_code(expression):
    with pytest.raises((ValueError, SyntaxError)):
        arithmetic_value(expression)


@pytest.mark.parametrize("text,conflict", [
    ("验算：9+3=12。", False), ("0.1+0.2=0.3", False),
    ("12−3=8", True), ("步骤：8×6−7=42", True),
    ("12－3＝8", True), ("错误示例：12-3=8。", False),
    ("反例：\n12-3=8。订正：12-3=9。", False),
    ("D项：12-3=8。", False), ("正确计算：12-3=8。", True),
    ("24÷6=4=5", True), ("3÷0=0", True),
    ("答案约为3.14≈22/7", False), ("x+2=4", False),
    ("面积为2m²=20000cm²", False), ("如果12−3=8，则不符合原条件", False),
    (["先计算", "720000÷10000=720"], True), (None, False),
])
def test_only_standalone_asserted_exact_equations_can_block_a_solution(text, conflict):
    assert arithmetic_conflict(text) is conflict


def choice_item():
    return {"status": "looks_correct", "answer": "D", "reference_answer": "选D",
            "confidence": .97, "solution_confidence": .96, "question_complete": True,
            "visible_question_text": "下列说法不正确的一项是。A.甲 B.乙 C.丙 D.丁",
            "evidence": "完整选项与孩子写的D清楚可见",
            "reason": "D项说法不正确，选择符合题意。", "solution_steps": ["核对要求与各选项"],
            "choice_evaluation": {"requirement": "不正确的一项", "selected": "D", "expected": "D",
                                  "selection_matches": True, "selected_statement_truth": "false"}}


@pytest.mark.parametrize("reason", ["D项说法不正确，选择符合题意。", "D项错误。", "D项表述有误，符合本题要求。"])
def test_negative_choice_can_select_a_false_statement_without_being_a_wrong_answer(reason):
    assert grounded_check_status({**choice_item(), "reason": reason}, [.1, .1, .1, .1]) == ("looks_correct", False)


@pytest.mark.parametrize("change", [
    {"answer": "A"}, {"reference_answer": "B"}, {"question_complete": False},
    {"visible_question_text": "下列说法正确的一项是"},
    {"reason": "D项说法不正确，但孩子作答错误。"},
    {"reason": "D项与题意不符。"}, {"solution_steps": ["12−3=8"]},
])
def test_choice_metadata_cannot_override_changed_evidence_or_a_real_conflict(change):
    item = {**choice_item(), **change}
    assert grounded_check_status(item, [.1, .1, .1, .1])[0] == "unclear"


def test_wrong_choice_verdict_disagreeing_with_bound_comparison_is_withheld():
    item = {**choice_item(), "status": "check_again", "reason": "请核对选择"}
    assert grounded_check_status(item, [.1, .1, .1, .1]) == ("unclear", True)


@pytest.mark.parametrize("requirement", ["没有错误的一项", "不是错误的一项", "并非错误的一项", "不是不正确的一项"])
def test_double_negative_ask_never_authorizes_selecting_a_false_statement(requirement):
    item = choice_item()
    item["visible_question_text"] = requirement
    item["choice_evaluation"]["requirement"] = requirement
    assert grounded_check_status(item, [.1, .1, .1, .1])[0] == "unclear"


@pytest.mark.parametrize("answer,reference", [("D。理由正确。", "D"), ("D", "D。理由正确。"), ("（D）", "答案选D。")])
def test_choice_binding_accepts_a_clear_letter_followed_by_explanation(answer, reference):
    item = {**choice_item(), "answer": answer, "reference_answer": reference}
    assert grounded_check_status(item, [.1, .1, .1, .1]) == ("looks_correct", False)


@pytest.mark.parametrize("answer", ["AD", "不是D，应选A", "D或A", "D，A", "D。不是D，应选A。"])
def test_ambiguous_or_retracted_letters_do_not_bind_to_a_single_selection(answer):
    item = {**choice_item(), "answer": answer}
    assert grounded_check_status(item, [.1, .1, .1, .1])[0] == "unclear"


def test_selecting_an_incorrect_equation_may_quote_it_without_asserting_it_is_true():
    item = {**choice_item(), "solution_steps": ["D项：12－3＝8。", "正确计算：12−3=9。"]}
    assert grounded_check_status(item, [.1, .1, .1, .1]) == ("looks_correct", False)


def test_false_equation_cannot_survive_in_cached_teaching_or_grading_solution():
    solution = {"answer": "8", "steps": "12−3=8", "confidence": .99, "ambiguous": False}
    assert not grounded_teaching_solution(solution, .99)
    item = {**choice_item(), "solution_steps": ["12−3=8"]}
    assert normalized_solution_fields(item, "looks_correct")["solution_steps"] == []
