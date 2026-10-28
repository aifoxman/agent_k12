"""Two teaching scopes preserve complete reasoning and current-photo word marks."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from k12_server.ai import resolved_teaching_level, teaching_goal, teaching_scope_guidance
from k12_server.teaching_ink import stream_teaching_beats

TARGET = [.1, .1, .8, .8]
SOLUTION = {"answer": "17", "steps": "按当前条件分类，核对每类并检查所有允许情况。", "confidence": .96}


def run_lesson(beats, *, level=1, question="请读题并讲清关键思路。", anchors=None,
               image=b"image", evidence="当前完整可见的条件", solution=None, target_extra=None):
    payloads = []

    def model(_settings, payload):
        payloads.append(payload)
        yield "\n".join(json.dumps(beat, ensure_ascii=False) for beat in beats) + "\n"

    target = {"label": "当前题目", "box": TARGET, "evidence": evidence,
              "read_confidence": .96, "anchors": anchors or [], **(target_extra or {})}
    events = list(stream_teaching_beats(
        SimpleNamespace(ai_model="test"), image, image, TARGET, target,
        SOLUTION if solution is None else solution, "小学", level, question=question, model_stream=model))
    return [beat for kind, beat in events if kind == "beat"], payloads


@pytest.mark.parametrize("question,level,expected_level,expected_goal", [
    ("请读题，给我关键思路。", 1, 1, "explain_method"),
    ("继续讲。", 2, 2, "worked_example"),
    ("继续讲。", 3, 2, "worked_example"),
    ("请完整讲解过程和检查。", 1, 2, "worked_example"),
    ("解题思考", 1, 2, "worked_example"),
    ("请展开解题。", 1, 2, "worked_example"),
    ("请完整讲解，但不要报答案。", 1, 2, "worked_example"),
    ("我想先自己试试。", 2, 2, "independent_attempt"),
    ("只给我一条提示。", 2, 2, "scaffold"),
    ("一半是什么意思？", 2, 2, "explain_method"),
])
def test_two_tiers_resolve_scope_without_third_level_or_overriding_explicit_intent(
        question, level, expected_level, expected_goal):
    actual = resolved_teaching_level(question, level=level)
    assert actual == expected_level
    assert teaching_goal(question, level=actual) == expected_goal


def test_request_only_goal_call_remains_compatible():
    assert teaching_goal("这题怎么做？") == "scaffold"
    assert teaching_goal("这题怎么做？", level=1) == "explain_method"
    assert teaching_goal("这题怎么做？", level=2) == "worked_example"


def test_explicit_single_hint_keeps_its_limited_scope_with_a_substantive_reason():
    policy = teaching_scope_guidance(1, teaching_goal("只给一条提示。", level=1))
    assert "不强行展开整题或所有分类" in policy
    assert "不能只用一句反问代替这个线索" in policy
    assert "本轮第一层先教会怎样审题和采用什么关键方法" not in policy


@pytest.mark.parametrize("level", [1, 2])
def test_both_scopes_require_real_condition_marks_and_exhaustive_executable_method(level):
    policy = teaching_scope_guidance(level, "worked_example" if level == 2 else "explain_method")
    assert "整题大圈不算题干要点圈画" in policy
    assert "怎样推进所有允许类别及怎样避免重复" in policy
    assert "允许项数、取值范围、顺序是否算重复" in policy
    assert "无可靠位置不捏造坐标" in policy
    if level == 1:
        assert "必须有实质讲解" in policy and "默认不公布最终答案" in policy
        assert "先回答问题作为继续辅导的门槛" in policy
    else:
        assert "逐步操作及每步理由" in policy and "简短检查" in policy
        assert "不再重复另一轮启发问答" in policy and "不固定追加测验" in policy


def test_second_tier_continues_after_incorrect_intermediate_handoffs_and_keeps_complete_board():
    raw = [
        {"speech": "先看条件怎样相连。你想到了什么？", "reply_mode": "spoken"},
        {"speech": "按类别逐项检查，每种允许情况都不能漏掉。", "teaching_move": "wait_for_writing",
         "reply_mode": "writing", "scratch": [{"kind": "text", "role": "prose", "x": 2, "y": 2,
                                                "text": "第一类、第二类覆盖全部允许方案，各自依据已列出。"}]},
        {"speech": "把各类合并，最后的答案是17。检查没有漏项或重复。", "reply_mode": "none"},
        {"speech": "你能说出检查方法吗？", "reply_mode": "spoken"},
    ]
    result, payloads = run_lesson(raw, level=2)
    assert len(result) == len(raw)
    assert all(beat["reply_mode"] == "none" and beat["teaching_move"] != "wait_for_writing"
               for beat in result)
    assert result[1]["scratch"] == raw[1]["scratch"]
    assert "17" in result[2]["speech"]
    instruction = payloads[0]["messages"][0]["content"]
    assert "本轮答案许可：完整解法可靠" in instruction
    assert "不固定追加测验或要求口述" in instruction


@pytest.mark.parametrize("move,parent", [("clarify_target", False), ("parent_handoff", True)])
def test_second_tier_still_stops_for_real_missing_conditions_or_parent_handoff(move, parent):
    result, _ = run_lesson([
        {"speech": "这里关键条件还不能确认，请先核对这一处。", "teaching_move": move,
         "reply_mode": "none", "needs_parent": parent},
        {"speech": "不能在条件未确认时继续宣布完整答案。"},
    ], level=2)
    assert len(result) == 1 and result[0]["teaching_move"] == move


@pytest.mark.parametrize("question", ["我想先自己试试。", "请只给一条提示。"])
def test_limited_or_independent_request_still_ends_at_its_real_handoff(question):
    result, _ = run_lesson([
        {"speech": "先按条件把相同情况放一起。请你试一试。", "teaching_move": "wait_for_writing",
         "reply_mode": "writing"},
        {"speech": "继续把不需要的完整答案告诉你。"},
    ], level=2, question=question)
    assert len(result) == 1 and result[0]["reply_mode"] == "writing"


@pytest.mark.parametrize("level,question", [
    (1, "请读题并讲关键方法。"), (2, "请完整讲解，但不要报答案。"),
])
def test_first_tier_and_explicit_noanswer_do_not_expose_final_result(level, question):
    result, payloads = run_lesson([
        {"speech": "先按条件分组，并依次检查是否重复。"},
        {"speech": "最后的答案是17。"},
    ], level=level, question=question)
    assert len(result) == 1 and "17" not in result[0]["speech"]
    assert "本轮答案许可：不展示最终答案" in payloads[0]["messages"][0]["content"]


def test_explicit_full_request_at_old_first_level_opens_second_tier():
    result, payloads = run_lesson([{"speech": "逐类检查之后，最后的答案是17。"}],
                                 question="请完整讲解并给出答案和检查。")
    assert result and "17" in result[0]["speech"]
    assert "当前提示层级为2" in payloads[0]["messages"][0]["content"]


def test_pending_second_tier_returns_a_specific_wait_without_a_false_complete_solution():
    result, payloads = run_lesson([], level=2, solution={},
                                  target_extra={"_solution_inflight": True})
    assert len(result) == 1 and result[0]["page"] == [] and result[0]["scratch"] == []
    assert payloads == []


def paper_words():
    picture = Image.new("RGB", (600, 400), "white")
    canvas, font = ImageDraw.Draw(picture), ImageFont.load_default(size=26)
    anchors = []
    for index, (word, x) in enumerate([("nonzero", 90), ("sum", 260), ("methods", 365)]):
        left, top, right, bottom = canvas.textbbox((x, 100), word, font=font)
        canvas.text((x, 100), word, font=font, fill="black")
        anchors.append({"id": f"word{index}", "text": word, "confidence": .96,
                        "box": [(left - 3) / 600, (top - 3) / 400,
                                (right - left + 6) / 600, (bottom - top + 6) / 400]})
    output = io.BytesIO()
    picture.save(output, "PNG")
    return output.getvalue(), anchors


@pytest.mark.parametrize("level", [1, 2])
def test_each_tier_restores_at_most_two_underlines_per_clip_without_repeating_positions(level):
    image, anchors = paper_words()
    speech = "读题里的nonzero、sum和methods，先说明每一项限制。"
    result, payloads = run_lesson([{"speech": speech}] * 3, level=level,
                                 image=image, anchors=anchors, evidence="nonzero sum methods")
    assert len(payloads) == 1  # The restoration makes no additional model request.
    assert [mark["kind"] for mark in result[0]["page"]] == ["underline", "underline"]
    for mark, anchor in zip(result[0]["page"], anchors[:2], strict=True):
        assert mark["box"] == pytest.approx(anchor["box"], abs=1e-6)
    assert result[0]["speech"] == speech
    assert result[1]["page"][0]["anchor_id"] == anchors[2]["id"]
    assert result[2]["page"] == []


@pytest.mark.parametrize("change", ["low_confidence", "ambiguous", "whole_target", "unspoken",
                                    "unknown_evidence", "empty_pixels", "only_number", "uncertain"])
def test_reading_restoration_never_guesses_an_unknown_word_identity_or_box(change):
    image, anchors = paper_words()
    anchor = deepcopy(anchors[0])
    speech, evidence = "先读清nonzero这个限制。", "nonzero sum methods"
    if change == "low_confidence":
        anchor["confidence"] = .89
    if change == "ambiguous":
        anchors = [anchor, {**anchor, "id": "duplicate", "box": [.4, .5, .1, .04]}]
    else:
        anchors = [anchor]
    if change == "whole_target":
        anchor["box"] = TARGET
    if change == "unspoken":
        speech = "先把关键条件对应到题目。"
    if change == "unknown_evidence":
        evidence = "sum methods"
    if change == "empty_pixels":
        picture, output = Image.new("RGB", (600, 400), "white"), io.BytesIO()
        picture.save(output, "PNG")
        image = output.getvalue()
    if change == "only_number":
        anchor["text"], speech, evidence = "12", "先看第12题。", "12"
    if change == "uncertain":
        speech = "还看不清nonzero这个词，请先核对。"
    result, _ = run_lesson([{"speech": speech}], image=image, anchors=anchors, evidence=evidence)
    assert result and result[0]["page"] == []


def test_existing_condition_circle_is_not_duplicated_as_an_underline():
    image, anchors = paper_words()
    result, _ = run_lesson([{"speech": "先读清nonzero这个限制。", "page": [
        {"kind": "circle", "anchor_id": "word0"}]}],
        image=image, anchors=anchors, evidence="nonzero sum methods")
    assert [mark["kind"] for mark in result[0]["page"]] == ["circle"]


def test_no_current_text_anchor_never_becomes_a_target_sized_reading_mark():
    result, _ = run_lesson([{"speech": "先读清每一项限制，再按条件分类。"}], anchors=[])
    assert result[0]["page"] == []
