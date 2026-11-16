"""Corpus truth isolation, independent scoring, and finite model-call budgets."""
from __future__ import annotations

import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.question_bank.budget import Budget, BudgetExceeded
from scripts.question_bank.corpus import docx_content, inventory
from scripts.question_bank.prepare import group_cases
from scripts.question_bank.runner import events, forms, meaningful, safe_base
from scripts.question_bank.simulation import percentile, safe_image
from scripts.question_bank.teacher import arithmetic, assess_review, distill, validate_oracle


def test_source_answers_are_separated_even_when_filename_has_no_answer(tmp_path):
    source = tmp_path / "一年级" / "数学练习.docx"
    source.parent.mkdir()
    paragraphs = ["标题", *["题目" for _ in range(10)], "参考答案", "1. 45"]
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    xml += "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", xml + "</w:body></w:document>")
    original = source.read_bytes()
    result = docx_content(source)
    assert "45" not in result["stem_text"]
    assert "45" in result["answer_text"]
    index = inventory(tmp_path, tmp_path / "output")
    assert index["records"][0]["grade"] == 1
    assert source.read_bytes() == original


def test_reference_fields_cannot_enter_production_form():
    for mode in ("hint1", "hint2"):
        data = forms(mode, "synthetic-session", "real-first-hint")
        assert set(data) <= {"session_id", "question", "level", "selected_region", "previous_hint_id"}
        assert not any(k in data for k in ("oracle", "answer", "reference", "box", "question_text", "expected_status"))
    with pytest.raises(ValueError):
        forms("hint2", "session")


@pytest.mark.parametrize("url", ["http://127.0.0.1:8003", "https://example.com", "http://localhost:8016/other", "http://user:pass@localhost:8016"])
def test_no_family_or_remote_server_can_be_evaluated(url):
    with pytest.raises(ValueError):
        safe_base(url)


def test_atomic_budget_cannot_overrun_under_concurrency(tmp_path):
    budget = Budget(tmp_path / "budget.sqlite3")
    def reserve(i):
        try:
            budget.reserve(1, "task", str(i))
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(reserve, range(330)))
    assert sum(accepted) == 300
    assert budget.summary()["counts"] == [{"round": 1, "kind": "task", "count": 300}]
    assert budget.reserve(2, "task", "next-round")
    with pytest.raises(ValueError):
        budget.reserve(4, "upstream", "invalid")


def test_repeated_stream_metadata_does_not_double_count_tokens(tmp_path):
    budget = Budget(tmp_path / "budget.sqlite3")
    reservation = budget.reserve(1, "upstream", "one-stream")
    budget.record(reservation, "real-model", {})
    budget.record(reservation, "real-model", {"prompt_tokens": 10, "completion_tokens": 5})
    budget.record(reservation, "unknown", {})
    summary = budget.summary()
    assert summary["reported_input_tokens"] == 10
    assert summary["reported_output_tokens"] == 5
    assert summary["calls_with_usage_record"] == 1


@pytest.mark.parametrize("expression,result", [("27+18", "45"), ("1/3+1/6", "1/2"), ("2**3", "8"), ("1.2*3", "18/5")])
def test_exact_independent_arithmetic(expression, result):
    from fractions import Fraction
    assert arithmetic(expression) == Fraction(result)


@pytest.mark.parametrize("expression", ["__import__('os').system('echo bad')", "2**100", "True+1", "sum([1,2])", "1e100"])
def test_arithmetic_never_executes_teacher_code(expression):
    with pytest.raises((ValueError, SyntaxError)):
        arithmetic(expression)


def test_wrong_reference_quote_or_arithmetic_is_not_accepted():
    spec = {"usable": True, "question_quote": "27加18", "reference_quote": "1. 45",
            "box": [.1, .2, .7, .1], "answer": "45", "final_answer": "45", "wrong_answer": "35", "question_text": "27加18",
            "proof_expression": "27+18", "proof_result": "44"}
    rejected = validate_oracle(spec, {"answer_text": "1. 45"}, "27加18")
    assert not rejected["accepted"]
    assert "arithmetic_disagreement" in rejected["validation_errors"]
    spec.update(proof_result="45", reference_quote="不存在的答案")
    assert not validate_oracle(spec, {"answer_text": "1. 45"}, "27加18")["accepted"]


def test_near_duplicate_numeric_variants_stay_in_one_group():
    cases = [
        {"case_id": "a", "source_sha256": "paper1", "question_text": "有12块饼干，平均分给3人，每人多少？"},
        {"case_id": "b", "source_sha256": "paper2", "question_text": "有24块饼干，平均分给6人，每人多少？"},
        {"case_id": "c", "source_sha256": "paper3", "question_text": "请用因为所以写一句话"},
    ]
    groups = group_cases(cases)
    assert sorted(len(g) for g in groups) == [1, 2]


def test_critical_error_cannot_be_hidden_by_perfect_teacher_scores():
    review = {"verdict": "pass", "reference_reliable": True,
              "scores": dict.fromkeys(("accuracy", "reasoning", "age_fit", "visual_structure", "coherence"), 5),
              "findings": [{"severity": "critical", "quote": "错误的答案"}]}
    assert assess_review(review)["verdict"] == "fail"
    review.update(findings=[], reference_reliable=False)
    assert assess_review(review)["verdict"] == "needs_review"
    review.update(reference_reliable=True, scores={"accuracy": 5})
    assert assess_review(review)["verdict"] == "needs_review"


def test_waiting_status_and_target_box_are_not_effective_teaching():
    timing = {}
    meaningful({"label": "正在准备", "box": [.1, .1, .8, .5]}, timing, 100)
    assert timing == {}
    meaningful({"page": [{"kind": "underline", "text": "平均"}], "scratch": [{"text": "先找每份"}]}, timing, 200)
    assert timing == {"first_effective_mark_ms": 200, "first_thinking_ms": 200}


def test_unterminated_sse_cannot_fake_a_done():
    assert list(events(["event: done", 'data: {"located": true}'])) == []
    assert list(events(["event: beat", 'data: {"speech":"看条件"}', ""])) == [("beat", {"speech": "看条件"})]


def test_holdout_never_enters_distillation(tmp_path):
    path = tmp_path / "sample.json"
    record = {"case_id": "x", "split": "holdout", "response": {"response": "teacher"}}
    distill(record, {"verdict": "pass", "reference_reliable": True}, path)
    assert json.loads(path.read_text(encoding="utf-8"))["eligible_for_training"] is False


def test_fixture_path_cannot_escape_run(tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"x")
    inner = tmp_path / "run"
    inner.mkdir()
    with pytest.raises(ValueError):
        safe_image(inner, "../outside.jpg")
    assert percentile([], .95) is None
    assert percentile([30, 10, 20], .5) == 20


def test_final_holdout_rejects_changed_candidate_or_wrong_server(tmp_path):
    from scripts.question_bank.seal import freeze, verify
    current = tmp_path / "current"
    baseline = tmp_path / "private" / "baseline-src" / "k12_server"
    for directory in (current, baseline):
        directory.mkdir(parents=True)
        (directory / "app.py").write_text("original", encoding="utf-8")
    value = freeze(tmp_path, current)
    verify(tmp_path, "holdout-candidate", current, value["candidate"])
    with pytest.raises(ValueError):
        verify(tmp_path, "made-up-tag", current)
    with pytest.raises(ValueError):
        verify(tmp_path, "holdout-baseline", current, "wrong-server-hash")
    (current / "app.py").write_text("tuned after seeing holdout", encoding="utf-8")
    with pytest.raises(ValueError):
        verify(tmp_path, "holdout-candidate", current)
