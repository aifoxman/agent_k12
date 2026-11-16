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
from scripts.question_bank.corpus import docx_content, inferred_subject, inventory
from scripts.question_bank.prepare import group_cases
from scripts.question_bank.runner import events, forms, meaningful, prepared_form, safe_base, valid_tag
from scripts.question_bank.simulation import percentile, rendered_text_regions, safe_image, student_paper
from scripts.question_bank.teacher import arithmetic, assess_review, distill, evaluation_contract, reference_view, validate_oracle


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


def test_subject_can_come_from_a_real_header_or_pep_without_guessing_mixed_headings():
    assert inferred_subject("人教PEP版三年级期末卷.docx", {}) == "english"
    assert inferred_subject("PEP English grade 3.docx", {}) == "english"
    assert inferred_subject("毕业试卷.docx", {"paragraphs": ["六年级数学期末试卷"]}) == "math"
    assert inferred_subject("无科目.docx", {"paragraphs": ["语文和数学成绩分析"]}) == "unknown"


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


def test_warm_selection_uses_only_the_live_backend_index_not_oracle_geometry():
    question = {"id": "index-id", "box": [.1, .2, .6, .3]}
    index = {"page_id": "page", "frame_sha256": "a" * 64, "questions": [question]}
    form = prepared_form(index)
    assert form["prepared_question_id"] == "index-id"
    assert json.loads(form["prepared_geometry"])["frame_sha256"] == "a" * 64
    assert set(form) == {"prepared_question_id", "prepared_geometry", "selected_strokes"}
    with pytest.raises(ValueError):
        prepared_form({**index, "questions": [question, question]})
    with pytest.raises(ValueError):
        valid_tag("../../data")


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


def test_client_task_reservation_is_bound_to_round_and_trace_and_single_use(tmp_path):
    from scripts.question_bank.transport import logical_task
    budget = Budget(tmp_path / "budget.sqlite3")
    reservation = budget.reserve(1, "task", "case:hint1")
    assert not budget.claim_task(reservation, 2, "case:hint1")
    assert not budget.claim_task(reservation, 1, "other:hint1")
    assert budget.claim_task(reservation, 1, "case:hint1")
    assert not budget.claim_task(reservation, 1, "case:hint1")
    assert logical_task("POST", "/v1/hints/stream")
    assert logical_task("POST", "/v1/sessions/own/corrections")
    assert not logical_task("POST", "/v1/sessions/own/page-prep")


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


def test_grading_blank_is_authorized_to_show_answers_and_not_a_first_tier():
    contract = evaluation_contract("blank")
    assert contract["task"] == "correction" and contract["allow_final_answers"]
    assert not contract["answer_withholding_rule_applies"]
    assert evaluation_contract("hint1")["answer_withholding_rule_applies"]
    review = {"verdict": "fail", "mode_understood": "hint1", "reference_reliable": True,
              "scores": dict.fromkeys(("accuracy", "reasoning", "age_fit", "visual_structure", "coherence"), 5),
              "findings": [{"severity": "critical", "quote": "6", "reason": "First tier reveals the answer"}],
              "hard_checks": {"answer_permission": "fail"}}
    corrected = assess_review(review, {"reference_answer": "6"}, "blank")
    assert corrected["verdict"] == "needs_review" and corrected["mode_conflict"]


def test_teacher_reference_does_not_include_scenario_truth_or_pdf_coordinates():
    view = reference_view({"box": [.1, .7, .8, .1], "crop_box": [.1, .7, .8, .1],
                           "wrong_answer": "35", "wrong_reason": "subtraction error", "answer": "45",
                           "independent_solution": {"final_answer": "45", "_call": {"model": "private"}}})
    assert view["answer"] == "45" and not {"box", "crop_box", "wrong_answer", "wrong_reason"} & view.keys()
    assert "_call" not in view["independent_solution"]


def test_batch_coverage_requires_every_question_and_never_guesses_spatial_ownership():
    from scripts.question_bank.batch import score
    rows = [{"ordinal": i + 1, "mode": mode, "box": [0, i / 3, 1, 1 / 3]} for i, mode in enumerate(("correct", "wrong", "blank"))]
    checks = [{"box": [.2, i / 3 + .1, .1, .05], "status": "looks_correct" if i == 0 else "check_again",
               "answer_state": "blank" if i == 2 else "written", "answer": "" if i == 2 else "value"} for i in range(3)]
    assert score({"checks": checks}, {"rows": rows})["status"] == "pass"
    assert score({"checks": checks[:2]}, {"rows": rows})["status"] == "fail"
    checks[0]["box"] = [0, 0, 1, 1]
    result = score({"checks": checks}, {"rows": rows})
    assert result["status"] == "fail" and result["unmatched_checks"] == 1


def test_teacher_word_regions_match_the_rendered_pixels_not_original_pdf_coordinates(tmp_path):
    from PIL import Image, ImageDraw
    source = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(source).rectangle((40, 20, 60, 40), fill="black")
    crop_path = tmp_path / "crop.png"
    source.save(crop_path)
    sheet = student_paper(source, "", None)
    sheet_path = tmp_path / "paper.png"
    sheet.save(sheet_path)
    regions = rendered_text_regions({"crop": "crop.png", "keyword_boxes": [{"text": "ink", "box": [.2, .2, .1, .2]}]}, sheet_path, tmp_path)
    x, y, w, h = regions[0]["box"]
    assert sheet.getpixel((round((x + w / 2) * sheet.width), round((y + h / 2) * sheet.height))) == (0, 0, 0)
    assert rendered_text_regions({"crop": "crop.png"}, sheet_path, tmp_path, "rotation") == []


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


def test_teacher_cannot_pass_missing_visuals_or_invented_blank_answers():
    from scripts.question_bank.gates import apply
    assessed = apply({"mode": "hint1", "response": {"located": True, "visual_beats": [], "explanation_status": "ready", "continuation_available": False}},
                     {"verdict": "pass", "findings": []})
    assert assessed["verdict"] == "fail" and len(assessed["deterministic_checks"]) == 2
    assessed = apply({"mode": "blank", "response": {"checks": [{"answer_state": "written", "answer": "12"}]}},
                     {"verdict": "pass", "findings": []})
    assert assessed["verdict"] == "fail" and assessed["findings"][0]["severity"] == "critical"


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


def test_isolated_secret_survives_restart_without_sharing_family_credentials(tmp_path):
    from scripts.acceptance.serve import secret_for_run
    first = secret_for_run(tmp_path / "run1")
    assert secret_for_run(tmp_path / "run1") == first
    assert secret_for_run(tmp_path / "run2") != first
    assert len(first) >= 48


def test_setup_gateway_retry_is_bounded_across_resumes(monkeypatch, tmp_path):
    import httpx
    from scripts.question_bank import runner
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(1)
        response = httpx.Response(502, request=httpx.Request("POST", "http://localhost/setup"))
        response.raise_for_status()
    monkeypatch.setattr(runner, "bootstrap", unavailable)
    args = ("http://127.0.0.1:8027", tmp_path, {"id": "case", "grade": 1}, tmp_path / "paper.jpg", "scope")
    with pytest.raises(httpx.HTTPStatusError):
        runner.bootstrap_bounded(*args)
    with pytest.raises(RuntimeError, match="previously failed"):
        runner.bootstrap_bounded(*args)
    assert len(calls) == 2
    assert len(json.loads((tmp_path / "private/setup-errors/scope.json").read_text())) == 2


def test_setup_negative_vision_is_not_retried_or_replaced_with_ready(monkeypatch, tmp_path):
    from scripts.question_bank import runner
    calls = []
    def reject(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("Real visual preflight did not accept the synthetic paper")
    monkeypatch.setattr(runner, "bootstrap", reject)
    args = ("http://127.0.0.1:8027", tmp_path, {"id": "case", "grade": 1}, tmp_path / "paper.jpg", "scope")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            runner.bootstrap_bounded(*args)
    assert len(calls) == 1


def test_setup_retry_requires_a_real_success(monkeypatch, tmp_path):
    import httpx
    from scripts.question_bank import runner
    results = iter([httpx.ConnectError("temporary"), {"synthetic": True, "session_id": "real-session"}])
    def bootstrap(*args, **kwargs):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(runner, "bootstrap", bootstrap)
    result = runner.bootstrap_bounded("http://127.0.0.1:8027", tmp_path, {"id": "case", "grade": 1}, tmp_path / "paper.jpg", "scope")
    assert result["session_id"] == "real-session"


@pytest.mark.parametrize("label,expected", [("2.", "第2题"), ("3", "第3题"), ("二、（4）", "第二大题第4小题"),
                                           ("左页第2大题第3小题", "左页第2大题第3小题"),
                                           ("小明有12块糖，吃了3块，还剩多少块", None)])
def test_reading_speaks_numbers_instead_of_repeating_a_full_stem_as_title(label, expected):
    from k12_server.reading_review import spoken_question_label
    assert spoken_question_label(label) == expected


def test_physical_fallback_and_tts_calls_share_budget_and_worker_trace(monkeypatch, tmp_path):
    import httpx
    from types import SimpleNamespace
    from k12_server import ai, tts
    from scripts.question_bank.transport import TRACE, install
    monkeypatch.setattr(ai, "_model_unavailable_until", {})
    attempts = []
    def post(url, **kwargs):
        attempts.append(kwargs["json"]["model"])
        data = {"error": {"code": "model_not_found"}} if len(attempts) == 1 else {"model": "gpt-6-astra", "usage": {"prompt_tokens": 5}}
        return httpx.Response(503 if len(attempts) == 1 else 200, json=data, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "stream", httpx.stream)
    monkeypatch.setattr(ThreadPoolExecutor, "submit", ThreadPoolExecutor.submit)
    monkeypatch.setattr(tts, "tencent_audio", lambda *_a, **_k: iter([b"pcm"]))
    monkeypatch.setattr(tts, "provider_audio", tts.provider_audio)
    path = tmp_path / "attempts.jsonl"
    install(tmp_path / "budget.sqlite3", 1, path)
    token = TRACE.set("test:owned-case")
    try:
        settings = SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test-secret", ai_timeout_seconds=5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            observed = pool.submit(lambda: (TRACE.get(), ai.post_model(settings, {"model": "gpt-6.1-sol", "question": "PRIVATE_QUESTION"}))).result()
        assert observed[0] == "test:owned-case"
        assert attempts == ["gpt-6.1-sol", "gpt-6-astra"]
        assert list(tts.tencent_audio()) == [b"pcm"]
    finally:
        TRACE.reset(token)
    assert Budget(tmp_path / "budget.sqlite3").summary()["counts"] == [{"round": 1, "kind": "upstream", "count": 3}]
    text = path.read_text(encoding="utf-8")
    assert "PRIVATE_QUESTION" not in text and "test-secret" not in text
    assert all(json.loads(line)["trace"] == "test:owned-case" for line in text.splitlines())
