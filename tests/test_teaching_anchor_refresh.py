"""A geometry refresh shares one request budget and never loses known word anchors."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from test_api import make_client, pair, parent

TARGET = [0.1, 0.2, 0.7, 0.6]
WORD = {"id": "a1", "text": "23", "box": [0.2, 0.3, 0.06, 0.03], "confidence": 0.96}
SEGMENT = {
    "id": "a1",
    "kind": "segment",
    "text": "已印刷的底边",
    "x1": 0.3,
    "y1": 0.6,
    "x2": 0.6,
    "y2": 0.6,
    "confidence": 0.97,
}
POINTS = [
    {
        "id": "p1",
        "kind": "point",
        "text": "左顶点",
        "box": [0.3, 0.4, 0.02, 0.02],
        "confidence": 0.97,
    },
    {
        "id": "p2",
        "kind": "point",
        "text": "右顶点",
        "box": [0.5, 0.4, 0.02, 0.02],
        "confidence": 0.97,
    },
]
REGION = {
    "id": "r1",
    "kind": "region",
    "text": "左侧可见图块",
    "box": [0.3, 0.4, 0.18, 0.15],
    "confidence": 0.96,
}


@pytest.fixture
def module(monkeypatch, tmp_path):
    _, app_module = make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "preferences", lambda *_args: {"locator_model": "test-locator"})
    monkeypatch.setattr(
        app_module, "tight_teaching_anchor_image", lambda *_args: (b"local-crop", TARGET)
    )
    monkeypatch.setattr(
        app_module, "verify_teaching_anchors", lambda _image, anchors, *_args: anchors
    )
    return app_module


def located(evidence="图中长方形面积为23", **extra):
    return {
        "box": TARGET,
        "label": "当前题目",
        "evidence": evidence,
        "anchors": [deepcopy(WORD)],
        **extra,
    }


@pytest.mark.parametrize(
    "question,evidence,extra",
    [
        ("请讲清楚方法", "图中长方形面积为23", {}),
        ("请在原图连线说明", "已知数量23", {}),
        ("请解释题目", "已知数量23", {"diagram": {"elements": [{"kind": "rect"}]}}),
    ],
)
def test_missing_geometry_is_read_once_without_displacing_known_words(
    module, monkeypatch, question, evidence, extra
):
    calls = []
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [deepcopy(WORD), deepcopy(SEGMENT)],
    )
    original = located(evidence, **extra)
    state = {}
    first = module._teaching_anchors(b"same-photo", original, state, "family", question)
    second = module._teaching_anchors(b"same-photo", original, state, "family", question)
    assert len(calls) == 1
    assert calls[0][1] == {"geometry_needed": True}
    assert calls[0][0][1:4] == (b"local-crop", TARGET, TARGET)
    assert calls[0][0][4] == evidence  # The user's request is never OCR evidence.
    assert first == second
    assert original["anchors"] == [WORD]
    assert WORD in first["anchors"]
    assert len({anchor["id"] for anchor in first["anchors"]}) == len(first["anchors"])
    segment = next(anchor for anchor in first["anchors"] if anchor.get("kind") == "segment")
    assert segment["id"] != WORD["id"]
    assert {key: segment[key] for key in ("x1", "y1", "x2", "y2")} == {
        key: SEGMENT[key] for key in ("x1", "y1", "x2", "y2")
    }


@pytest.mark.parametrize("evidence,question,options", [
    ("图中积木需要搬移", "在原图圈出所用部分", {"region_needed": True}),
    ("图中积木需要搬移", "把两个顶点连线", {"geometry_needed": True, "region_needed": True}),
    ("已知长方形条件", "请解释图形关系", {"geometry_needed": True}),
])
def test_schematic_shape_does_not_force_precision_geometry_on_object_only_tasks(
        module, monkeypatch, evidence, question, options):
    calls = []
    monkeypatch.setattr(module, "read_teaching_anchors",
                        lambda *_args, **kwargs: calls.append(kwargs) or [])
    target = located(evidence, solution={"diagram": {"elements": [{"kind": "rect"},
                                                                    {"kind": "circle"}]}})
    state = {}
    module._teaching_anchors(b"photo", target, state, "family", question)
    module._teaching_anchors(b"photo", target, state, "family", question)
    assert calls == [options]


@pytest.mark.parametrize("anchors", [[WORD], [WORD, *POINTS], [WORD, {**SEGMENT, "id": "s1"}]])
def test_text_only_task_or_usable_geometry_needs_no_optional_read(module, monkeypatch, anchors):
    calls = []
    monkeypatch.setattr(
        module, "read_teaching_anchors", lambda *_args, **_kwargs: calls.append(1) or []
    )
    target = located("已知数量23", anchors=deepcopy(anchors))
    question = "请解释23的意思" if len(anchors) == 1 else "请在原图标出这条边"
    result = module._teaching_anchors(b"photo", target, {}, "family", question)
    assert calls == []
    assert WORD in result["anchors"]


def test_single_point_is_insufficient_for_requested_connection(module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *_args, **_kwargs: calls.append(1) or [deepcopy(POINTS[1])],
    )
    result = module._teaching_anchors(
        b"photo", located(anchors=[WORD, POINTS[0]]), {}, "family", "请连起两个顶点"
    )
    assert calls == [1]
    assert len([anchor for anchor in result["anchors"] if anchor.get("kind") == "point"]) == 2


def test_existing_verified_edge_does_not_skip_missing_visible_object_region(module, monkeypatch):
    edge = {**deepcopy(SEGMENT), "id": "s1"}
    reads = []
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *args, **kwargs: reads.append((args, kwargs)) or [deepcopy(REGION)],
    )
    target = located(anchors=[deepcopy(WORD), edge])
    state = {}
    result = module._teaching_anchors(
        b"same-photo", target, state, "family", "请圈出图中左侧可见的积木"
    )
    assert len(reads) == 1
    assert reads[0][1] == {"region_needed": True}
    assert reads[0][0][1:4] == (b"local-crop", TARGET, TARGET)
    assert WORD in result["anchors"] and edge in result["anchors"]
    assert REGION in result["anchors"]
    assert "x" not in next(anchor for anchor in result["anchors"] if anchor["id"] == "r1")
    assert (
        module._teaching_anchors(b"same-photo", target, state, "family", "请圈出图中左侧可见的积木")
        == result
    )
    assert len(reads) == 1


def test_visible_object_region_does_not_replace_geometry_needed_to_trace_a_real_edge(
    module, monkeypatch
):
    reads = []
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *args, **kwargs: reads.append((args, kwargs)) or [deepcopy(SEGMENT)],
    )
    result = module._teaching_anchors(
        b"same-photo", located(anchors=[WORD, REGION]), {}, "family", "请描出原图已有底边"
    )
    assert len(reads) == 1
    assert reads[0][1] == {"geometry_needed": True}
    assert REGION in result["anchors"]
    edges = [anchor for anchor in result["anchors"] if anchor.get("kind") == "segment"]
    assert len(edges) == 1
    assert {key: edges[0][key] for key in ("x1", "y1", "x2", "y2")} == {
        key: SEGMENT[key] for key in ("x1", "y1", "x2", "y2")
    }
    assert not any(anchor.get("kind") == "point" for anchor in result["anchors"])


@pytest.mark.parametrize("failure", [None, httpx.ReadTimeout("synthetic region timeout")])
def test_missing_object_region_still_uses_only_one_read_when_edge_is_already_valid(
    module, monkeypatch, failure
):
    edge = {**deepcopy(SEGMENT), "id": "s1"}
    target, state, reads = located(anchors=[WORD, edge]), {}, []

    def read(*_args, **kwargs):
        reads.append(kwargs)
        if failure:
            raise failure
        return []

    monkeypatch.setattr(module, "read_teaching_anchors", read)
    for _ in range(3):
        result = module._teaching_anchors(
            b"same-photo", target, state, "family", "请圈出图中突出的一块"
        )
        assert result["anchors"] == [WORD, edge]
    assert reads == [{"region_needed": True}]


def test_region_is_not_accepted_when_pixel_verification_is_unavailable(module, monkeypatch):
    reads = []

    def unavailable(*_args):
        raise OSError("synthetic pixel verification unavailable")

    monkeypatch.setattr(module, "verify_teaching_anchors", unavailable)
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *_args, **_kwargs: reads.append(1) or [deepcopy(REGION)],
    )
    result = module._teaching_anchors(
        b"same-photo", located(anchors=[WORD, REGION]), {}, "family", "请圈出图中这块积木"
    )
    assert result["anchors"] == [WORD]
    assert reads == [1]


@pytest.mark.parametrize(
    "failure", [None, httpx.ReadTimeout("synthetic timeout"), ValueError("unreadable")]
)
def test_failed_read_keeps_word_positions_and_spends_request_budget(module, monkeypatch, failure):
    calls = []

    def read(*_args, **_kwargs):
        calls.append(1)
        if failure:
            raise failure
        return []

    monkeypatch.setattr(module, "read_teaching_anchors", read)
    target, state = located(), {}
    for _ in range(3):
        assert module._teaching_anchors(b"photo", target, state, "family", "请描出原图边线")[
            "anchors"
        ] == [WORD]
    assert calls == [1]


def test_other_photo_or_other_target_cannot_reuse_refresh_coordinates(module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *_args, **_kwargs: calls.append(1) or [deepcopy(SEGMENT)],
    )
    state = {}
    first = module._teaching_anchors(b"photo-one", located(), state, "family", "请描出原图边线")
    assert any(anchor.get("kind") == "segment" for anchor in first["anchors"])
    assert module._teaching_anchors(b"photo-two", located(), state, "family", "请描出原图边线")[
        "anchors"
    ] == [WORD]
    assert module._teaching_anchors(
        b"photo-one", located("另一道三角形题23"), state, "family", "请描出原图边线"
    )["anchors"] == [WORD]
    assert calls == [1]


def test_a_refreshed_target_itself_is_bound_to_its_original_photo(module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *_args, **_kwargs: calls.append(1) or [deepcopy(SEGMENT)],
    )
    state = {}
    first = module._teaching_anchors(b"photo-one", located(), state, "family", "请描边")
    result = module._teaching_anchors(b"photo-two", first, state, "family", "请描边")
    assert result["anchors"] == []
    assert calls == [1]


def test_low_confidence_or_outside_geometry_is_discarded_without_guessing(module, monkeypatch):
    invalid = [dict(SEGMENT, confidence=0.6), dict(POINTS[0], box=[0.9, 0.9, 0.02, 0.02])]
    monkeypatch.setattr(module, "read_teaching_anchors", lambda *_args, **_kwargs: invalid)
    result = module._teaching_anchors(b"photo", located(), {}, "family", "请描原图")
    assert result["anchors"] == [WORD]


def test_existing_segment_requires_photo_support_before_it_can_skip_refresh(module, monkeypatch):
    supported = {**SEGMENT, "id": "visible-edge"}
    verification_calls = []

    def verify(image, anchors, box, evidence):
        verification_calls.append((image, deepcopy(anchors), box, evidence))
        return anchors

    monkeypatch.setattr(module, "verify_teaching_anchors", verify)
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *_args, **_kwargs: pytest.fail(
            "Verified current geometry needs no extra model read"
        ),
    )
    result = module._teaching_anchors(
        b"photo", located(anchors=[WORD, supported]), {}, "family", "请描边"
    )
    assert len(verification_calls) == 1
    assert verification_calls[0][0] == b"photo"
    assert verification_calls[0][2:] == (TARGET, "图中长方形面积为23")
    assert supported in result["anchors"]


def test_rejected_existing_segment_uses_tight_read_then_verifies_merged_output(module, monkeypatch):
    rejected = {**SEGMENT, "id": "unsupported-edge"}
    replacement = {**SEGMENT, "id": "supported-edge"}
    snapshots, reads, crops = [], [], []

    def verify(_image, anchors, *_args):
        snapshots.append(deepcopy(anchors))
        return [anchor for anchor in anchors if anchor["id"] != "unsupported-edge"]

    monkeypatch.setattr(module, "verify_teaching_anchors", verify)
    monkeypatch.setattr(
        module,
        "tight_teaching_anchor_image",
        lambda image, box: crops.append((image, box)) or (b"tight-image", TARGET),
    )
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *args, **kwargs: reads.append((args, kwargs)) or [replacement],
    )
    state = {}
    target = located(anchors=[WORD, rejected])
    result = module._teaching_anchors(b"photo", target, state, "family", "请描边")
    assert crops == [(b"photo", TARGET)]
    assert len(reads) == 1 and reads[0][0][1] == b"tight-image"
    assert reads[0][1] == {"geometry_needed": True}
    assert [anchor["id"] for anchor in snapshots[0]] == ["a1", "unsupported-edge"]
    assert any(anchor["id"] == "supported-edge" for anchor in snapshots[-1])
    assert result["anchors"] == [WORD, replacement]
    assert (
        module._teaching_anchors(b"photo", target, state, "family", "请描边")["anchors"]
        == result["anchors"]
    )
    assert len(reads) == 1


def test_rejected_word_is_reread_even_when_an_existing_segment_is_supported(module, monkeypatch):
    shifted_word = {**WORD, "box": [0.35, 0.35, 0.06, 0.03]}
    supported = {**SEGMENT, "id": "visible-edge"}
    reads = []

    def verify(_image, anchors, *_args):
        return [anchor for anchor in anchors if anchor.get("box") != shifted_word["box"]]

    monkeypatch.setattr(module, "verify_teaching_anchors", verify)
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *_args, **_kwargs: reads.append(1) or [deepcopy(WORD)],
    )
    state = {}
    target = located(anchors=[shifted_word, supported])
    for _ in range(2):
        result = module._teaching_anchors(b"photo", target, state, "family", "请讲清图形关系")
        assert WORD in result["anchors"]
        assert supported in result["anchors"]
    assert reads == [1]


def test_fresh_model_geometry_is_also_rejected_without_pixel_support(module, monkeypatch):
    snapshots, reads = [], []

    def verify(_image, anchors, *_args):
        snapshots.append(deepcopy(anchors))
        return [anchor for anchor in anchors if anchor.get("kind", "text") == "text"]

    monkeypatch.setattr(module, "verify_teaching_anchors", verify)
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *_args, **_kwargs: reads.append(1) or [deepcopy(SEGMENT)],
    )
    state = {}
    for _ in range(3):
        result = module._teaching_anchors(b"photo", located(), state, "family", "请描边")
        assert result["anchors"] == [WORD]
    assert reads == [1]
    assert any(any(anchor.get("kind") == "segment" for anchor in anchors) for anchors in snapshots)


def test_photo_verification_failure_never_licenses_geometry_but_keeps_words(module, monkeypatch):
    reads = []

    def unavailable(*_args):
        raise OSError("synthetic invalid image")

    monkeypatch.setattr(module, "verify_teaching_anchors", unavailable)
    monkeypatch.setattr(
        module,
        "read_teaching_anchors",
        lambda *_args, **_kwargs: reads.append(1) or [deepcopy(SEGMENT)],
    )
    result = module._teaching_anchors(
        b"photo", located(anchors=[WORD, {**SEGMENT, "id": "old-edge"}]), {}, "family", "请描边"
    )
    assert result["anchors"] == [WORD]
    assert reads == [1]


def test_failed_tight_crop_also_spends_the_single_request_budget(module, monkeypatch):
    crops, reads = [], []

    def unavailable(*_args):
        crops.append(1)
        raise ValueError("synthetic crop failure")

    monkeypatch.setattr(module, "tight_teaching_anchor_image", unavailable)
    monkeypatch.setattr(
        module, "read_teaching_anchors", lambda *_args, **_kwargs: reads.append(1) or []
    )
    state = {}
    for _ in range(3):
        assert module._teaching_anchors(b"photo", located(), state, "family", "请描边")[
            "anchors"
        ] == [WORD]
    assert crops == [1] and reads == []


def test_merged_ids_support_real_line_validation_and_duplicate_edges_are_not_added(
    module, monkeypatch
):
    from k12_server.teaching_ink import normalized_teaching_beat

    opposite = {**deepcopy(SEGMENT), "id": "reverse", "x1": SEGMENT["x2"], "x2": SEGMENT["x1"]}
    monkeypatch.setattr(
        module, "read_teaching_anchors", lambda *_args, **_kwargs: [deepcopy(SEGMENT), opposite]
    )
    target = module._teaching_anchors(b"photo", located(), {}, "family", "请描边")
    edges = [anchor for anchor in target["anchors"] if anchor.get("kind") == "segment"]
    assert len(edges) == 1
    beat = normalized_teaching_beat(
        {
            "speech": "先描出图中已有的这条底边。",
            "page": [{"kind": "line", "anchor_id": edges[0]["id"], "confidence": 0.97}],
            "scratch": [],
        },
        TARGET,
        TARGET,
        "99",
        target["anchors"],
        True,
    )
    assert beat["page"] == [
        {
            "kind": "line",
            "anchor_id": edges[0]["id"],
            **{key: SEGMENT[key] for key in ("x1", "y1", "x2", "y2")},
        }
    ]


def test_stream_to_ordinary_fallback_reuses_geometry_read_and_preserves_text(monkeypatch, tmp_path):
    client, app_module = make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module, "verify_teaching_anchors", lambda _image, anchors, *_args: anchors
    )
    parent_headers = parent(client)
    child_headers = pair(client, parent_headers)
    claims = app_module.read_token(
        app_module.settings.app_secret, child_headers["Authorization"].removeprefix("Bearer ")
    )
    ticket = app_module.make_token(app_module.settings.app_secret, "setup", claims["sub"], 120)
    session = client.post(
        "/v1/sessions", json={"setup_ticket": ticket}, headers=child_headers
    ).json()["id"]
    target = {
        **located(),
        "found": True,
        "id": "question",
        "read_confidence": 0.96,
        "verification_status": "verified",
        "solution": {
            "answer": "99",
            "steps": "按已知边关系比较",
            "confidence": 0.96,
            "ambiguous": False,
        },
    }
    monkeypatch.setattr(
        app_module,
        "page_preparation",
        lambda: SimpleNamespace(match=lambda *_a, **_k: deepcopy(target)),
    )
    monkeypatch.setattr(app_module, "locate_question", lambda *_a, **_k: deepcopy(target))
    reads, tutor_anchors = [], []
    monkeypatch.setattr(
        app_module,
        "read_teaching_anchors",
        lambda *_a, **_k: reads.append(1) or [deepcopy(SEGMENT)],
    )

    def empty_stream(*args):
        tutor_anchors.append(args[4]["anchors"])
        return iter(())

    monkeypatch.setattr(app_module, "stream_teaching_beats", empty_stream)
    monkeypatch.setattr(
        app_module,
        "analyze",
        lambda *_a, **_k: {
            "response": "先观察这条已有的底边。",
            "needs_parent": False,
            "visual_cue": None,
        },
    )
    image = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(image, format="JPEG")
    response = client.post(
        "/v1/hints/stream",
        data={"session_id": session, "question": "请在原图描出边线"},
        files={"image": ("page.jpg", image.getvalue(), "image/jpeg")},
        headers=child_headers,
    )
    assert response.status_code == 200 and "event: done" in response.text
    assert reads == [1]
    assert tutor_anchors and all(WORD in anchors for anchors in tutor_anchors)
    assert all(
        any(anchor.get("kind") == "segment" for anchor in anchors) for anchors in tutor_anchors
    )
    done = next(
        json.loads(frame.split("data: ", 1)[1])
        for frame in response.text.split("\n\n")
        if frame.startswith("event: done\n")
    )
    assert done["located"] is True


@pytest.mark.parametrize(
    "question,thought,overrides,allowed",
    [
        ("请完整讲解这个方法和解题过程", "", {}, True),
        ("请从头到尾解释", "", {"verification_status": "rechecked"}, True),
        ("请完整讲解，但先不要报最终答案", "", {}, False),
        ("请完整讲解", "先不要告诉我结果", {}, False),
        ("请讲一种方法，解释为什么这样做", "", {}, False),
        ("请完整讲解", "", {"read_confidence": 0.6}, False),
        ("请完整讲解", "", {"verification_status": "uncertain"}, False),
        ("请完整讲解", "", {"solution_confidence": 0.5}, False),
        ("请完整讲解", "", {"quoted_conflict": True}, True),
    ],
)
def test_ordinary_hint_full_conclusion_requires_explicit_goal_and_checked_solution(
    monkeypatch,
    tmp_path,
    question,
    thought,
    overrides,
    allowed,
):
    client, app_module = make_client(monkeypatch, tmp_path)
    parent_headers = parent(client)
    child_headers = pair(client, parent_headers)
    claims = app_module.read_token(
        app_module.settings.app_secret, child_headers["Authorization"].removeprefix("Bearer ")
    )
    ticket = app_module.make_token(app_module.settings.app_secret, "setup", claims["sub"], 120)
    session = client.post(
        "/v1/sessions", json={"setup_ticket": ticket}, headers=child_headers
    ).json()["id"]
    target = {
        **located("已知数量23"),
        "found": True,
        "id": "question",
        "read_confidence": 0.96,
        "verification_status": "verified",
        "solution": {
            "answer": "99",
            "steps": "先比较已知条件，再计算，最后代回检查",
            "hint_seed": "先比较已知条件",
            "confidence": overrides.get("solution_confidence", 0.96),
            "ambiguous": False,
        },
        **{
            key: value
            for key, value in overrides.items()
            if key not in {"solution_confidence", "quoted_conflict"}
        },
    }
    monkeypatch.setattr(
        app_module,
        "page_preparation",
        lambda: SimpleNamespace(match=lambda *_a, **_k: deepcopy(target)),
    )
    monkeypatch.setattr(app_module, "_answer_feedback", lambda *_args: None)
    calls, rewrites = [], []
    solves = []

    def unresolved_solve(*_args):
        # An untrusted cache now gets one on-demand check. Keep that check
        # explicitly unresolved instead of letting this fixture call a provider.
        solves.append(1)
        return {
            "answer": "99",
            "steps": "尚需核对条件与计算的对应关系",
            "confidence": 0.5,
            "ambiguous": True,
        }

    monkeypatch.setattr(app_module, "solve_question", unresolved_solve)

    def analyze(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "response": "条件对应后依次计算，答案是99。",
            "needs_parent": False,
            "visual_cue": None,
        }

    monkeypatch.setattr(app_module, "analyze", analyze)
    monkeypatch.setattr(
        app_module, "quoted_target_conflict", lambda *_args: overrides.get("quoted_conflict", False)
    )
    monkeypatch.setattr(
        app_module,
        "teach_from_evidence",
        lambda *_a, **kwargs: (
            rewrites.append(kwargs)
            or (
                "完整说明条件和计算，答案是99。"
                if allowed
                else "先说明这个条件为什么能用，再自己完成计算。"
            )
        ),
    )
    image = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(image, format="JPEG")
    response = client.post(
        "/v1/hints",
        data={"session_id": session, "question": question, "student_thought": thought},
        files={"image": ("page.jpg", image.getvalue(), "image/jpeg")},
        headers=child_headers,
    )
    assert response.status_code == 200, response.text
    assert solves == (
        [1]
        if overrides.get("verification_status") == "uncertain"
        or overrides.get("solution_confidence", 1) < 0.85
        else []
    )
    assert ("答案是99" in response.json()["response"]) is allowed
    assert calls[0][1].get("allow_final_answer", False) is allowed
    assert rewrites == (
        [{"allow_final_answer": True}]
        if overrides.get("quoted_conflict")
        else []
        if allowed
        else [{}]
    )
    assert "只给一步提示，绝不向孩子泄露完整解答" not in calls[0][0][2]
    if allowed:
        assert target["solution"]["steps"] in calls[0][0][2]
        assert "教师已核对的最终结果：99" in calls[0][0][2]
