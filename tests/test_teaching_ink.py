from __future__ import annotations

import json
from types import SimpleNamespace

from k12_server import teaching_ink
from k12_server.teaching_ink import (
    fallback_teaching_beat,
    grounded_teaching_solution,
    normalized_teaching_beat,
    stream_teaching_beats,
)

FOCUS = [.2, .1, .6, .5]
TARGET = [.44, .3, .2, .12]


def test_fallback_hint_keeps_a_located_mark_and_readable_draft():
    beat = fallback_teaching_beat("我先按第4题来理解：先看相同的边，再画一画。", TARGET)
    assert beat["page"] == []
    assert beat["scratch"][0]["role"] == "prose"
    assert "先看相同的边" in beat["scratch"][0]["text"]
    assert beat["speech"].startswith("我先按第4题")


def test_fallback_does_not_invent_a_board_for_target_clarification_or_only_waiting():
    for speech in ["请读出题目开头几个字？", "请你先自己试试。", "我还不能确定这道题的条件。",
                   "现在请你对着原图，自己解释这个关系。"]:
        beat = fallback_teaching_beat(speech, TARGET)
        assert beat["scratch"] == []
        assert beat["page"] == []


def test_plain_text_generator_keeps_the_current_step_as_a_board():
    result = list(stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", FOCUS,
        {"label": "题目", "evidence": "比较同单位的量", "box": TARGET, "read_confidence": .96},
        {"answer": "未知结果", "steps": "先比较单位"}, "三年级", 1,
        model_stream=lambda *_args: iter(["先把单位统一，再比较大小。你能先找出单位吗？"])))
    assert result[0][1]["scratch"][0]["role"] == "prose"
    assert "先把单位统一" in result[0][1]["scratch"][0]["text"]
    assert result[-1][1]["scratch"] == []


def test_open_ended_drawing_solution_can_teach_without_guessing_unreadable_paper():
    example = {"answer": "可拼成正方形，也可拼成长方形；画一种即可。",
               "steps": "先比较叶片的边长，再考虑相同边如何贴合。",
               "confidence": .94, "ambiguous": True}
    assert grounded_teaching_solution(example, .78)
    assert not grounded_teaching_solution(example, .65)
    assert not grounded_teaching_solution({**example,
        "answer": "暂不能可靠确定答案，需要更清晰的局部图。"}, .83)


def test_teaching_beat_maps_grounded_marks_and_keeps_safe_scratch():
    beat = normalized_teaching_beat({
        "speech": "先看题目中的两个已知量。",
        "page": [
            {"kind": "underline", "box": [.5, .5, .1, .05], "confidence": .93},
            {"kind": "circle", "box": [.05, .05, .1, .05], "confidence": .99},
            {"kind": "arrow", "box": [.5, .5, .1, .05], "confidence": .4},
        ],
        "scratch": [
            {"kind": "text", "x": 15, "y": 25, "text": "已知量 → 关系"},
            {"kind": "line", "x1": 10, "y1": 30, "x2": 80, "y2": 30},
            {"kind": "text", "x": 10, "y": 120, "text": "画布外"},
        ],
    }, FOCUS, TARGET, "42")
    assert beat["page"] == [{"kind": "underline", "box": [.5, .35, .06, .025]}]
    assert [item["kind"] for item in beat["scratch"]] == ["text", "line"]


def test_teaching_beat_places_named_points_on_the_full_page():
    beat = normalized_teaching_beat({
        "speech": "把上方这个顶点记作A。",
        "page": [
            {"kind": "circle", "space": "page", "box": [.5, .34, .03, .03],
             "confidence": .96},
            {"kind": "label", "space": "page", "box": [.5, .34, .03, .03],
             "text": "A", "confidence": .96},
            {"kind": "label", "space": "page", "box": [.5, .34, .03, .03],
             "text": "42", "confidence": .96},
        ],
    }, FOCUS, TARGET, "42")
    assert beat["page"] == [
        {"kind": "circle", "box": [.5, .34, .03, .03]},
        {"kind": "label", "box": [.5, .34, .03, .03], "text": "A"},
    ]


def test_teaching_beat_rejects_final_answer_in_voice_or_drawing():
    base = {"speech": "先写出中间关系。", "scratch": [
        {"kind": "text", "x": 20, "y": 20, "text": "2＋3＝？"}]}
    assert normalized_teaching_beat(base, FOCUS, TARGET, "5")
    assert normalized_teaching_beat({**base, "speech": "所以答案是5。"}, FOCUS, TARGET, "5") is None
    assert normalized_teaching_beat({**base, "scratch": [
        {"kind": "text", "x": 20, "y": 20, "text": "2＋3＝5"}]}, FOCUS, TARGET, "5") is None


def test_teaching_beats_arrive_as_model_lines_without_waiting_for_entire_response(monkeypatch):
    first = {"speech": "圈出已知条件。", "page": [], "scratch": []}
    second = {"speech": "把关系画在草稿里。", "page": [], "scratch": [
        {"kind": "line", "x1": 10, "y1": 20, "x2": 70, "y2": 20}]}
    chunks = [json.dumps(first, ensure_ascii=False) + "\n" + json.dumps(second, ensure_ascii=False)[:8],
              json.dumps(second, ensure_ascii=False)[8:] + "\n"]
    monkeypatch.setattr(teaching_ink, "stream_model", lambda *_args: iter(chunks))
    target = {"label": "第2题", "evidence": "已知两个数求和", "box": TARGET}
    result = list(stream_teaching_beats(SimpleNamespace(ai_model="tutor"),
                                        b"page", b"crop", FOCUS, target,
                                        {"answer": "42", "steps": "先列关系"}, "三年级", 1))
    assert [kind for kind, _ in result] == ["beat", "beat"]
    assert result[1][1]["scratch"][0]["kind"] == "line"
