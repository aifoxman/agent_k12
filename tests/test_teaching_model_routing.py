"""Lesson complexity selects an existing family role in every hint transport."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image
from test_api import make_client, pair, parent

GOALS = [
    ("请完整讲解这道题的步骤和检查方法", "", "solver_model"),
    ("解释这个方法为什么这样做", "", "solver_model"),
    ("请完整讲解，但不要报最终答案", "", "solver_model"),
    ("请只给一步提示", "", "tutor_model"),
    ("请完整讲解", "我先自己试一试", "tutor_model"),
]
FAMILY_MODELS = {
    "solver_model": "family-selected-reasoning-model",
    "tutor_model": "family-selected-tutoring-model",
    "locator_model": "family-selected-vision-model",
}


@pytest.fixture
def api(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    child = pair(client, parent(client))
    claims = module.read_token(
        module.settings.app_secret, child["Authorization"].removeprefix("Bearer ")
    )
    ticket = module.make_token(module.settings.app_secret, "setup", claims["sub"], 120)
    session = client.post("/v1/sessions", json={"setup_ticket": ticket}, headers=child).json()["id"]
    image = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(image, format="JPEG")
    target = {
        "found": True,
        "label": "第1题",
        "box": [.2, .2, .5, .4],
        "evidence": "观察数量关系并说明理由",
        "confidence": .96,
        "read_confidence": .96,
        "verification_status": "verified",
        "solution": {
            "answer": "99", "steps": "比较条件再计算，最后回代检查",
            "confidence": .96, "ambiguous": False,
        },
    }
    monkeypatch.setattr(module, "preferences", lambda *_args: dict(FAMILY_MODELS))
    monkeypatch.setattr(module, "_teaching_anchors", lambda _image, value, *_args: value)
    monkeypatch.setattr(module, "_answer_feedback", lambda *_args: None)
    monkeypatch.setattr(module, "locate_question", lambda *_args, **_kwargs: deepcopy(target))
    return client, module, child, session, image.getvalue(), target


@pytest.mark.parametrize("question,thought,role", GOALS)
@pytest.mark.parametrize("transport", [
    "cached-stream", "uncached-stream", "stream-to-ordinary", "cached-ordinary", "uncached-ordinary",
])
def test_each_hint_path_uses_the_family_role_for_this_teaching_goal(
    api, monkeypatch, question, thought, role, transport,
):
    client, module, child, session, image, target = api
    cached = transport in {"cached-stream", "stream-to-ordinary", "cached-ordinary"}
    if cached:
        target["id"] = "prepared-question"
    monkeypatch.setattr(module, "page_preparation", lambda: SimpleNamespace(
        match=lambda *_args, **_kwargs: deepcopy(target) if cached else None,
    ))
    # Exercise the ungrounded stream branch as well as the verified cached branch.
    monkeypatch.setattr(module, "solve_question", lambda *_args: {
        "answer": "", "steps": "", "confidence": 0,
    })
    calls = []

    def stream(config, *_args, **_kwargs):
        calls.append(("stream", config.ai_model))
        if transport == "stream-to-ordinary":
            return iter(())
        return iter([("beat", {
            "speech": "先观察条件，再说明当前关系。", "page": [], "scratch": [],
            "teaching_move": "give_clue", "reply_mode": "none", "needs_parent": False,
        })])

    def analyze(config, *_args, **_kwargs):
        calls.append(("ordinary", config.ai_model))
        return {"response": "先观察条件，再说明当前关系。", "visual_cue": None,
                "teaching_move": "give_clue", "reply_mode": "none", "needs_parent": False}

    monkeypatch.setattr(module, "stream_teaching_beats", stream)
    monkeypatch.setattr(module, "analyze", analyze)
    streamed = transport.endswith("stream") or transport == "stream-to-ordinary"
    response = client.post(
        "/v1/hints/stream" if streamed else "/v1/hints",
        data={"session_id": session, "question": question, "student_thought": thought},
        files={"image": ("page.jpg", image, "image/jpeg")}, headers=child,
    )
    assert response.status_code == 200, response.text
    if streamed:
        result = next(json.loads(frame.split("data: ", 1)[1])
                      for frame in response.text.split("\n\n")
                      if frame.startswith("event: done\n"))
    else:
        result = response.json()
    assert result["located"] is True
    assert calls and all(model == FAMILY_MODELS[role] for _, model in calls)
    expected_calls = (["stream", "stream", "ordinary"] if transport == "stream-to-ordinary"
                      else ["stream" if streamed else "ordinary"])
    assert [kind for kind, _ in calls] == expected_calls


@pytest.mark.parametrize("question,thought,role", GOALS)
def test_routing_uses_selected_role_without_mutating_family_configuration(api, question, thought, role):
    module = api[1]
    config = dict(FAMILY_MODELS)
    assert module._teaching_model(config, question, thought) == config[role]
    assert config == FAMILY_MODELS
