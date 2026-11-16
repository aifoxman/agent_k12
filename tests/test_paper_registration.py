"""A local selection remains a proposal until actual page pixels agree."""

import io
import json
import hashlib
import random

import pytest
from PIL import Image, ImageDraw

from k12_server.paper_registration import IDENTITY, map_box, stroke_scores, valid_transform
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
