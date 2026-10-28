"""Locator coordinates have declared units; integer scale is never guessed."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from k12_server import ai

FOCUS = [.1, .2, .8, .6]
WORD = {"id": "t", "kind": "text", "text": "长度", "confidence": .96}
SETTINGS = SimpleNamespace(ai_api_url="https://unused.invalid", ai_api_key="unused",
                           ai_model="mock-locator", ai_timeout_seconds=120)


def read(monkeypatch, raw, **options):
    requests = []

    def post(_settings, payload, timeout, fallback):
        requests.append((payload, timeout, fallback))
        return {"choices": [{"message": {"content": json.dumps(raw)}}]}

    monkeypatch.setattr(ai, "post_model", post)
    result = ai.read_teaching_anchors(SETTINGS, b"not-sent", FOCUS, FOCUS, "已知长度", **options)
    assert len(requests) == 1 and requests[0][1:] == (30, False)
    instruction = requests[0][0]["messages"][0]["content"]
    assert '"coordinate_space":"normalized_0_1000"' in instruction
    assert '"box_format":"xywh"' in instruction
    return result


def test_explicit_thousand_scale_converts_xywh_before_inverse_crop_mapping(monkeypatch):
    result = read(monkeypatch, {"coordinate_space": "normalized_0_1000", "box_format": "xywh",
                               "anchors": [{**WORD, "box": [250, 300, 100, 100]}]})
    assert result == [{"id": "t", "text": "长度", "confidence": .96, "box": [.3, .38, .08, .06]}]


@pytest.mark.parametrize("declaration", [{}, {"coordinate_space": "normalized_0_1", "box_format": "xywh"}])
def test_legacy_normalized_coordinates_remain_compatible(monkeypatch, declaration):
    result = read(monkeypatch, {**declaration, "anchors": [{**WORD, "box": [.25, .3, .1, .1]}]})
    assert result[0]["box"] == [.3, .38, .08, .06]


@pytest.mark.parametrize("declaration", [
    {}, {"coordinate_space": "pixels", "box_format": "xywh"},
    {"coordinate_space": "normalized_0_1000"},
    {"coordinate_space": "normalized_0_1000", "box_format": "xyxy"},
    {"coordinate_space": "normalized_0_1000", "box_format": "unknown"},
    {"coordinate_space": ["normalized_0_1000"], "box_format": "xywh"},
])
def test_unknown_or_missing_integer_units_and_xyxy_are_not_inferred(monkeypatch, declaration):
    assert read(monkeypatch, {**declaration, "anchors": [{**WORD, "box": [250, 300, 100, 100]}]}) == []


def test_declared_xyxy_is_rejected_even_when_numbers_could_pass_as_normalized_xywh(monkeypatch):
    assert read(monkeypatch, {"box_format": "xyxy",
                              "anchors": [{**WORD, "box": [.2, .2, .3, .3]}]}) == []


@pytest.mark.parametrize("box", [[950, 100, 100, 100], [-1, 100, 20, 20],
                                 [100, 100, 0, 20], [True, 100, 20, 20],
                                 [100, 100, float("nan"), 20], {"x": 100, "y": 200}, [1, 2, 3]])
def test_declared_scale_never_licenses_invalid_or_outside_boxes(monkeypatch, box):
    assert read(monkeypatch, {"coordinate_space": "normalized_0_1000", "box_format": "xywh",
                              "anchors": [{**WORD, "box": box}]}) == []


def test_point_box_and_visible_segment_endpoints_share_the_declared_scale(monkeypatch):
    raw = {"coordinate_space": "normalized_0_1000", "box_format": "xywh", "anchors": [
        {"kind": "point", "id": "p", "text": "左顶点", "box": [300, 400, 20, 20], "confidence": .96},
        {"kind": "segment", "id": "s", "text": "可见底边", "x1": 100, "y1": 300,
         "x2": 800, "y2": 300, "confidence": .96}]}
    result = read(monkeypatch, raw, geometry_needed=True)
    assert result[0]["x"] == pytest.approx(.348) and result[0]["y"] == pytest.approx(.446)
    assert result[1]["x1"] == pytest.approx(.18) and result[1]["x2"] == pytest.approx(.74)
    assert result[1]["y1"] == result[1]["y2"] == pytest.approx(.38)
    raw.pop("coordinate_space")
    assert read(monkeypatch, raw, geometry_needed=True) == []


def test_scale_does_not_bypass_word_identity_confidence_or_question_bounds(monkeypatch):
    raw = {"coordinate_space": "normalized_0_1000", "box_format": "xywh", "anchors": [
        {**WORD, "box": [250, 300, 100, 100]},
        {**WORD, "id": "unseen", "text": "没有的条件", "box": [500, 300, 100, 100]},
        {**WORD, "id": "low", "confidence": .4, "box": [500, 300, 100, 100]}]}
    assert [item["id"] for item in read(monkeypatch, raw)] == ["t"]


def test_schema_logs_count_exact_unit_and_box_rejections_without_returning_text(monkeypatch):
    messages = []

    class Capture(logging.Handler):
        def emit(self, record):
            messages.append(json.loads(record.getMessage()))

    handler = Capture()
    ai._teaching_logger.addHandler(handler)
    try:
        with ai.teaching_call_context("", "test_trace"):
            read(monkeypatch, {"anchors": [{**WORD, "box": [250, 300, 100, 100]}]})
        last = messages[-1]
        assert last["raw"] == 1 and last["mapped"] == 0 and last["drop_box_range"] == 1
        assert last["coordinate_declared"] is False and last["converted"] == 0
        assert "长度" not in json.dumps(messages, ensure_ascii=False)
    finally:
        ai._teaching_logger.removeHandler(handler)
