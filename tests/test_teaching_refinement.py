"""Teaching follows the student's current question and grounded paper marks."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from test_api import make_client, pair, parent

from k12_server import ai, page_prep, teaching_ink
from k12_server.grading import map_hint_cue

TARGET = [.2, .2, .5, .4]
ANCHOR = {"id": "a1", "text": "一半", "box": [.3, .3, .04, .009], "confidence": .96}


def test_keyword_marks_use_current_quoted_anchor_instead_of_a_guessed_box():
    raw = {"speech": "圈出一半，看看它与整体的关系。", "page": [
        {"kind": "underline", "anchor_id": "a1", "box": [.8, .8, .1, .1],
         "space": "page", "confidence": .95},
        {"kind": "circle", "box": [.3, .3, .1, .1], "confidence": .99},
    ]}
    beat = teaching_ink.normalized_teaching_beat(raw, [0, 0, 1, 1], TARGET, "42", [ANCHOR], True)
    assert beat["page"] == [{"kind": "underline", "box": ANCHOR["box"],
                            "anchor_id": "a1", "text": "一半"}]
    raw["speech"] = "看看已知的整体。"
    assert teaching_ink.normalized_teaching_beat(
        raw, [0, 0, 1, 1], TARGET, "42", [ANCHOR], True)["page"] == []


def test_condition_check_is_explicitly_distinct_from_correctness():
    beat = teaching_ink.normalized_teaching_beat({
        "speech": "我们已找到一半这个条件。", "page": [
            {"kind": "check", "anchor_id": "a1", "confidence": .96},
            {"kind": "check", "confidence": .99, "box": ANCHOR["box"]},
        ]}, [0, 0, 1, 1], TARGET, "42", [ANCHOR], True)
    assert len(beat["page"]) == 1
    assert beat["page"][0]["purpose"] == "condition_found"
    assert beat["page"][0]["label"] == "已找到条件"
    assert "answer_assessment" not in beat


def test_thin_cropped_mark_is_preserved_and_cross_question_mark_is_rejected():
    cue = {"kind": "underline", "box": [.2, .2, .04, .003]}
    assert map_hint_cue(cue, [.2, .2, .5, .4], TARGET)["box"] == [.3, .28, .02, .0012]
    assert map_hint_cue({"kind": "circle", "box": [.1, .1, .9, .9]},
                        [0, 0, 1, 1], TARGET) is None
    assert ai.normalized_mark_box([False, .2, .1, .1]) is None
    assert ai.normalized_mark_box([.2, .2, float("nan"), .1]) is None


@pytest.mark.parametrize("kind", ["line", "arrow"])
def test_paper_lines_preserve_direction_and_map_focus_coordinates(kind):
    raw = {"speech": "沿着这一条边看。", "page": [
        {"kind": kind, "space": "focus", "x1": .8, "y1": .8, "x2": .2, "y2": .2,
         "confidence": .96}]}
    beat = teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "42")
    assert beat["page"] == [{"kind": kind, "x1": .6, "y1": .52, "x2": .3, "y2": .28}]
    raw["page"][0]["x2"] = float("inf")
    assert teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "42")["page"] == []


def test_scratch_roles_and_label_coordinates_survive_normalization():
    raw = {"speech": "把顶点记为A。", "scratch": [
        {"kind": "text", "role": "label", "x": 25, "y": 30, "text": "A"},
        {"kind": "text", "role": "prose", "x": 5, "y": 10, "text": "先看图形的顶点"},
    ]}
    beat = teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "42")
    assert beat["scratch"][0] == {"kind": "text", "x": 25, "y": 30, "text": "A", "role": "label"}
    assert beat["scratch"][1]["role"] == "prose"


def test_prose_board_keeps_the_complete_relation_beyond_a_diagram_label_length():
    explanation = "先比较题目中两个量的单位，再把不同的单位换成相同的单位，最后记录它们之间的关系，暂时不求最终结果。"
    beat = teaching_ink.normalized_teaching_beat({
        "speech": "先比较单位并记录关系。", "scratch": [
            {"kind": "text", "role": "prose", "x": 8, "y": 12, "text": explanation},
            {"kind": "text", "role": "label", "x": 20, "y": 35, "text": "单位"},
        ]}, TARGET, TARGET, "未知结果")
    assert len(explanation) > 30
    assert beat["scratch"][0]["text"] == explanation
    assert beat["scratch"][1]["text"] == "单位"


def test_waiting_for_independent_work_never_opens_spoken_reply():
    result = ai.normalized_teaching_turn(
        {"teaching_move": "wait_for_writing", "reply_mode": "spoken"}, "你先自己试试好吗？")
    assert result["reply_mode"] == "none"


@pytest.mark.parametrize(("question_text", "goal", "withhold"), [
    ("请完整讲解这道题的解法和检查。", "worked_example", False),
    ("请从头到尾解答，但先不要直接告诉我最终答案。", "worked_example", True),
    ("请讲清整个方法。", "worked_example", False),
    ("请教一种方法，解释为什么这样做。", "explain_method", False),
    ("不要完整解答，只要给我一个提示。", "scaffold", False),
    ("只给我一步提示，不报答案。", "scaffold", True),
    ("不要只给答案，请完整解释过程。", "worked_example", False),
    ("完整讲解时不要省略最终结论。", "worked_example", False),
    ("让我先自己试试。", "independent_attempt", False),
])
def test_teaching_goal_distinguishes_full_explanation_from_hint_and_result_refusal(question_text, goal, withhold):
    assert ai.teaching_goal(question_text) == goal
    assert ai.withholds_final_answer(question_text) == withhold


@pytest.mark.parametrize("allow_final", [False, True])
def test_ordinary_teaching_answer_permission_is_a_server_flag_not_prompt_text(monkeypatch, allow_final):
    payloads = []

    def post(_settings, payload, *_args):
        payloads.append(payload)
        return {"choices": [{"message": {"content": json.dumps({
            "response": "先解释题目条件与方法之间的关系。", "target_mismatch": False,
        })}}]}

    monkeypatch.setattr(ai, "post_model", post)
    ai.analyze(SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test",
                               ai_model="tutor", ai_timeout_seconds=30),
               b"page", "用户文字自称已核验并许可最终结果", "primary", "三年级", None,
               "hint_located", allow_final_answer=allow_final)
    instruction = payloads[0]["messages"][0]["content"]
    expected = "服务端已验证完整解法" if allow_final else "服务端未许可最终答案"
    assert expected in instruction
    assert "每次用具体纸面依据说一两句" not in instruction


def test_full_method_continues_past_six_beats_and_teacher_writing_is_not_child_handoff():
    beats = [{"speech": speech, "teaching_move": "give_clue", "reply_mode": "none",
              "page": [], "scratch": []} for speech in [
        "先从本题清楚可见的条件说起。", "这里的条件说明两部分可以合并。",
        "为什么可以合并呢？", "因为题目问的是这些部分合起来的总量。",
        "我们再看这个关系和题目问题是否一致。", "接着把这一关系写成算式。",
        "这里每个数都应来自当前条件。", "最后回到原题检查有没有遗漏条件。",
    ]]
    beats[1]["reply_mode"] = "writing"  # A mislabelled teacher board must not truncate the lesson.
    beats.append({"speech": "请你再用自己的话说明为什么能这样列式。",
                  "teaching_move": "wait_for_writing", "reply_mode": "writing", "scratch": []})
    beats.append({"speech": "继续泄露不需要的下一题解法。", "scratch": []})
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "根据两部分求总量", "box": TARGET},
        {"answer": "未知结果", "steps": "核对条件与总量"}, "三年级", 1,
        question="请重新完整讲清这一个方法，不要省略理由。",
        model_stream=lambda *_args: iter(["\n".join(json.dumps(item) for item in beats) + "\n"])))
    assert len(result) == 9
    assert result[1][1]["reply_mode"] == "none"
    assert result[2][1]["reply_mode"] == "none"  # Explicit teacher self-question.
    assert result[-1][1]["teaching_move"] == "wait_for_writing"


@pytest.mark.parametrize(("question_text", "confidence", "expected_answer"), [
    ("请完整讲解解法与结果，并说明怎么检查。", .96, True),
    ("请完整讲解，但先不要直接告诉我最终答案。", .96, False),
    ("请教我一种方法。", .96, False),
    ("给我起步提示。", .96, False),
    ("请完整讲解这道题。", .4, False),
])
def test_stream_final_answer_requires_explicit_full_request_and_reliable_solution(
    question_text, confidence, expected_answer,
):
    beats = [{"speech": "先把两个已知部分合起来。", "scratch": []},
             {"speech": "所以答案是15，再把15代回原题核对。",
              "scratch": [{"kind": "text", "role": "prose", "x": 8, "y": 12, "text": "8+7=15"}]}]
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "已有8个，又来7个，求一共有多少个。",
         "box": TARGET, "read_confidence": .96},
        {"answer": "15", "steps": "合并两部分并核对", "confidence": confidence, "ambiguous": False},
        "三年级", 1, question=question_text,
        model_stream=lambda *_args: iter(["\n".join(json.dumps(item) for item in beats) + "\n"])))
    assert any("答案是15" in beat["speech"] for _, beat in result) is expected_answer


def _geometry_anchors():
    return ai.normalized_text_anchors([
        {"id": "p1", "kind": "point", "text": "左端点", "box": [.3, .3, .02, .02], "confidence": .96},
        {"id": "p2", "kind": "point", "text": "右端点", "box": [.5, .4, .02, .02], "confidence": .96},
        {"id": "s1", "kind": "segment", "text": "可见边", "x1": .31, "y1": .31,
         "x2": .51, "y2": .41, "confidence": .96},
    ], TARGET)


def test_current_point_anchors_keep_circle_label_and_connection_together():
    raw = {"speech": "把左端点记为A，再连接这两个端点。", "page": [
        {"kind": "circle", "anchor_id": "p1", "confidence": .96},
        {"kind": "label", "anchor_id": "p1", "text": "A", "box": [.8, .8, .1, .1], "confidence": .96},
        {"kind": "line", "from_anchor_id": "p1", "to_anchor_id": "p2",
         "x1": .9, "y1": .9, "x2": .99, "y2": .99, "confidence": .96},
        {"kind": "circle", "anchor_id": "p2", "confidence": .96},
    ]}
    beat = teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "42", _geometry_anchors(), True)
    assert [mark["kind"] for mark in beat["page"]] == ["circle", "label", "line", "circle"]
    assert beat["page"][0]["anchor_id"] == beat["page"][1]["anchor_id"] == "p1"
    assert beat["page"][1]["box"] == [.3, .3, .02, .02]
    assert beat["page"][2] == {"kind": "line", "x1": .31, "y1": .31,
                               "x2": .51, "y2": .41, "anchor_id": "p1:p2"}


def test_new_paper_geometry_rejects_guessed_positions_and_accepts_visible_segment():
    raw = {"speech": "沿着这一条可见边看。", "page": [
        {"kind": "arrow", "space": "page", "x1": .3, "y1": .3, "x2": .4, "y2": .4, "confidence": .99},
        {"kind": "label", "text": "A", "box": [.3, .3, .02, .02], "confidence": .99},
        {"kind": "arrow", "anchor_id": "s1", "confidence": .96},
    ]}
    beat = teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "42", _geometry_anchors(), True)
    assert beat["page"] == [{"kind": "arrow", "anchor_id": "s1",
                             "x1": .31, "y1": .31, "x2": .51, "y2": .41,
                             "anchor_text": "可见边"}]


def test_short_beats_preserve_logical_step_metadata_without_ending_the_whole_method():
    raw = [
        {"speech": "先看当前这一处条件。", "step_id": 1, "step_title": "核对条件", "step_end": False},
        {"speech": "这个条件说明了它们之间的关系。", "step_id": 1, "step_title": "核对条件", "step_end": True},
        {"speech": "接着解释为什么可以用这一关系。", "step_id": 2, "step_title": "解释依据", "step_end": True},
    ]
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "当前可读条件", "box": TARGET}, {}, "三年级", 1,
        model_stream=lambda *_args: iter(["\n".join(json.dumps(item) for item in raw) + "\n"])))
    assert len(result) == 3
    assert [(beat["step_id"], beat["step_end"]) for _, beat in result] == [(1, False), (1, True), (2, True)]
    assert result[1][1]["step_title"] == "核对条件"
    legacy = teaching_ink.normalized_teaching_beat({"speech": "保留原有的普通提示。"}, TARGET, TARGET, "")
    assert legacy["step_end"] is True


def test_long_ordinary_fallback_keeps_every_character_and_final_check_with_current_boards():
    explanation = (
        "先看题目给出的两个条件。每个条件都要对应到图上实际的位置，不能只看数字。"
        "因为这两部分有同样长的一条边，所以可以比较另一条边。"
        "接着把当前得到的关系代回题目，分别检查两个条件，不能只检查其中一个。"
    )
    tail = "最后再核对两处条件与单位，说明为什么都符合原题。现在请你自己动笔检验。"
    speech = (explanation * 12)[:719 - len(tail)] + tail
    beats = teaching_ink.fallback_teaching_beats(speech, TARGET, turn={
        "teaching_move": "wait_for_writing", "reply_mode": "writing", "needs_parent": False})
    assert len(speech) == 719
    assert "".join(beat["speech"] for beat in beats) == speech
    assert 3 < len(beats) < 10
    assert max(len(beat["speech"]) for beat in beats) <= 150
    assert beats[-1]["speech"].endswith("现在请你自己动笔检验。")
    assert beats[-1]["reply_mode"] == "writing" and beats[-1]["step_end"] is True
    assert all(beat["reply_mode"] == "none" and not beat["needs_parent"] for beat in beats[:-1])
    for beat in beats:
        for note in beat["scratch"]:
            assert note["text"].removeprefix("当前一步：") in beat["speech"]
    assert any("最后再核对" in beat["speech"] for beat in beats)
    assert all(beats[index]["step_id"] <= beats[index + 1]["step_id"] for index in range(len(beats) - 1))


def test_fallback_places_existing_cue_only_when_its_anchor_is_actually_named():
    cue = {"kind": "underline", "text": "每份", "box": [.3, .3, .05, .01]}
    speech = "先分清楚题目中的整体是什么意思。因为各份一样多，所以再看每份表示哪一份。请说说你准备怎样检查？"
    beats = teaching_ink.fallback_teaching_beats(speech, TARGET, cue, {"reply_mode": "spoken"})
    marked = [beat for beat in beats if beat["page"]]
    assert len(marked) == 1 and "每份" in marked[0]["speech"]
    assert marked[0]["page"] == [cue]
    assert all(beat["page"] == [] for beat in beats if "每份" not in beat["speech"])
    assert beats[-1]["reply_mode"] == "spoken"
    assert all(beat["reply_mode"] == "none" for beat in beats[:-1])
    assert not any(beat["page"] for beat in teaching_ink.fallback_teaching_beats(speech, TARGET, {**cue, "text": "未知条件"}))
    assert not any(beat["page"] for beat in teaching_ink.fallback_teaching_beats(speech, TARGET, {"kind": "line", "x1": .2}))


def test_fallback_keeps_whitespace_decimals_and_legacy_single_beat_semantics():
    speech = "  核对 1.25 米与 125 厘米的对应关系；\n再说说为什么可以统一单位？  "
    beats = teaching_ink.fallback_teaching_beats(speech, TARGET)
    assert "".join(beat["speech"] for beat in beats) == speech
    assert any("1.25" in beat["speech"] for beat in beats)
    assert beats[-1]["reply_mode"] == "spoken"
    cue = {"kind": "circle", "box": TARGET}
    legacy = teaching_ink.fallback_teaching_beat(speech, TARGET, cue)
    assert legacy["speech"] == speech and legacy["page"] == [cue]


def test_oversize_fallback_widens_fragments_instead_of_losing_the_tail():
    speech = "因为条件相同，所以这一步可以比较。" * 150 + "最后逐项代回核对。"
    beats = teaching_ink.fallback_teaching_beats(speech, TARGET)
    assert len(beats) <= teaching_ink.MAX_TEACHING_BEATS
    assert "".join(beat["speech"] for beat in beats) == speech
    assert beats[-1]["speech"].endswith("最后逐项代回核对。")


@pytest.mark.parametrize("configured,expected", [(6, 6), (20, 20), (120, 30)])
def test_anchor_read_budget_stays_within_configured_timeout(monkeypatch, configured, expected):
    calls = []

    def post(_settings, _payload, timeout, fallback):
        calls.append((timeout, fallback))
        return {"choices": [{"message": {"content": '{"anchors":[]}'}}]}

    monkeypatch.setattr(ai, "post_model", post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test",
                               ai_model="locator", ai_timeout_seconds=configured)
    assert ai.read_teaching_anchors(settings, b"crop", TARGET, TARGET, "条件") == []
    assert calls == [(expected, False)]


def test_short_complete_method_is_not_truncated_by_the_old_sixteen_beat_limit():
    beats = [{"speech": f"这是第{index + 1}段所需的理由说明。", "step_id": index // 3 + 1,
              "step_end": index % 3 == 2 or index == 19} for index in range(20)]
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "当前可读条件", "box": TARGET}, {}, "三年级", 1,
        model_stream=lambda *_args: iter(["\n".join(json.dumps(item) for item in beats) + "\n"])))
    assert len(result) == 20
    assert result[-1][1]["speech"] == beats[-1]["speech"]


def test_line_carries_the_actual_spoken_cue_for_short_sentence_synchronization():
    beat = teaching_ink.normalized_teaching_beat({
        "speech": "现在沿左侧描这一条边。", "page": [
            {"kind": "line", "anchor_id": "s1", "spoken_cue": "左侧", "confidence": .96}]},
        TARGET, TARGET, "", _geometry_anchors(), True)
    assert beat["page"][0]["anchor_text"] == "左侧"


def test_two_condition_marks_do_not_displace_a_reliable_paper_line():
    anchors = [ANCHOR, {"id": "whole", "text": "整体", "box": [.4, .3, .04, .01],
                        "confidence": .96}, *_geometry_anchors()]
    raw = {"speech": "一半与整体的关系，沿这条可见边对照。", "page": [
        {"kind": "circle", "anchor_id": "a1", "confidence": .96},
        {"kind": "underline", "anchor_id": "whole", "confidence": .96},
        {"kind": "line", "anchor_id": "s1", "confidence": .96},
        {"kind": "line", "x1": .2, "y1": .2, "x2": .7, "y2": .6, "confidence": .99},
    ]}
    beat = teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "未知", anchors, True)
    assert [mark["kind"] for mark in beat["page"]] == ["circle", "underline", "line"]
    assert beat["page"][-1]["anchor_id"] == "s1"


@pytest.mark.parametrize("anchor_id", ["a1", "p1", "s1"])
def test_paper_note_is_short_spoken_and_located_by_current_anchor(anchor_id):
    anchors = [ANCHOR, *_geometry_anchors()]
    raw = {"speech": "把这一关系写成8加7等于15，再检查题目要求。",
           "page": [{"kind": "note", "anchor_id": anchor_id, "text": "8+7=15",
                     "box": [.8, .8, .1, .1], "confidence": .96}]}
    beat = teaching_ink.normalized_teaching_beat(
        raw, TARGET, TARGET, "15", anchors, True, True, True)
    note = beat["page"][0]
    assert note["kind"] == "note" and note["anchor_id"] == anchor_id
    assert note["box"] != [.8, .8, .1, .1]
    assert note["text"] == "8+7=15"
    assert teaching_ink.normalized_teaching_beat(
        raw, TARGET, TARGET, "15", anchors, True, False, True) is None


@pytest.mark.parametrize("anchor,spoken,expected", [
    ("12", "先圈十二这个条件。", "十二"),
    ("36", "看三十六表示的面积。", "三十六"),
    ("33", "再读三十三。", "三十三"),
    ("36", "三十三不是这次要找的数。", None),
    ("33", "三十六不是这次要找的数。", None),
    ("12", "这里写112。", None),
    ("12", "一百一十二在另一处。", None),
    ("36", "九百三十六是另一个数。", None),
    ("36", "936是另一个数。", None),
    ("36", "36.5是另一个数。", None),
    ("36", "变量x36不代表这个条件。", None),
    ("36", "负三十六与正数不同。", None),
    ("1234567890123456789012345678901", "1234567890123456789012345678902不是同一数。", None),
    ("三十六", "先读36这个数。", "36"),
    ("一半", "先看0.5。", None),
    ("一半", "先理解一半的意思。", "一半"),
    ("12厘米", "十二厘米长。", "十二厘米"),
])
def test_anchor_number_equivalence_is_whole_value_and_returns_actual_speech(anchor, spoken, expected):
    assert ai.spoken_anchor_text(anchor, spoken) == expected
    assert ai.anchor_text_mentioned(anchor, spoken) is (expected is not None)


@pytest.mark.parametrize("kind", ["circle", "underline", "box", "check"])
def test_numeric_paper_mark_keeps_its_chinese_spoken_span(kind):
    anchor = {**ANCHOR, "text": "36"}
    beat = teaching_ink.normalized_teaching_beat({
        "speech": "先看三十六这个已知条件。", "page": [
            {"kind": kind, "anchor_id": "a1", "confidence": .96}]},
        TARGET, TARGET, "", [anchor], True)
    assert beat["page"][0]["text"] == "36"
    assert beat["page"][0]["anchor_text"] == "三十六"
    wrong = teaching_ink.normalized_teaching_beat({
        "speech": "先看九百三十六这个数。", "page": [
            {"kind": kind, "anchor_id": "a1", "confidence": .96}]},
        TARGET, TARGET, "", [anchor], True)
    assert wrong["page"] == []


@pytest.mark.parametrize("text,speech,expected,actual", [
    ("36÷12＝3；大边是小边的3倍", "三十六除以十二等于三，所以大正方形边长是小边的三倍。",
     "36÷12＝3", "三十六除以十二等于三"),
    ("小正方形面积＝12÷2＝6（平方单位）", "十二除以二等于六，所以所求面积是六平方单位。",
     "12÷2＝6", "十二除以二等于六"),
    ("检查：2×6＝12", "检查右上，二乘六等于十二。", "2×6＝12", "二乘六等于十二"),
    ("检查：2×3＝6块，6×6＝36", "这里只检查六乘六等于三十六。", "6×6＝36", "六乘六等于三十六"),
    ("8+7=15", "八加七等于十五。", "8+7=15", "八加七等于十五"),
    ("检查：8+7=15", "检查：八加七等于十五。", "检查：8+7=15", "检查：八加七等于十五"),
])
def test_model_note_projects_only_its_explicit_spoken_math_core(text, speech, expected, actual):
    raw = {"speech": speech, "page": [{"kind": "note", "anchor_id": "a1", "text": text, "confidence": .96}]}
    beat = teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "", [ANCHOR], True, True, True)
    assert beat["page"][0]["text"] == expected
    assert beat["page"][0]["anchor_text"] == actual
    assert beat["page"][0]["box"] == ANCHOR["box"]


@pytest.mark.parametrize("text,speech", [
    ("2×3=6", "这里只有六乘六等于三十六。"),
    ("3-1=2", "三份减一份剩两份。"),
    ("2×6=13", "二乘六等于十三。"),
    ("12÷2=6", "一百一十二除以二等于五十六。"),
    ("6×6=36", "六乘六等于三百六十。"),
    ("8÷0=1", "八除以零等于一。"),
    ("12+3=15", "一，二加三等于十五。"),
    ("12+3=15", "1 2加3等于15。"),
])
def test_note_projection_does_not_invent_math_or_reinterpret_quantity_words(text, speech):
    raw = {"speech": speech, "page": [{"kind": "note", "anchor_id": "a1", "text": text, "confidence": .96}]}
    beat = teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "", [ANCHOR], True, True, True)
    assert beat["page"] == []


def test_projected_note_needs_original_model_reference_reliable_permission_and_noanswer_guard():
    raw = {"speech": "八加七等于十五。", "page": [
        {"kind": "note", "anchor_id": "a1", "text": "计算：8+7=15", "confidence": .96}]}
    for anchors, worked, final in [([], True, True), ([ANCHOR], False, True), ([ANCHOR], True, False)]:
        beat = teaching_ink.normalized_teaching_beat(raw, TARGET, TARGET, "15", anchors, True, final, worked)
        assert beat is None or not beat["page"]
    missing = teaching_ink.normalized_teaching_beat({"speech": raw["speech"], "page": []}, TARGET, TARGET, "", [ANCHOR], True, True, True)
    assert missing["page"] == []  # Never promote an equation that the model did not submit as a note.


def test_fallback_numeric_cue_follows_its_actual_chinese_fragment():
    cue = {"kind": "circle", "text": "36", "box": ANCHOR["box"]}
    beats = teaching_ink.fallback_teaching_beats("先读题目要求，确定这次要找什么。接着看三十六这个条件。", TARGET, cue)
    marked = [beat for beat in beats if beat["page"]]
    assert len(marked) == 1 and "三十六" in marked[0]["speech"]
    assert marked[0]["page"][0]["anchor_text"] == "三十六"


def test_page_scan_accepts_readable_screen_work_without_relaxing_visibility(monkeypatch):
    payloads = []
    page = {"box": [.2, .1, .6, .8], "confidence": .96, "readable": True, "subject": "数学"}

    def post(_settings, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": json.dumps({"reason": "电子题图完整清楚", "pages": [page]})}}]}

    monkeypatch.setattr(ai, "post_model", post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test", ai_model="vision")
    assert len(ai.scan_pages(settings, b"screen", fill_spread=False)["pages"]) == 1
    instruction = payloads[0]["messages"][0]["content"]
    assert "不能仅因内容来自屏幕、截图或电子页面就拒绝" in instruction
    assert "不能猜框" in instruction and "模糊、遮挡、裁边" in instruction
    page["readable"] = False
    assert ai.scan_pages(settings, b"screen", fill_spread=False)["pages"] == []
    page.update(readable=True, box=[.98, .1, .6, .8])
    assert ai.scan_pages(settings, b"screen", fill_spread=False)["pages"] == []


@pytest.mark.parametrize("problem", ["unreliable", "missing_anchor", "unspoken", "long"])
def test_paper_note_does_not_guess_or_add_an_unspoken_calculation(problem):
    raw = {"speech": "连接两个端点，检查这条边。", "page": [
        {"kind": "note", "anchor_id": "p1", "text": "连接两个端点", "confidence": .96}]}
    if problem == "missing_anchor":
        raw["page"][0]["anchor_id"] = "unknown"
    elif problem == "unspoken":
        raw["page"][0]["text"] = "另一种未讲过的操作"
    elif problem == "long":
        raw["page"][0]["text"] = "长" * 61
    beat = teaching_ink.normalized_teaching_beat(
        raw, TARGET, TARGET, "未知", _geometry_anchors(), True, False, problem != "unreliable")
    assert beat["page"] == []


def test_mixed_anchor_budget_preserves_current_geometry_after_many_text_boxes():
    raw = [{"id": f"t{index}", "text": f"词{index}", "box": [.3, .3, .02, .01],
            "confidence": .96} for index in range(12)]
    anchors = ai.normalized_text_anchors(raw + _geometry_anchors(), TARGET)
    assert len(anchors) == 12
    assert {"p1", "p2", "s1"} <= {item["id"] for item in anchors}


@pytest.mark.parametrize("question", ["请在原图连线说明", "请勾勒这条边", "请描出两条边", "圈出边看看"])
def test_geometry_request_is_not_limited_to_explicit_geometry_in_question_text(question):
    assert ai.teaching_geometry_needed("只有数量条件", question)
    assert not ai.teaching_geometry_needed("找出关键词", "给这一句话划线")


def test_geometry_anchor_validation_rejects_unreadable_or_invalid_locations():
    anchors = ai.normalized_text_anchors([
        {"id": "low", "kind": "point", "box": [.3, .3, .02, .02], "confidence": .89},
        {"id": "outside", "kind": "point", "box": [.8, .8, .02, .02], "confidence": .99},
        {"id": "bad", "kind": "segment", "x1": .3, "y1": .3, "x2": float("nan"),
         "y2": .4, "confidence": .99},
        {"id": "zero", "kind": "segment", "x1": .3, "y1": .3, "x2": .3,
         "y2": .3, "confidence": .99},
    ], TARGET)
    assert anchors == []


def test_optional_text_and_geometry_read_share_one_request_and_locator_model(monkeypatch):
    calls = []
    instructions = []

    def post(_settings, payload, timeout, allow_fallback):
        calls.append((payload["model"], timeout, allow_fallback))
        instructions.append(payload["messages"][0]["content"])
        return {"choices": [{"message": {"content": json.dumps({"anchors": [
            {"id": "p", "kind": "point", "text": "顶点", "box": [.2, .2, .02, .02], "confidence": .96},
            {"id": "s", "kind": "segment", "x1": .2, "y1": .2, "x2": .6, "y2": .6, "confidence": .96},
            {"id": "t", "text": "一半", "box": [.2, .3, .04, .01], "confidence": .96},
        ]})}}]}

    monkeypatch.setattr(ai, "post_model", post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test",
                               ai_model="configured-locator", ai_fast_vision_model="ignored-fast-model",
                               ai_timeout_seconds=120)
    anchors = ai.read_teaching_anchors(settings, b"crop", TARGET, TARGET, "一半", geometry_needed=True)
    assert calls == [("configured-locator", 30, False)]
    assert "优先给清楚可见的端点" in instructions[0]
    assert {item.get("kind", "text") for item in anchors} == {"point", "segment", "text"}
    segment = next(item for item in anchors if item["id"] == "s")
    assert (segment["x1"], segment["y1"], segment["x2"], segment["y2"]) == (.3, .28, .5, .44)


def test_stream_receives_current_question_context_age_and_stops_for_spoken_reply(monkeypatch):
    payloads = []
    first = {"speech": "一半表示把整体平均分成两份。你能指出整体吗？",
             "teaching_move": "answer_concept", "reply_mode": "spoken",
             "page": [], "scratch": [], "needs_parent": False}
    second = {"speech": "再把完整解法告诉你。", "page": [], "scratch": []}

    def stream(_settings, payload):
        payloads.append(payload)
        yield json.dumps(first, ensure_ascii=False) + "\n" + json.dumps(second) + "\n"

    monkeypatch.setattr(teaching_ink, "stream_model", stream)
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "第二题", "evidence": "一半", "box": TARGET, "anchors": [ANCHOR]},
        {"answer": "42", "steps": "核对整体"}, "三年级", 3,
        question="一半是什么意思", session_context="孩子此前说不懂整体", age_band="primary",
        assessment={"verdict": "incomplete", "summary": "需要说清整体"}, textbook="配置教材"))
    assert len(result) == 1
    assert result[0][1]["teaching_move"] == "answer_concept"
    user = payloads[0]["messages"][1]["content"][0]["text"]
    assert "一半是什么意思" in user and "孩子此前说不懂整体" in user and "primary" in user
    assert "配置教材" in user and "未知时不得宣称课程同步" in payloads[0]["messages"][0]["content"]


def test_first_hint_hands_back_a_local_task_before_the_rest_of_the_solution():
    payloads = []
    question = "我容易漏掉，请先教一种方法，让我自己继续。"
    beats = [
        {"speech": "可以按同一种标准分组，先看其中的一小组。", "page": [], "scratch": []},
        {"speech": "这里用两条线作一个局部示意，再试着找下一组。",
         "teaching_move": "wait_for_writing", "reply_mode": "writing", "page": [
             {"kind": "line", "space": "page", "x1": .3, "y1": .3,
              "x2": .4, "y2": .4, "confidence": .99}], "scratch": [
             {"kind": "line", "x1": 20, "y1": 20, "x2": 40, "y2": 40},
             {"kind": "text", "role": "label", "x": 20, "y": 15, "text": "局部示意"}]},
        {"speech": "接着把其余各组和所有小计全部告诉你。", "page": [], "scratch": []},
    ]

    def stream(_settings, payload):
        payloads.append(payload)
        yield "\n".join(json.dumps(beat, ensure_ascii=False) for beat in beats) + "\n"

    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "看图分组", "box": TARGET, "anchors": []},
        {"answer": "未知总数", "steps": "教师内部完整推理"}, "三年级", 1,
        question=question, model_stream=stream))
    assert len(result) == 2
    assert result[-1][1]["reply_mode"] == "writing"
    assert result[-1][1]["page"] == []  # No current geometric anchor: never guess a paper edge.
    assert result[-1][1]["scratch"][0]["kind"] == "line"
    prompt = payloads[0]["messages"][0]["content"]
    assert ai.teaching_scope_guidance(1, ai.teaching_goal(question)) in prompt
    assert "不能刚抛出一个关系就停" in prompt and "本轮必须自洽" in prompt
    assert "最小局部示意" in prompt
    assert question in payloads[0]["messages"][1]["content"][0]["text"]


def test_each_teaching_beat_keeps_its_board_before_handing_back_the_task():
    beats = [
        {"speech": "先比较这两个量的单位，因为单位相同才能直接比较大小。", "teaching_move": "give_clue", "scratch": []},
        {"speech": "把相同单位的量放在一起比较，因为比较时单位必须一致。请你先自己试试。",
         "teaching_move": "wait_for_writing", "reply_mode": "writing", "scratch": []},
        {"speech": "继续把整题做完。", "scratch": []},
    ]
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "比较同单位的量", "box": TARGET, "read_confidence": .96},
        {"answer": "未知结果", "steps": "核对单位"}, "三年级", 1,
        model_stream=lambda *_args: iter(["\n".join(json.dumps(item) for item in beats) + "\n"])))
    assert len(result) == 2
    assert [beat["scratch"][0]["role"] for _, beat in result] == ["prose", "prose"]
    assert "比较这两个量的单位" in result[0][1]["scratch"][0]["text"]
    assert "相同单位的量" in result[1][1]["scratch"][0]["text"]
    assert "自己试试" not in result[1][1]["scratch"][0]["text"]


def test_explicit_full_review_can_use_multiple_beats_at_first_level():
    question = "请完整复核我已经写出的每一步，说明各步为什么成立。"
    payloads = []
    beats = [{"speech": speech, "page": [], "scratch": []} for speech in [
        "先核对你写出的条件，因为后面每一步都要以这些已知条件为依据。",
        "再核对你列出的关系，因为关系两边必须表示题目中对应的量。",
        "接着检查中间变形的依据，因为两边保持相等才能保证推理不变。",
        "最后检查步骤是否回应题目要求，因为结果必须同时符合题意和已知条件。"]]

    def stream(_settings, payload):
        payloads.append(payload)
        yield "\n".join(json.dumps(beat, ensure_ascii=False) for beat in beats) + "\n"

    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "已有多步推理", "box": TARGET,
         "read_confidence": .96},
        {"answer": "17", "steps": "核对每步依据", "confidence": .96,
         "ambiguous": False}, "三年级", 1,
        question=question, model_stream=stream))
    assert [beat["speech"] for kind, beat in result if kind == "beat"] == [
        beat["speech"] for beat in beats]
    assert all(beat["scratch"] and beat["scratch"][0]["role"] == "prose"
               for kind, beat in result if kind == "beat")
    assert "不一律把最后一步留空" in payloads[0]["messages"][0]["content"]
    assert question in payloads[0]["messages"][1]["content"][0]["text"]


def _concept_sketch():
    return {
        "speech": "这是局部示意图，只表示整体被平均分开，请你再对照题目。",
        "teaching_move": "wait_for_writing", "reply_mode": "writing",
        "scratch_basis": ["整体平均分成两份"],
        "page": [{"kind": "line", "space": "page", "x1": .3, "y1": .3,
                  "x2": .4, "y2": .4, "confidence": .99}],
        "scratch": [
            {"kind": "text", "role": "label", "x": 20, "y": 10, "text": "示意图"},
            {"kind": "rect", "x": 20, "y": 20, "w": 60, "h": 30},
            {"kind": "line", "x1": 50, "y1": 20, "x2": 50, "y2": 50},
        ],
    }


@pytest.mark.parametrize("trailing_newline", [True, False])
def test_uncertain_full_solution_allows_a_cited_local_concept_sketch(trailing_newline):
    raw, payloads = _concept_sketch(), []

    def stream(_settings, payload):
        payloads.append(payload)
        yield json.dumps(raw, ensure_ascii=False) + ("\n" if trailing_newline else "")

    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "把一个整体平均分成两份，取其中一份。",
         "box": TARGET, "read_confidence": .96},
        {"answer": "9999", "steps": "未经核验的完整推导9999", "confidence": .2},
        "三年级", 1, question="请先画一个局部概念示意。",
        allow_diagram=False, model_stream=stream))
    assert len(result) == 1
    beat = result[0][1]
    assert [item["kind"] for item in beat["scratch"]] == ["text", "rect", "line"]
    assert beat["scratch"][0]["text"] == "示意图"
    assert beat["page"] == []  # Local sketches never authorize guessed paper geometry.
    details = payloads[0]["messages"][1]["content"][0]["text"]
    assert "未经核验的完整推导" not in details and "9999" not in details


@pytest.mark.parametrize("problem", [
    "unreadable", "no_evidence", "uncited", "invented_basis", "unlabelled", "unannounced",
    "number", "calculation", "text_only",
])
def test_uncertain_solution_does_not_allow_unsupported_or_unlabelled_sketches(problem):
    raw = _concept_sketch()
    target = {"label": "题目", "evidence": "整体平均分成两份。",
              "box": TARGET, "read_confidence": .96}
    if problem == "unreadable":
        target["read_confidence"] = .8
    elif problem == "no_evidence":
        target["evidence"] = ""
    elif problem == "uncited":
        raw.pop("scratch_basis")
    elif problem == "invented_basis":
        raw["scratch_basis"] = ["整体平均分成五份"]
    elif problem == "unlabelled":
        raw["scratch"] = raw["scratch"][1:]
    elif problem == "unannounced":
        raw["speech"] = "请对照题目自己试一试。"
    elif problem in {"number", "calculation"}:
        raw["scratch"].append({"kind": "text", "x": 20, "y": 60,
                               "text": "75" if problem == "number" else "中间结果=多少"})
    elif problem == "text_only":
        raw["scratch"] = raw["scratch"][:1]
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET, target, {}, "三年级", 1,
        allow_diagram=False,
        model_stream=lambda *_args: iter([json.dumps(raw, ensure_ascii=False) + "\n"])))
    assert result[0][1]["scratch"] == []
    assert result[0][1]["page"] == []


@pytest.mark.parametrize("read_confidence", [.96, .8])
def test_unchecked_full_solution_keeps_only_current_condition_board_not_guessed_work(read_confidence):
    raw = {"speech": "先记录题目已经给出的条件，再由你试一试。", "scratch": [
        {"kind": "text", "role": "prose", "x": 8, "y": 12,
         "text": "题目条件：每箱有18个苹果"},
        {"kind": "text", "role": "prose", "x": 8, "y": 25,
         "text": "中间结果18÷3=6"},
        {"kind": "line", "x1": 10, "y1": 40, "x2": 50, "y2": 40},
    ]}
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "题目", "evidence": "每箱有18个苹果，求若干箱的总数。",
         "box": TARGET, "read_confidence": read_confidence}, {}, "三年级", 1,
        allow_diagram=False,
        model_stream=lambda *_args: iter([json.dumps(raw, ensure_ascii=False) + "\n"])))
    assert result[0][1]["scratch"] == ([raw["scratch"][0]] if read_confidence >= .85 else [])


@pytest.mark.parametrize("verdict", ["incomplete", "incorrect", "unclear"])
def test_structured_spoken_assessment_preserves_noncorrect_diagnosis(monkeypatch, verdict):
    monkeypatch.setattr(page_prep, "post_model", lambda *_args: {"choices": [{"message": {
        "content": json.dumps({"verdict": verdict, "confidence": .96, "summary": "请补充题目要求的单位"})}}]})
    result = page_prep.assess_spoken_answer(
        SimpleNamespace(ai_model="test"), {"evidence": "求时间", "read_confidence": .96},
        {"answer": "30分钟", "confidence": .96, "ambiguous": False}, "30", True)
    assert result["verdict"] == verdict
    assert result["response"] == ""


def test_optional_anchor_read_is_bounded_once_and_drops_unseen_text(monkeypatch):
    seen = []

    def post(_settings, payload, timeout, allow_fallback):
        seen.append((timeout, allow_fallback, payload["model"]))
        return {"choices": [{"message": {"content": json.dumps({"anchors": [
            {"id": "a1", "text": "一半", "box": [.2, .2, .04, .01], "confidence": .95},
            {"id": "a2", "text": "凭空条件", "box": [.3, .3, .04, .01], "confidence": .99},
        ]})}}]}

    monkeypatch.setattr(ai, "post_model", post)
    settings = SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test",
                               ai_model="test", ai_fast_vision_model="fast", ai_timeout_seconds=120)
    anchors = ai.read_teaching_anchors(settings, b"crop", TARGET, TARGET, "一半是多少")
    assert seen == [(30, False, "test")] and len(anchors) == 1
    monkeypatch.setattr(ai, "post_model", lambda *_args: (_ for _ in ()).throw(httpx.ReadTimeout("test")))
    assert ai.read_teaching_anchors(settings, b"crop", TARGET, TARGET, "一半") == []


def _session(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    headers = parent(client)
    child = pair(client, headers)
    device = module.read_token(module.settings.app_secret,
                               child["Authorization"].removeprefix("Bearer "))["sub"]
    ticket = module.make_token(module.settings.app_secret, "setup", device, 120)
    session = client.post("/v1/sessions", json={"setup_ticket": ticket}, headers=child).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    target = {"id": "q1", "label": "第二题", "box": TARGET, "evidence": "一半是多少",
              "read_confidence": .96, "verification_status": "verified",
              "solution": {"answer": "42", "steps": "核对整体与一半的关系",
                           "confidence": .96, "ambiguous": False}}
    monkeypatch.setattr(module, "page_preparation", lambda: SimpleNamespace(match=lambda *_a, **_k: target))
    return client, module, child, session, output.getvalue()


def test_stream_success_keeps_child_thought_once_and_uses_real_teaching_move(monkeypatch, tmp_path):
    client, module, child, session, image = _session(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "assess_spoken_answer", lambda *_args: {
        "verdict": "incomplete", "confidence": .96, "summary": "还缺单位", "response": ""})
    beat = {"speech": "你可以先自己试一试。", "teaching_move": "wait_for_writing",
            "reply_mode": "writing", "needs_parent": False, "page": [], "scratch": []}
    monkeypatch.setattr(module, "stream_model", lambda *_args: iter([json.dumps(beat) + "\n"]))
    response = client.post("/v1/hints/stream", data={
        "session_id": session, "question": "我先自己试试", "student_thought": "我准备先画图"},
        files={"image": ("page.jpg", image, "image/jpeg")}, headers=child)
    assert response.status_code == 200 and '"teaching_move": "wait_for_writing"' in response.text
    assert '"reply_mode": "writing"' in response.text
    with module.connect(module.settings.database_path) as db:
        rows = db.execute("SELECT text FROM reflections WHERE session_id=?", (session,)).fetchall()
    assert [row["text"] for row in rows] == ["我准备先画图"]


def test_stream_fallback_shares_one_anchor_budget_with_ordinary_hint(monkeypatch, tmp_path):
    client, module, child, session, image = _session(monkeypatch, tmp_path)
    reads = []
    monkeypatch.setattr(module, "read_teaching_anchors", lambda *_args: reads.append(1) or [])
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_args: iter([]))
    monkeypatch.setattr(module, "analyze", lambda *_args, **_kwargs: {
        "response": "先说说一半是什么意思？", "needs_parent": False,
        "teaching_move": "answer_concept", "reply_mode": "spoken", "visual_cue": None})
    response = client.post("/v1/hints/stream", data={
        "session_id": session, "question": "一半什么意思", "student_thought": "我还不明白"},
        files={"image": ("page.jpg", image, "image/jpeg")}, headers=child)
    assert response.status_code == 200 and "event: done" in response.text
    assert reads == [1]
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM reflections WHERE session_id=?", (session,)).fetchone()[0] == 1


@pytest.mark.parametrize("timeout", [False, True])
def test_uncached_spoken_thought_does_not_solve_same_question_twice(monkeypatch, tmp_path, timeout):
    client, module, child, session, image = _session(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "page_preparation", lambda: SimpleNamespace(match=lambda *_a, **_k: None))
    monkeypatch.setattr(module, "locate_question", lambda *_a, **_k: {
        "found": True, "label": "第二题", "box": TARGET,
        "evidence": "一半是多少", "confidence": .96})
    calls = []

    def solve(*_args):
        calls.append(1)
        if timeout:
            raise httpx.ReadTimeout("synthetic timeout")
        return {"answer": "42", "steps": "先找整体", "confidence": .96, "ambiguous": False}

    monkeypatch.setattr(module, "solve_question", solve)
    monkeypatch.setattr(module, "assess_spoken_answer", lambda *_args: {
        "verdict": "incomplete", "confidence": .96, "summary": "这是计划，还不是完整答案", "response": ""})
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_args: iter([("beat", {
        "speech": "先说说你想怎样找整体？", "teaching_move": "review_attempt",
        "reply_mode": "spoken", "needs_parent": False, "page": [], "scratch": []})]))
    response = client.post("/v1/hints/stream", data={
        "session_id": session, "question": "这样想可以吗", "student_thought": "我想先画图"},
        files={"image": ("page.jpg", image, "image/jpeg")}, headers=child)
    assert response.status_code == 200 and "event: done" in response.text
    assert calls == [1]


def test_all_correct_language_summary_uses_subject_neutral_self_check(monkeypatch, tmp_path):
    client, module, child, session, image = _session(monkeypatch, tmp_path)
    with module.connect(module.settings.database_path) as db:
        db.execute("UPDATE sessions SET correction_authorized_until=? WHERE id=?",
                   (module.current() + 600, session))
    monkeypatch.setattr(module, "correction_regions",
                        lambda picture: [("整页", (0, 0, 1, 1), picture)])
    monkeypatch.setattr(module, "analyze", lambda *_args, **_kwargs: {
        "confidence": .96, "subject": "语文", "visible_answer_count": 1,
        "checks": [{"question": "1", "status": "looks_correct", "answer": "因为下雨，所以带伞。",
                    "box": [.2, .3, .4, .1], "confidence": .96, "steps": []}]})
    response = client.post(f"/v1/sessions/{session}/corrections", data={"subject_label": "语文"},
                           files={"image": ("page.jpg", image, "image/jpeg")}, headers=child)
    assert response.status_code == 200
    assert "验算" not in response.json()["summary"]
    assert "对照题目要求再自查" in response.json()["summary"]
