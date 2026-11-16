"""Recovering a current target must preserve structured, complete teaching delivery."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from test_api import make_client, pair, parent


@pytest.fixture
def api(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    child = pair(client, parent(client))
    child_id = module.read_token(
        module.settings.app_secret, child["Authorization"].removeprefix("Bearer ")
    )["sub"]
    ticket = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    session = client.post("/v1/sessions", json={"setup_ticket": ticket}, headers=child).json()["id"]
    image = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(image, format="JPEG")
    target = {"found": True, "label": "第1题", "box": [.2, .2, .5, .4],
              "evidence": "观察两个部分并说明理由", "confidence": .96, "anchors": []}
    monkeypatch.setattr(module, "page_preparation", lambda: SimpleNamespace(
        match=lambda *_a, **_k: None, status=lambda *_a, **_k: None,
    ))
    monkeypatch.setattr(module, "_teaching_anchors", lambda _image, value, *_args: value)
    monkeypatch.setattr(module, "solve_question", lambda *_args: {
        "answer": "99", "steps": "比较条件再计算并检查", "confidence": .96, "ambiguous": False,
    })
    return client, module, child, session, image.getvalue(), target


def ask(api, *, stream=True, **data):
    client, _, child, session, image, _ = api
    response = client.post("/v1/hints/stream" if stream else "/v1/hints",
                           data={"session_id": session, "question": "请完整讲解这个方法", **data},
                           files={"image": ("page.jpg", image, "image/jpeg")}, headers=child)
    assert response.status_code == 200, response.text
    if not stream:
        return response.json(), []
    events = [(frame.split("\n", 1)[0].removeprefix("event: "),
               json.loads(frame.split("data: ", 1)[1]))
              for frame in response.text.split("\n\n") if frame.startswith("event: ")]
    done = [data for kind, data in events if kind == "done"]
    assert done, events
    return done[-1], events


@pytest.mark.parametrize("initial", ["not-found", "error"])
def test_second_location_recovery_returns_to_the_structured_stream(api, monkeypatch, initial):
    module, target = api[1], api[5]
    locations, streams = [], []

    def locate(*_args, **_kwargs):
        locations.append(1)
        if len(locations) == 1:
            if initial == "error":
                raise HTTPException(502, "location unavailable")
            return {"found": False}
        return deepcopy(target)

    def stream(*args):
        streams.append(args[4])
        return iter([("beat", {"speech": "因为条件对应，所以先比较这两个部分。", "page": [],
                               "scratch": [], "reply_mode": "none", "teaching_move": "give_clue"})])

    monkeypatch.setattr(module, "locate_question", locate)
    monkeypatch.setattr(module, "stream_teaching_beats", stream)
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: pytest.fail("recovery bypassed structured teaching"))
    result, events = ask(api)
    assert len(locations) == 2 and len(streams) == 1
    assert [kind for kind, _ in events] == ["status", "target", "beat", "done"]
    assert result["target_box"] == target["box"]
    assert streams[0]["evidence"] == target["evidence"]


def test_failed_recovery_clarifies_without_an_ordinary_third_location(api, monkeypatch):
    module, locations = api[1], []
    monkeypatch.setattr(module, "locate_question", lambda *_a, **_k: locations.append(1) or {"found": False})
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: pytest.fail("unlocated target taught"))
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: pytest.fail("unlocated target streamed"))
    result, events = ask(api)
    assert len(locations) == 2
    assert result["located"] is False
    assert [kind for kind, _ in events] == ["status", "done"]


def test_selected_crop_keeps_visual_beats_and_same_image_second_tier(api, monkeypatch):
    module, target = api[1], api[5]
    locations = []
    monkeypatch.setattr(module, "locate_question", lambda *_a, **_k: locations.append(_a) or deepcopy(target))
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: pytest.fail("selected crop fell into a plain-text response"))
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: iter([("beat", {
        "speech": "先把两个条件对应起来，因为它们说明同一个关系。",
        "page": [], "scratch": [{"kind": "text", "role": "prose", "x": 4, "y": 4, "text": "对应已知条件"}],
        "step_id": 1, "step_end": True, "reply_mode": "none", "teaching_move": "give_clue",
    })]))
    first, frames = ask(api, selected_region="true", level=1,
                        question="请读题并解释方法，先不公布最终答案。")
    assert first["target_box"] == target["box"] and first["visual_beats"]
    assert any(kind == "target" for kind, _ in frames)
    assert any(kind == "beat" for kind, _ in frames)
    assert first["continuation_available"] is True
    assert locations[0][4] is False
    assert "关联小问一起核对" in locations[0][2]
    monkeypatch.setattr(module, "locate_question", lambda *_a, **_k: pytest.fail("continuation relocated the original crop"))
    second, _ = ask(api, previous_hint_id=first["id"], level=2,
                    question="请把这道题完整解答，包括答案与检查。")
    assert second["target_box"] == first["target_box"]
    assert second["delivery_complete"] is True
    assert len(locations) == 1


def test_unconfirmed_crop_does_not_bypass_location_or_invent_teaching(api, monkeypatch):
    module, locations = api[1], []
    monkeypatch.setattr(module, "locate_question", lambda *_a, **_k: locations.append(1) or {"found": False})
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: pytest.fail("unconfirmed crop was taught"))
    result, _ = ask(api, selected_region="true")
    assert result["located"] is False
    assert len(locations) == 2


@pytest.mark.parametrize("stream", [True, False])
def test_literal_disagreement_asks_about_the_glyph_without_relocating_or_solving(api, monkeypatch, stream):
    module, locations = api[1], []
    monkeypatch.setattr(module, "locate_question", lambda *_a, **_k: locations.append(1) or
                        {"found": False, "issue": "critical_literal_disagreement"})
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: pytest.fail("Disputed glyph cannot be taught"))
    result, _ = ask(api, selected_region="true", stream=stream)
    assert result["located"] is False and result["delivery_complete"] is False
    assert result["location_issue"] == "critical_literal_disagreement"
    assert "关键字" in result["response"] and "题号" not in result["response"]
    assert len(locations) == 1


def test_pen_recovery_keeps_the_same_marks_and_does_not_bypass_a_failed_target(api, monkeypatch):
    module, locations = api[1], []
    marks = [[[.2, .3], [.5, .3]]]

    def locate(*args):
        locations.append(args)
        return {"found": False}

    monkeypatch.setattr(module, "locate_question", locate)
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: pytest.fail("pen constraint bypassed"))
    result, _ = ask(api, selected_strokes=json.dumps(marks))
    assert len(locations) == 2 and result["located"] is False
    assert all(args[4] is False and args[5] == marks for args in locations)


@pytest.mark.parametrize("stream", [True, False])
def test_long_ordinary_fallback_keeps_coherent_reasons_and_the_saved_ending(
    api, monkeypatch, stream,
):
    client, module, child, _, _, target = api
    locations, stream_calls = [], []
    monkeypatch.setattr(module, "locate_question", lambda *_a, **_k: locations.append(1) or deepcopy(target))
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: stream_calls.append(1) or iter(()))
    text = "因为这两个部分的条件相同，所以先比较它们对应的关系。" * 23 + "最后回代检查两处条件。请写下这一步，再自行检查。"
    assert 500 < len(text) < 800
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: {
        "response": text, "visual_cue": None, "teaching_move": "wait_for_writing",
        "reply_mode": "writing", "needs_parent": False,
    })
    result, events = ask(api, stream=stream)
    beats = result["visual_beats"]
    assert len(locations) == 1  # An already resolved same-frame target is reused.
    assert len(stream_calls) == (2 if stream else 0)
    assert 1 < len(beats) <= 48 and max(len(beat["speech"]) for beat in beats) <= 150
    assert any(len(beat["speech"]) >= 60 for beat in beats)
    assert all(beat["speech"].count("因为") == beat["speech"].count("所以") for beat in beats)
    assert "".join(beat["speech"] for beat in beats) == result["response"]
    assert result["response"].endswith("请写下这一步，再自行检查。")
    assert all(beat["reply_mode"] == "none" for beat in beats[:-1])
    assert beats[-1]["reply_mode"] == "writing"
    assert sum(bool(beat["scratch"]) for beat in beats) > 1
    if stream:
        assert [data for kind, data in events if kind == "beat"] == beats
    dashboard = client.get("/v1/families/dashboard", headers=child).json()
    saved = next(hint["teaching_scene"] for hint in dashboard["hints"] if hint["id"] == result["id"])
    assert saved["beats"] == beats
    assert "".join(beat["speech"] for beat in saved["beats"]) == result["response"]


@pytest.mark.parametrize("word,speech,expected", [
    ("12", "十二表示这部分的数量。", "十二"),
    ("12", "一百一十二表示这部分的数量。", None),
    ("甲", "由表示这个条件里的关系。", None),
])
def test_ordinary_cue_keeps_numeric_identity_and_actual_spoken_span(
    api, monkeypatch, word, speech, expected,
):
    module, target = api[1], api[5]
    target["anchors"] = [{"id": "condition", "text": word,
                           "box": [.3, .3, .1, .05], "confidence": .99}]
    monkeypatch.setattr(module, "locate_question", lambda *_a, **_k: deepcopy(target))
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: {
        "response": speech, "needs_parent": False,
        "visual_cue": {"kind": "circle", "anchor_id": "condition"},
    })
    result, _ = ask(api, stream=False)
    if expected is None:
        assert result["visual_cue"] is None
        assert all(not beat["page"] for beat in result["visual_beats"])
    else:
        assert result["visual_cue"]["text"] == word
        assert result["visual_cue"]["anchor_text"] == expected
        assert [mark for beat in result["visual_beats"] for mark in beat["page"]] == [result["visual_cue"]]
