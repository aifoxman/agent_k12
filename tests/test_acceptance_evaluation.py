"""The synthetic evaluator must reject known bad outputs independently of the tutor."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def acceptance(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts" / "acceptance"))
    return (
        importlib.import_module("evaluate"),
        importlib.import_module("cases"),
        importlib.import_module("scoring"),
    )


def _case(modules, identifier, tmp_path):
    _, cases, _ = modules
    source = next(case for case in cases.definitions() if case["id"] == identifier)
    return cases.render(source, tmp_path / f"{identifier}.jpg")


def test_six_synthetic_cases_have_separate_truth_and_no_user_photos(acceptance, tmp_path):
    runner, _, _ = acceptance
    manifest = runner.prepare(tmp_path)
    assert manifest["synthetic"] and len(manifest["cases"]) == 6
    assert len({case["id"] for case in manifest["cases"]}) == 6
    for case in manifest["cases"]:
        assert (tmp_path / case["image"]).is_file()
        assert case["oracle"]["regions"]


@pytest.mark.parametrize(
    ("identifier", "verdict"),
    [
        ("arithmetic_wrong", "looks_correct"),
        ("units_correct", "check_again"),
        ("language_open", "check_again"),
    ],
)
def test_grading_oracle_rejects_wrong_verdicts(acceptance, tmp_path, identifier, verdict):
    _, _, scoring = acceptance
    case = _case(acceptance, identifier, tmp_path)
    result = scoring.evaluate(case, {"checks": [{"status": verdict}]})
    assert result["status"] == "fail"


def test_abstention_and_open_expression_are_not_fake_precise_passes(acceptance, tmp_path):
    _, _, scoring = acceptance
    case = _case(acceptance, "arithmetic_wrong", tmp_path)
    assert scoring.evaluate(case, {"checks": [{"status": "unclear"}]})["status"] == "needs_review"
    case = _case(acceptance, "language_open", tmp_path)
    assert (
        scoring.evaluate(case, {"checks": [{"status": "looks_correct"}]})["status"]
        == "needs_review"
    )


def test_correct_verdict_cannot_hide_wrong_reference_or_wrong_ocr(acceptance, tmp_path):
    _, _, scoring = acceptance
    case = _case(acceptance, "arithmetic_wrong", tmp_path)
    result = scoring.evaluate(
        case, {"checks": [{"status": "check_again", "answer": "25", "reference_answer": "44"}]}
    )
    failures = {check["name"] for check in result["checks"] if check["status"] == "fail"}
    assert failures == {"reference_answer_1", "read_written_answer_1"}


@pytest.mark.parametrize("speech", ["每份有4块。", "答案是四块。", "12÷3＝4。"])
def test_answer_leakage_is_detected_in_arabic_and_chinese(acceptance, tmp_path, speech):
    _, _, scoring = acceptance
    case = _case(acceptance, "fraction_concept", tmp_path)
    result = scoring.evaluate(case, {"located": True, "response": speech})
    assert (
        next(check for check in result["checks"] if check["name"] == "withheld_final_answer")[
            "status"
        ]
        == "fail"
    )


def test_false_confirmation_of_incorrect_units_is_a_hard_failure(acceptance, tmp_path):
    _, _, scoring = acceptance
    case = _case(acceptance, "units_wrong_thought", tmp_path)
    result = scoring.evaluate(
        case, {"located": True, "response": "完全正确，都是长度就可以直接减。"}
    )
    assert result["status"] == "fail"
    good = scoring.evaluate(
        case, {"located": True, "response": "先把两个长度换成相同的单位，再用减法。"}
    )
    assert good["status"] == "pass"


def test_off_topic_concept_reply_requires_review(acceptance, tmp_path):
    _, _, scoring = acceptance
    case = _case(acceptance, "fraction_concept", tmp_path)
    result = scoring.evaluate(case, {"located": True, "response": "先认真读题，再想一想。"})
    assert result["status"] == "needs_review"


def test_geometry_and_exact_keyword_truth_reject_large_coordinate_errors(acceptance, tmp_path):
    _, _, scoring = acceptance
    case = _case(acceptance, "geometry_guidance", tmp_path)
    bad = {
        "located": True,
        "response": "周长是一圈的长度。",
        "visual_beats": [
            {
                "page": [
                    {"kind": "label", "text": "A", "box": [0.8, 0.8, 0.02, 0.02]},
                    {"kind": "underline", "text": "8厘米", "box": [0.9, 0.8, 0.05, 0.02]},
                ]
            }
        ],
    }
    result = scoring.evaluate(case, bad)
    assert result["status"] == "fail"
    assert sum(check["status"] == "fail" for check in result["checks"]) == 2


def test_oracle_supports_arbitrary_visible_phrase_without_a_whole_line_shortcut(
    acceptance, tmp_path
):
    _, _, scoring = acceptance
    case = _case(acceptance, "geometry_guidance", tmp_path)
    boxes = scoring._expected_text_boxes(case, "长8厘米")
    assert len(boxes) == 1 and boxes[0][2] < 0.2
    raw = {
        "located": True,
        "response": "圈出长8厘米和宽3厘米，周长是一圈的长度。",
        "visual_beats": [
            {
                "page": [
                    {"kind": "circle", "text": "长8厘米", "box": boxes[0]},
                    {
                        "kind": "circle",
                        "text": "宽3厘米",
                        "box": scoring._expected_text_boxes(case, "宽3厘米")[0],
                    },
                ]
            }
        ],
    }
    assert scoring.evaluate(case, raw)["status"] == "pass"


def test_production_hint_request_does_not_include_oracle(acceptance, tmp_path):
    runner, _, _ = acceptance
    case = _case(acceptance, "fraction_concept", tmp_path)
    case["oracle"]["private_canary"] = "EVALUATOR_TRUTH_MUST_NOT_BE_SENT"
    requests = []

    def respond(request):
        requests.append(request.content)
        body = 'event: done\ndata: {"located":true,"response":"每份要同样多。"}\n\n'
        return httpx.Response(200, text=body)

    with httpx.Client(
        base_url="http://127.0.0.1:8016", transport=httpx.MockTransport(respond)
    ) as client:
        result = runner._stream_hint(
            client,
            case,
            b"synthetic",
            "session",
            {"Authorization": "Bearer synthetic"},
            tmp_path / "events.jsonl",
        )
    assert result["located"]
    assert b"EVALUATOR_TRUTH_MUST_NOT_BE_SENT" not in requests[0]
    assert b"withhold_answer" not in requests[0]
    assert b"correct_answer" not in requests[0]


def test_run_flushes_results_and_rejects_real_local_service(acceptance, monkeypatch, tmp_path):
    runner, _, _ = acceptance
    with pytest.raises(ValueError):
        runner._safe_base_url("http://127.0.0.1:8003")
    runner.prepare(tmp_path)
    monkeypatch.setenv("ACCEPTANCE_CHILD_TOKEN", "synthetic-child")
    monkeypatch.setenv("ACCEPTANCE_SESSION_ID", "synthetic-session")
    monkeypatch.delenv("ACCEPTANCE_PARENT_TOKEN", raising=False)
    original = httpx.Client

    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(
                200, json={"status": "ok"}, headers={"X-K12-Acceptance-Run": tmp_path.parent.name}
            )
        return httpx.Response(
            200, text='event: done\ndata: {"located":true,"response":"每份同样多。"}\n\n'
        )

    monkeypatch.setattr(
        runner.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = runner.run(tmp_path, "http://127.0.0.1:8016", ["fraction_concept"], 1, 10)
    assert result["completed_at"] and result["counts"]["pass"] == 1
    saved = json.loads((tmp_path / "results" / "fraction_concept.json").read_text(encoding="utf-8"))
    assert saved["started_at"] and saved["completed_at"]
    assert saved["models"]["actual_upstream_models"] is None
    assert "synthetic-child" not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_model_trace_attachment_retains_real_ids_but_excludes_sensitive_fields(
    acceptance, tmp_path
):
    runner, _, _ = acceptance
    runner.prepare(tmp_path)
    path = tmp_path / "results" / "arithmetic_wrong.json"
    runner.write_json(
        path,
        {
            "started_at": "2026-10-22T20:00:00+00:00",
            "completed_at": "2026-10-22T20:01:00+00:00",
            "models": {"configured": {"grading_model": "configured"}},
        },
    )
    trace = tmp_path / "server-events.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "at": "2026-10-22T20:00:01+00:00",
                    "type": "model_start",
                    "call_id": "one",
                    "configured_model": "configured",
                    "api_key": "DO_NOT_COPY",
                },
                {
                    "at": "2026-10-22T20:00:05+00:00",
                    "type": "model_done",
                    "call_id": "one",
                    "response_model": "actual-upstream",
                    "image": "DO_NOT_COPY",
                },
                {
                    "at": "2026-10-22T20:00:06+00:00",
                    "type": "model_start",
                    "call_id": "stream",
                    "configured_model": "stream-model",
                },
                {
                    "at": "2026-10-22T20:00:08+00:00",
                    "type": "model_done",
                    "call_id": "stream",
                    "response_model": None,
                },
            ]
        ),
        encoding="utf-8",
    )
    runner.attach_trace(tmp_path, trace)
    text = path.read_text(encoding="utf-8")
    result = json.loads(text)
    assert result["models"]["actual_upstream_models"] == ["actual-upstream"]
    assert result["models"]["unresolved_call_ids"] == ["stream"]
    assert result["models"]["upstream_identity_complete"] is False
    assert "DO_NOT_COPY" not in text and "api_key" not in text
