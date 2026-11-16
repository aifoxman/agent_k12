"""A local selection remains a proposal until actual page pixels agree."""

import io
import json
import hashlib
import random

import pytest
from PIL import Image, ImageDraw

from k12_server.paper_registration import IDENTITY, complete_answer_regions, map_box, stroke_scores, valid_transform
from k12_server import page_prep
from test_prepared_lesson_wait import prepared  # noqa: F401


def paper(seed=13, offset=(0, 0)):
    image = Image.new("RGB", (640, 640), "white")
    draw, rng = ImageDraw.Draw(image), random.Random(seed)
    for y in range(35, 600, 29):
        for x in range(35, 600, 31):
            w, h = rng.randrange(7, 21), rng.randrange(9, 23)
            px, py = x + offset[0], y + offset[1]
            draw.line((px, py+h, px, py, px+w, py), fill="black", width=3)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def test_mapping_requires_real_texture_across_page_and_selected_question():
    image = paper()
    assert valid_transform(image, image, IDENTITY, [.1, .1, .7, .7])
    moved = paper(offset=(16, 16))
    matrix = [1, 0, 16/639, 0, 1, 16/639, 0, 0, 1]
    assert valid_transform(image, moved, matrix, [.1, .1, .7, .7])
    assert not valid_transform(image, paper(321), IDENTITY, [.1, .1, .7, .7])
    assert not valid_transform(image, moved, IDENTITY, [.1, .1, .7, .7])
    assert not valid_transform(image, image, matrix, [.1, .1, .7, .7])
    assert not valid_transform(image, moved, [float("nan")] * 9, [.1, .1, .7, .7])
    assert map_box(matrix, [.1, .1, .2, .2]) == pytest.approx([.1+16/639, .1+16/639, .2, .2])


def test_answer_area_is_used_and_cross_question_scores_remain_ambiguous():
    questions = [{"id": "one", "box": [.1, .1, .7, .15], "answer_regions": [[.1, .25, .7, .1]]},
                 {"id": "two", "box": [.1, .4, .7, .4]}]
    assert stroke_scores(questions, [[[.2, .3], [.6, .3]]])[0][2]["id"] == "one"
    scores = stroke_scores(questions, [[[.3, .2], [.3, .55]]])
    assert abs(scores[0][0] - scores[1][0]) < .16


def test_writing_gap_extends_to_the_next_question_in_the_same_column_only():
    items = [{"id": "one", "box": [.1, .1, .35, .05], "read_confidence": .96},
             {"id": "right-column", "box": [.55, .2, .35, .05], "read_confidence": .96},
             {"id": "two", "box": [.1, .3, .35, .05], "read_confidence": .96}]
    completed = complete_answer_regions(items)
    assert completed[0]["answer_regions"][0] == pytest.approx([.1, .15, .35, .144])
    assert not completed[1]["answer_regions"] and not completed[2]["answer_regions"]
    assert stroke_scores(completed, [[[.2, .25], [.4, .25]]])[0][2]["id"] == "one"


def test_incremental_scan_publishes_a_whole_question_before_later_tokens(monkeypatch):
    value = {"path": ["1"], "label": "one", "evidence": "visible printed question",
             "box": [.1, .1, .7, .3], "progress": "blank", "confidence": .96,
             "answer_regions": [[.1, .4, .7, .2]]}
    published = []
    def stream(*_):
        line = json.dumps(value)
        yield line[:20]
        assert not published
        yield line[20:] + "\n"
        assert len(published) == 1
        yield 'not a question\n'
    monkeypatch.setattr(page_prep, "stream_model", stream)
    from types import SimpleNamespace
    result = page_prep.inventory_page(SimpleNamespace(ai_model="test"), paper(), None,
                                      lambda item, ordinal: published.append((item, ordinal)))
    assert len(result) == 1 and published[0][1] == 0
    assert result[0]["box"] == [.1, .1, .7, .3]
    assert result[0]["answer_regions"] == [[.1, .4, .7, .2]]


@pytest.mark.parametrize("wrapper", ["pretty", "array", "question"])
def test_streaming_inventory_accepts_complete_provider_wrappers_without_partial_objects(monkeypatch, wrapper):
    from types import SimpleNamespace
    item = {"coordinate_space": "normalized_0_1000", "path": ["1"], "label": "one",
            "evidence": "visible printed question", "box": [100, 100, 700, 300],
            "progress": "blank", "confidence": .96}
    value = [item] if wrapper == "array" else {"question": item} if wrapper == "question" else item
    text = json.dumps(value, indent=2)
    received = []
    def stream(*_):
        yield text[:30]
        assert not received
        yield text[30:]
    monkeypatch.setattr(page_prep, "stream_model", stream)
    monkeypatch.setattr(page_prep, "post_model", lambda *_a, **_k: pytest.fail("Unnecessary second read"))
    result = page_prep.inventory_page(SimpleNamespace(ai_model="test"), paper(), None,
                                      lambda item, ordinal: received.append(item))
    assert len(received) == 1 and result[0]["box"] == [.1, .1, .7, .3]


def test_empty_stream_gets_one_bounded_ordinary_scan_instead_of_silently_disabling_cache(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(page_prep, "stream_model", lambda *_a: iter([]))
    calls = []
    def ordinary(_settings, payload, **kwargs):
        calls.append(kwargs)
        assert payload["response_format"] == {"type": "json_object"}
        return {"choices": [{"message": {"content": json.dumps({"questions": [{
            "path": ["1"], "label": "one", "evidence": "real visible question",
            "box": [.1, .1, .7, .3], "progress": "blank", "confidence": .96}]})}}]}
    monkeypatch.setattr(page_prep, "post_model", ordinary)
    result = page_prep.inventory_page(SimpleNamespace(ai_model="test", ai_timeout_seconds=90),
                                      paper(), None, lambda *_a: None)
    assert len(result) == 1 and calls == [{"timeout": 30}]


def test_scan_transport_timeout_uses_the_configured_fast_vision_recovery(prepared, monkeypatch):
    import httpx
    from dataclasses import replace
    _, module, _, _, _, image, _ = prepared
    settings = replace(module.settings, ai_model="gpt-6-sol", ai_fast_vision_model="gpt-6-luna")
    def unavailable(scan_settings, _payload):
        assert scan_settings.ai_timeout_seconds <= 30
        raise httpx.ReadTimeout("synthetic unavailable stream")
        yield  # This test double follows the streaming generator contract.
    monkeypatch.setattr(page_prep, "stream_model", unavailable)
    def ordinary(read_settings, payload, **kwargs):
        assert read_settings.ai_model == payload["model"] == "gpt-6-luna"
        return {"choices": [{"message": {"content": json.dumps({"questions": [{
            "path": ["1"], "label": "one", "evidence": "real visible question",
            "box": [.1, .1, .7, .3], "progress": "blank", "confidence": .96}]})}}]}
    monkeypatch.setattr(page_prep, "post_model", ordinary)
    assert len(page_prep.inventory_page(settings, image, None, lambda *_a: None)) == 1


def test_index_exposes_no_answers_and_binding_checks_hash_session_pixels_and_overlap(prepared):
    _, module, _, session, family, image, service = prepared
    service.frames["current-page"] = image
    snapshot = service.index(session, family["id"], "current-page")
    assert snapshot and snapshot["questions"][0]["solution_ready"] is False
    assert not any(key in snapshot["questions"][0] for key in ("solution", "solution_json", "answer", "steps"))
    geometry = {"page_id": "current-page", "frame_sha256": hashlib.sha256(image).hexdigest(), "matrix": IDENTITY}
    stroke = [[[.2, .3], [.6, .3]]]
    bound = service.bind_selection(session, family["id"], image, "current-question", geometry, stroke)
    assert bound and bound["id"] == "current-question"
    assert service.bind_selection(session, family["id"], image, "current-question", {**geometry, "frame_sha256": "wrong"}, stroke) is None
    assert service.bind_selection("another", family["id"], image, "current-question", geometry, stroke) is None
    assert service.bind_selection(session, family["id"], paper(), "current-question", geometry, stroke) is None
    assert service.bind_selection(session, family["id"], image, "current-question", geometry, [[[.92, .92], [.98, .98]]]) is None


def test_old_reference_remains_immutable_when_progress_photo_moves(prepared, monkeypatch):
    _, module, _, session, family, image, service = prepared
    with module.transaction(module.settings.database_path) as db:
        db.execute("UPDATE page_preparations SET status='ready'")
    service.frames["current-page"] = image
    calls = []
    monkeypatch.setattr(service, "schedule", lambda *args, **kw: calls.append((args, kw)))
    config = module.preferences(module.settings.database_path, family["id"])
    service._refresh_progress(session, family["id"], "current-page", paper(), config)
    assert calls and calls[0][1]["force"]
    assert service.frames["current-page"] == image


def test_verified_local_selection_starts_cached_reading_without_model_location(prepared, monkeypatch):
    client, module, child, session, family, image, service = prepared
    from test_prepared_lesson_wait import SOLUTION, events
    service.frames["current-page"] = image
    with module.transaction(module.settings.database_path) as db:
        db.execute("UPDATE prepared_questions SET solution_json=?,verification_status='verified',"
                   "reading_json=?", (json.dumps(SOLUTION), json.dumps({
                       "conditions": ["本题两个部分"], "ask": "说明关系", "anchors": []})))
    monkeypatch.setattr(module, "locate_question", lambda *_a: pytest.fail("Unnecessary model location"))
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: iter([("beat", {
        "speech": "接着比较这两个部分的关系。", "step_id": 1, "step_end": True,
        "page": [], "scratch": [], "reply_mode": "none", "teaching_move": "give_clue"})]))
    geometry = {"page_id": "current-page", "frame_sha256": hashlib.sha256(image).hexdigest(), "matrix": IDENTITY}
    response = client.post("/v1/hints/stream", headers=child, data={
        "session_id": session, "question": "读题并说明解题思路", "level": 1,
        "prepared_question_id": "current-question", "prepared_geometry": json.dumps(geometry),
        "selected_strokes": json.dumps([[[.2, .3], [.6, .3]]])},
        files={"image": ("page.jpg", image, "image/jpeg")})
    assert response.status_code == 200
    result = events(response)
    assert next(data for kind, data in result if kind == "target")["question_id"] == "current-question"
    assert next(data for kind, data in result if kind == "beat")["phase"] == "reading"
    assert result[-1][0] == "done"


def test_anchor_lookup_overlaps_solution_instead_of_starting_after_it(prepared, monkeypatch):
    from threading import Event
    from test_prepared_lesson_wait import SOLUTION, ask
    _, module, _, _, _, _, _ = prepared
    entered, release = Event(), Event()
    def anchors(_image, target, *_args):
        entered.set()
        assert release.wait(2)
        return target
    def solve(_s, _f, _i, target, *_args, **_kwargs):
        assert entered.wait(1), "Anchor lookup was still blocked behind the solve"
        release.set()
        return {**target, "solution": SOLUTION, "verification_status": "verified"}
    monkeypatch.setattr(module, "_teaching_anchors", anchors)
    monkeypatch.setattr(module, "_resolve_teaching_solution", solve)
    monkeypatch.setattr(module, "_refresh_teaching_solution", lambda _s, _f, _i, item, *_a, **_k: item)
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: iter([("beat", {
        "speech": "先比较这两个部分的关系。", "step_id": 1, "step_end": True,
        "page": [], "scratch": [], "reply_mode": "none", "teaching_move": "give_clue"})]))
    try:
        response = ask(prepared)
        assert response.status_code == 200 and 'event: done' in response.text
    finally:
        release.set()


def test_slow_optional_anchors_do_not_block_the_first_reasoning_board(prepared, monkeypatch):
    from threading import Event
    from test_prepared_lesson_wait import SOLUTION, ask
    _, module, _, _, _, _, _ = prepared
    entered, release = Event(), Event()
    def anchors(_image, target, *_args):
        entered.set()
        release.wait(3)
        return target
    def tutor(*_args):
        assert entered.wait(1)
        assert not release.is_set(), "The explanation still waited for word positions"
        yield "beat", {"speech": "先比较这两个部分的关系。", "step_id": 1, "step_end": True,
                       "page": [], "scratch": [{"kind": "text", "role": "prose", "text": "先找关系"}],
                       "reply_mode": "none", "teaching_move": "give_clue"}
        release.set()
    monkeypatch.setattr(module, "_teaching_anchors", anchors)
    monkeypatch.setattr(module, "_resolve_teaching_solution", lambda _s, _f, _i, item, *_a, **_k:
                        {**item, "solution": SOLUTION, "verification_status": "verified"})
    monkeypatch.setattr(module, "_refresh_teaching_solution", lambda _s, _f, _i, item, *_a, **_k: item)
    monkeypatch.setattr(module, "stream_teaching_beats", tutor)
    try:
        response = ask(prepared)
        assert response.status_code == 200 and 'event: done' in response.text
    finally:
        release.set()


def test_reference_photo_and_stream_require_the_active_child_device(prepared):
    from test_api import parent
    client, _, child, session, _, image, service = prepared
    service.frames["current-page"] = image
    base = f"/v1/sessions/{session}/page-prep/current-page"
    response = client.get(base + "/image", headers=child)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.content == image
    assert client.get(base + "/image", headers=parent(client, "another@example.com")).status_code == 403


def test_selected_planned_question_can_run_before_the_page_worker_finishes(prepared, monkeypatch):
    from threading import Event
    from test_prepared_lesson_wait import SOLUTION
    _, module, _, session, family, image, service = prepared
    service.inventory_jobs.add("current-page")
    completed, calls = Event(), []
    def solve(*_args, **_kwargs):
        calls.append(1)
        completed.set()
        return SOLUTION
    monkeypatch.setattr(page_prep, "solve_question", solve)
    item = service.teaching_question(session, family["id"], image, "current-question")
    service.prioritize(session, family["id"], item)
    assert completed.wait(2)
    service.foreground.shutdown(wait=True)
    item = service.teaching_question(session, family["id"], image, "current-question")
    assert item["verification_status"] == "verified"
    # The old background plan is allowed to reach this ordinal later, but it
    # must consume the completed result rather than start another model call.
    service._solve_one("current-page", session, family["id"], image,
                       module.preferences(module.settings.database_path, family["id"]),
                       module.settings, item, 0, "", "")
    assert calls == [1]
