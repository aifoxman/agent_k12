"""Optional on-device OCR is tied to the exact photo and earns text-only permission."""

from __future__ import annotations

import hashlib
import io
import json
import logging
from copy import deepcopy
from types import SimpleNamespace

import anyio
import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont
from starlette.concurrency import iterate_in_threadpool
from test_teaching_refinement import _session

from k12_server import ai

TARGET = [.05, .05, .9, .9]


def photo_payload():
    picture = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(picture)
    font = ImageFont.load_default(size=24)
    draw.text((120, 130), "15 m", font=font, fill="black")
    left, top, right, bottom = draw.textbbox((120, 130), "15 m", font=font)
    output = io.BytesIO()
    picture.save(output, format="JPEG", quality=95)
    image = output.getvalue()
    payload = {"image_sha256": hashlib.sha256(image).hexdigest(), "width": 640, "height": 480,
               "anchors": [{"id": "local_e_1", "level": "element", "text": "15 m",
                            "box": [(left - 5) / 640, (top - 5) / 480,
                                    (right - left + 10) / 640, (bottom - top + 10) / 480],
                            "confidence": .96}]}
    return image, payload


def test_same_photo_literal_ocr_passes_actual_pixel_verification():
    image, payload = photo_payload()
    result = ai.local_teaching_text_anchors(json.dumps(payload), image, TARGET, "长度15m")
    assert result == [{"id": "ocr_local_e_1", "text": "15 m",
                       "box": ai.normalized_mark_box(payload["anchors"][0]["box"]), "confidence": .96}]
    assert result[0].get("kind", "text") == "text"


@pytest.mark.parametrize("change", [
    {"image_sha256": "0" * 64}, {"width": 480}, {"height": True},
    {"anchors": [None] * 121}, {"anchors": "not-a-list"},
])
def test_local_ocr_rejects_other_photo_dimensions_or_unbounded_payload(change):
    image, payload = photo_payload()
    assert ai.local_teaching_text_anchors(json.dumps({**payload, **change}), image, TARGET, "长度15m") == []


@pytest.mark.parametrize("change", [
    {"confidence": None}, {"confidence": False}, {"confidence": .89}, {"confidence": 1.1},
    {"confidence": float("nan")}, {"confidence": float("inf")}, {"kind": "region"},
    {"level": "inferred"}, {"text": "15厘米"}, {"text": ""}, {"text": "x" * 81},
    {"box": [.99, .4, .1, .1]}, {"box": [False, .4, .1, .1]},
    {"box": [.4, .4, float("nan"), .1]}, {"box": [.4, .4, .1, .1]},
    {"box": [.01, .01, .02, .02]}, {"id": "invalid id"},
])
def test_unobserved_confidence_words_positions_or_geometry_cannot_become_local_text(change):
    image, payload = photo_payload()
    payload["anchors"][0].update(change)
    assert ai.local_teaching_text_anchors(json.dumps(payload), image, TARGET, "长度15m") == []


def test_local_ocr_is_bounded_and_nonsemantic():
    image, payload = photo_payload()
    assert ai.local_teaching_text_anchors("x" * 65537, image, TARGET, "长度15m") == []
    assert ai.local_teaching_text_anchors(json.dumps(payload), image, TARGET, "长度十五米") == []
    assert ai.local_teaching_text_anchors("[]", image, TARGET, "长度15m") == []
    assert ai.local_teaching_text_anchors("{", image, TARGET, "长度15m") == []


@pytest.mark.parametrize("endpoint", ["/v1/hints", "/v1/hints/stream"])
@pytest.mark.parametrize("same_photo", [True, False])
def test_both_hint_routes_use_only_same_photo_local_words_without_extra_text_read(
        monkeypatch, tmp_path, endpoint, same_photo):
    client, module, child, session, _ = _session(monkeypatch, tmp_path)
    image, payload = photo_payload()
    if not same_photo:
        payload["image_sha256"] = "0" * 64
    target = {"id": "q1", "label": "测试题", "box": TARGET, "evidence": "长度15m",
              "read_confidence": .96, "verification_status": "verified",
              "solution": {"answer": "未知结果", "steps": "解释长度条件", "confidence": .96,
                           "ambiguous": False}}
    monkeypatch.setattr(module, "page_preparation", lambda: SimpleNamespace(match=lambda *_a, **_k: deepcopy(target)))
    reads = []
    monkeypatch.setattr(module, "read_teaching_anchors", lambda *_a, **_k: reads.append(1) or [])
    raw = {"speech": "先划出15 m这个长度条件。", "teaching_move": "give_clue", "reply_mode": "none",
           "page": [{"kind": "underline", "anchor_id": "ocr_local_e_1"}], "scratch": []}
    monkeypatch.setattr(module, "stream_model", lambda *_a: iter([json.dumps(raw) + "\n"]))
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: {
        "response": raw["speech"], "visual_cue": raw["page"][0],
        "teaching_move": "give_clue", "reply_mode": "none"})
    response = client.post(endpoint, data={"session_id": session, "question": "解释长度条件",
                                           "local_text_anchors": json.dumps(payload)},
                           files={"image": ("page.jpg", image, "image/jpeg")}, headers=child)
    assert response.status_code == 200
    with module.connect(module.settings.database_path) as db:
        row = db.execute("SELECT teaching_json FROM hints WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                         (session,)).fetchone()
    scene = json.loads(row["teaching_json"])
    marks = [mark for beat in scene["beats"] for mark in beat["page"]]
    assert bool(marks) is same_photo
    assert reads == ([] if same_photo else [1])
    if same_photo:
        assert marks[0]["kind"] == "underline" and marks[0]["anchor_id"] == "ocr_local_e_1"


def test_stream_context_rebinds_each_next_and_close_across_real_starlette_workers(monkeypatch, tmp_path):
    from test_api import make_client

    _, module = make_client(monkeypatch, tmp_path)
    observed = []

    def events():
        try:
            yield "first"
            observed.append((ai._usage_family.get(), ai._teaching_trace.get()))
            yield "second"
        finally:
            observed.append((ai._usage_family.get(), ai._teaching_trace.get()))

    async def collect():
        result = [part async for part in iterate_in_threadpool(
            module._hint_stream_context(events(), "synthetic-family", "trace0123"))]
        assert result == ["first", "second"]

    anyio.run(collect)
    assert observed == [("synthetic-family", "trace0123")] * 2
    assert ai._usage_family.get() != "synthetic-family" and ai._teaching_trace.get() is None
    observed.clear()

    async def close_early():
        iterator = module._hint_stream_context(events(), "synthetic-family", "trace0123")
        assert await anyio.to_thread.run_sync(next, iterator) == "first"
        await anyio.to_thread.run_sync(iterator.close)

    anyio.run(close_early)
    assert observed == [("synthetic-family", "trace0123")]


def test_usage_after_stream_status_yield_is_saved_to_the_correct_synthetic_family(monkeypatch, tmp_path):
    _, module, _, session, _ = _session(monkeypatch, tmp_path)
    with module.connect(module.settings.database_path) as db:
        family = db.execute("SELECT family_id FROM sessions WHERE id=?", (session,)).fetchone()[0]
    monkeypatch.setattr(ai.httpx, "post", lambda *_a, **_k: SimpleNamespace(
        status_code=200, raise_for_status=lambda: None,
        json=lambda: {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3,
                                                "total_tokens": 10}}))

    def events():
        yield "status"
        ai.post_model(module.settings, {"model": "mock-model"})
        yield "beat"

    async def collect():
        assert [part async for part in iterate_in_threadpool(
            module._hint_stream_context(events(), family, "trace0123"))] == ["status", "beat"]

    anyio.run(collect)
    with module.connect(module.settings.database_path) as db:
        rows = db.execute("SELECT family_id,model,total_tokens FROM ai_usage").fetchall()
    assert [tuple(row) for row in rows] == [(family, "mock-model", 10)]


def test_saved_move_scene_keeps_region_references_and_explicit_schematic_label(monkeypatch, tmp_path):
    from test_api import make_client

    _, module = make_client(monkeypatch, tmp_path)
    mark = {"kind": "arrow", "purpose": "move", "label": "搬入示意", "anchor_id": "move:a:b",
            "from_anchor_id": "a", "to_anchor_id": "b", "anchor_text": "左侧突出部分移入中间缺口",
            "x1": .2, "y1": .3, "x2": .5, "y2": .4}
    scene = module._hint_teaching_scene(TARGET, "题目", [{"speech": "把左侧突出部分移入中间缺口。",
                                                        "page": [mark], "scratch": []}])
    assert json.loads(json.dumps(scene))["beats"][0]["page"] == [mark]


def test_anchor_stage_logs_record_real_failure_class_without_private_content(monkeypatch):
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = Capture()
    ai._teaching_logger.addHandler(handler)
    settings = SimpleNamespace(ai_api_url="https://unused.invalid", ai_api_key="unused",
                               ai_model="unused", ai_timeout_seconds=120)
    monkeypatch.setattr(ai, "post_model", lambda *_a: (_ for _ in ()).throw(
        httpx.ReadTimeout("PRIVATE_STUDENT_SENTINEL")))
    try:
        with ai.teaching_call_context("PRIVATE_FAMILY_SENTINEL", "trace0123"):
            assert ai.read_teaching_anchors(settings, b"not-sent", TARGET, TARGET,
                                             "PRIVATE_QUESTION_SENTINEL") == []
            ai.teaching_stage("safe_metrics", question="PRIVATE_QUESTION_SENTINEL", accepted=0)
    finally:
        ai._teaching_logger.removeHandler(handler)
    joined = "\n".join(captured)
    assert "ReadTimeout" in joined and "elapsed_ms" in joined and "anchor_read_failed" in joined
    assert "PRIVATE_" not in joined
    assert ai._teaching_logger.level == logging.INFO and ai._teaching_logger.handlers
