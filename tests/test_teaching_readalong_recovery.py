"""Recover omitted read-along ink without inventing wording, arithmetic or positions."""

from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from k12_server import ai
from k12_server.teaching_ink import normalized_teaching_beat, stream_teaching_beats

TARGET = [.05, .05, .9, .85]
SOLUTION = {"answer": "17", "steps": "根据当前条件分类计算，再核对所有允许情况。", "confidence": .97}


def paper(words=None):
    picture = Image.new("RGB", (800, 500), "white")
    draw, font = ImageDraw.Draw(picture), ImageFont.load_default(size=24)
    anchors = []
    for index, (text, role, x, y) in enumerate(words or [
        ("nonzero", "condition", 80, 90), ("methods", "question", 350, 90),
    ]):
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        draw.text((x, y), text, font=font, fill="black")
        anchors.append({"id": f"t{index}", "text": text, "role": role, "confidence": .97,
                        "box": [(left - 3) / 800, (top - 3) / 500,
                                (right - left + 6) / 800, (bottom - top + 6) / 500]})
    output = io.BytesIO()
    picture.save(output, "PNG")
    return output.getvalue(), anchors


def lesson(raw, *, anchors=None, image=None, level=2, question="请完整讲解。",
           evidence=None, solution=None, tail=False):
    original_image, original_anchors = paper()
    anchors = original_anchors if anchors is None else anchors
    image = original_image if image is None else image
    payloads = []

    def model(_settings, payload):
        payloads.append(payload)
        yield "\n".join(json.dumps(beat, ensure_ascii=False) for beat in raw) + ("" if tail else "\n")

    target = {"label": "当前题", "box": TARGET, "read_confidence": .97,
              "evidence": evidence if evidence is not None else " ".join(a["text"] for a in anchors),
              "anchors": anchors}
    events = list(stream_teaching_beats(
        SimpleNamespace(ai_model="test"), image, image, TARGET, target,
        SOLUTION if solution is None else solution, "小学", level, question=question,
        model_stream=model))
    return [value for kind, value in events if kind == "beat"], payloads


def operation_raw(speech="求methods，三加五等于八，再检查分类是否重复。", text="合并：3+5=8"):
    return {"speech": speech, "page": [], "scratch": [
        {"kind": "text", "role": "prose", "x": 8, "y": 15, "text": text}]}


@pytest.mark.parametrize("level", [1, 2])
@pytest.mark.parametrize("tail", [False, True])
def test_each_layer_restores_later_spoken_conditions_without_repeating_same_position(level, tail):
    raw = [{"speech": "先确认题目允许哪些情况。"}, {"speech": "读清nonzero这个限制。"},
           {"speech": "再看methods这个所求。"}, {"speech": "现在重新看nonzero这个限制。"}]
    result, payloads = lesson(raw, level=level, question="请读题讲方法。", tail=tail)
    assert len(payloads) == 1 and len(result) == 4
    assert [len(beat["page"]) for beat in result] == [0, 1, 1, 0]
    assert [result[index]["page"][0]["anchor_id"] for index in (1, 2)] == ["t0", "t1"]
    assert [beat["speech"] for beat in result] == [beat["speech"] for beat in raw]


@pytest.mark.parametrize("explicit", [False, True])
def test_same_word_same_position_aliases_are_one_real_occurrence(explicit):
    image, anchors = paper()
    alias = {**anchors[0], "id": "same-word-again"}
    alias["box"] = [anchors[0]["box"][0] + .001, *anchors[0]["box"][1:]]
    speech = "圈出nonzero这个限制。" if explicit else "读清nonzero这个限制。"
    result, _ = lesson([{"speech": speech}], anchors=[anchors[0], alias], image=image)
    assert len(result[0]["page"]) == 1
    assert result[0]["page"][0]["kind"] == ("circle" if explicit else "underline")


@pytest.mark.parametrize("chain", [False, True])
def test_same_word_at_distinct_positions_or_chain_of_aliases_remains_ambiguous(chain):
    image, anchors = paper()
    original = anchors[0]
    others = []
    for index, offset in enumerate((.1, .2) if chain else (2,)):
        duplicate = {**original, "id": f"other{index}"}
        duplicate["box"] = [original["box"][0] + original["box"][2] * offset,
                            *original["box"][1:]]
        others.append(duplicate)
    result, _ = lesson([{"speech": "读清nonzero这个限制。"}], anchors=[original, *others], image=image)
    assert result[0]["page"] == []


def test_same_position_aliases_do_not_repeat_a_provider_mark_on_later_clip():
    image, anchors = paper()
    duplicate = {**anchors[0], "id": "alias"}
    result, _ = lesson([
        {"speech": "先读nonzero。", "page": [{"kind": "circle", "anchor_id": "alias"}]},
        {"speech": "再读nonzero。"},
    ], image=image, anchors=[anchors[0], duplicate])
    assert len(result[0]["page"]) == 1 and result[1]["page"] == []


def test_heading_role_is_not_used_to_claim_condition_reading():
    image, anchors = paper()
    anchors[0]["role"] = "label"
    result, _ = lesson([{"speech": "读清nonzero。"}], image=image, anchors=anchors)
    assert result[0]["page"] == []


def test_recovery_does_not_strip_unspoken_parts_of_a_cross_line_anchor():
    image, anchors = paper()
    anchors[0]["text"] = "（nonzero）"
    result, _ = lesson([{"speech": "读清nonzero。"}], image=image, anchors=anchors)
    assert result[0]["page"] == []


@pytest.mark.parametrize("punctuation", ["。", "，", "？", ".", "；"])
def test_later_reading_recovery_uses_spoken_span_without_requiring_printed_sentence_end(punctuation):
    image, anchors = paper()
    anchors[0]["text"] += punctuation
    result, _ = lesson([{"speech": "先整理题目。"}, {"speech": "读清nonzero这个限制。"}],
                       image=image, anchors=anchors)
    mark = result[1]["page"][0]
    assert mark["text"] == anchors[0]["text"] and mark["box"] == anchors[0]["box"]
    assert mark["anchor_text"] == "nonzero"


@pytest.mark.parametrize("same_position", [False, True])
def test_sentence_punctuation_cannot_hide_a_second_word_position(same_position):
    image, anchors = paper()
    duplicate = {**anchors[0], "id": "with-sentence-end", "text": anchors[0]["text"] + "。"}
    if not same_position:
        duplicate["box"] = [.5, .5, .12, .05]
    result, _ = lesson([{"speech": "圈出nonzero这个限制。"}], image=image, anchors=[anchors[0], duplicate])
    assert len(result[0]["page"]) == int(same_position)


@pytest.mark.parametrize("speech", ["不要圈出nonzero这个限制。", "请你自己划出nonzero这个限制。",
                                    "暂不划出nonzero，请先确认条件。"])
def test_reading_recovery_does_not_turn_negation_or_child_assignment_into_teacher_ink(speech):
    result, _ = lesson([{"speech": speech}])
    assert result[0]["page"] == []


@pytest.mark.parametrize("tail", [False, True])
def test_spoken_submitted_true_equation_can_be_written_near_same_clause_question(tail):
    raw = operation_raw()
    result, payloads = lesson([raw], tail=tail)
    notes = [mark for mark in result[0]["page"] if mark["kind"] == "note"]
    assert len(payloads) == 1 and len(notes) == 1
    assert notes[0]["text"] == "3+5=8" and notes[0]["anchor_id"] == "t1"
    assert notes[0]["anchor_text"] == "三加五等于八"
    assert result[0]["speech"] == raw["speech"]
    assert result[0]["scratch"] == raw["scratch"]


def test_submitted_equation_can_bind_one_spoken_related_condition_operand():
    image, anchors = paper([("3", "condition", 100, 100)])
    result, _ = lesson([operation_raw("先看3这个条件，三加五等于八。")], image=image, anchors=anchors)
    assert result[0]["page"][0]["text"] == "3+5=8"
    assert result[0]["page"][0]["box"] == anchors[0]["box"]


@pytest.mark.parametrize("change", [
    "no_raw_formula", "unspoken", "wrong_arithmetic", "other_sentence", "split_formula",
    "unrelated_number", "untyped", "heading", "unknown_role", "ambiguous_question",
    "ambiguous_operands", "blank", "low_confidence", "unreliable", "example", "negation",
    "unknown_evidence", "first_tier", "noanswer", "invalid_coordinates", "invalid_kind",
    "rejected_board", "truncated_source",
])
def test_recovered_operation_note_preserves_scope_identity_relatedness_and_pixel_guards(change):
    image, anchors = paper()
    raw, options = operation_raw(), {}
    if change == "no_raw_formula":
        raw["scratch"][0]["text"] = "分类合并后检查遗漏。"
    elif change == "unspoken":
        raw["speech"] = "看methods，先比较各类是否重复。"
    elif change == "wrong_arithmetic":
        raw = operation_raw("求methods，三加五等于九。", "3+5=9")
    elif change == "other_sentence":
        raw["speech"] = "先看methods。三加五等于八。"
    elif change == "split_formula":
        raw["speech"] = "求methods，三加五。等于八。"
    elif change == "unrelated_number":
        image, anchors = paper([("29", "condition", 100, 100)])
        raw["speech"] = "看29这个条件，三加五等于八。"
    elif change in {"untyped", "heading", "unknown_role"}:
        anchors[1]["role"] = {"untyped": None, "heading": "label", "unknown_role": "answer"}[change]
    elif change == "ambiguous_question":
        anchors.append({**anchors[1], "id": "other", "box": [.6, .4, .12, .05]})
    elif change == "ambiguous_operands":
        image, anchors = paper([("3", "condition", 100, 100), ("5", "condition", 200, 100)])
        raw["speech"] = "看3和5，三加五等于八。"
    elif change == "blank":
        output = io.BytesIO()
        Image.new("RGB", (800, 500), "white").save(output, "PNG")
        image = output.getvalue()
    elif change == "low_confidence":
        anchors[1]["confidence"] = .89
    elif change == "unreliable":
        options["solution"] = {**SOLUTION, "confidence": .5}
    elif change == "example":
        raw["speech"] = "求methods，比如三加五等于八。"
    elif change == "negation":
        raw["speech"] = "求methods，不要写三加五等于八。"
    elif change == "unknown_evidence":
        options["evidence"] = "nonzero"
    elif change == "first_tier":
        options.update(level=1, question="请读题讲关键方法。")
    elif change == "noanswer":
        options["question"] = "请完整讲解，但不要报答案。"
    elif change == "invalid_coordinates":
        raw["scratch"][0]["x"] = 101
    elif change == "invalid_kind":
        raw["scratch"][0]["kind"] = "html"
    elif change == "rejected_board":
        raw["scratch"] *= 33
    elif change == "truncated_source":
        raw["scratch"][0]["text"] = "这是一段当前分析。" * 40 + "3+5=8"
    result, _ = lesson([raw], anchors=anchors, image=image, **options)
    assert all(mark["kind"] != "note" for beat in result for mark in beat["page"])


def test_recovery_keeps_already_provided_note_and_does_not_duplicate_it():
    raw = operation_raw()
    raw["page"] = [{"kind": "note", "anchor_id": "t1", "text": "3+5=8"}]
    result, _ = lesson([raw])
    assert sum(mark["kind"] == "note" for mark in result[0]["page"]) == 1


def test_recovered_notes_keep_only_equations_actually_submitted_and_spoken_now():
    raw = operation_raw(text="当前：3+5=8；稍后：6+4=10")
    result, _ = lesson([raw])
    assert [mark["text"] for mark in result[0]["page"] if mark["kind"] == "note"] == ["3+5=8"]
    # Speech alone is not a source from which to manufacture another formula.
    raw["scratch"][0]["text"] = "6+4=10"
    result, _ = lesson([raw])
    assert all(mark["kind"] != "note" for mark in result[0]["page"])


def test_optional_visual_roles_are_bounded_and_client_ocr_cannot_grant_them():
    image, anchors = paper()
    original = anchors[1]
    normalized = ai.normalized_text_anchors([original], TARGET, "methods")
    assert normalized[0]["role"] == "question"
    for value in ("answer", [], {"role": "question"}, None):
        assert "role" not in ai.normalized_text_anchors([{**original, "role": value}], TARGET, "methods")[0]
    payload = json.dumps({"image_sha256": hashlib.sha256(image).hexdigest(), "width": 800, "height": 500,
                          "anchors": [{**original, "level": "element"}]})
    local = ai.local_teaching_text_anchors(payload, image, TARGET, "methods")
    assert local and "role" not in local[0]


def test_one_visual_read_preserves_optional_role_and_requires_same_line_snippets(monkeypatch):
    calls = []

    def post(_settings, payload, timeout, fallback):
        calls.append((payload, timeout, fallback))
        return {"choices": [{"message": {"content": json.dumps({
            "coordinate_space": "normalized_0_1000", "box_format": "xywh", "anchors": [
                {"id": "q", "text": "methods", "role": "question", "confidence": .96,
                 "box": [400, 150, 100, 50]}]})}}]}

    monkeypatch.setattr(ai, "post_model", post)
    result = ai.read_teaching_anchors(
        SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test", ai_model="locator",
                        ai_timeout_seconds=30), b"crop", [0, 0, 1, 1], TARGET, "methods")
    assert len(calls) == 1 and calls[0][1:] == (30, False)
    assert result[0]["role"] == "question" and result[0]["box"] == [.4, .15, .1, .05]
    instruction = calls[0][0]["messages"][0]["content"]
    assert "跨行内容分别给框" in instruction and "不要用标题替代条件和所求" in instruction
    assert "一处短所求词和一到三处关键条件" in instruction


def test_role_conflict_in_same_position_does_not_grant_recovery_permission():
    image, anchors = paper()
    anchors.append({**anchors[1], "id": "label-alias", "role": "label"})
    result, _ = lesson([operation_raw()], anchors=anchors, image=image)
    assert all(mark["kind"] != "note" for mark in result[0]["page"])


def test_readalong_prompt_requires_actual_conditions_and_related_paper_calculation():
    _, payloads = lesson([{"speech": "先读清条件。"}])
    instruction = payloads[0]["messages"][0]["content"]
    assert "跨行条件分别引用各行实际词框" in instruction
    assert "不能用请你自己划线代替本轮实际标记" in instruction
    assert "在实际运算片自然逐字点读相关条件或所求锚点" in instruction
    assert "不能借无关标题挂结果" in instruction
    assert "若标记条件不足，直接讲该条件，不声称已画" in instruction
    scratch, _ = json.JSONDecoder().raw_decode(instruction.split('"scratch":', 1)[1])
    assert scratch == [{"kind": "text", "role": "prose", "text": "本步理由与比较依据"}]


def test_flowing_prose_needs_no_canvas_position_but_labels_still_do():
    prose = {"kind": "text", "role": "prose", "text": "按允许类别整理后检查是否遗漏。"}
    raw = {"speech": "按允许类别整理后检查是否遗漏。", "scratch": [prose]}
    beat = normalized_teaching_beat(raw, TARGET, TARGET, "")
    assert beat["scratch"] == [{"x": 2, "y": 2, **prose}]
    for invalid in ({**prose, "role": "label"}, {**prose, "x": True}, {**prose, "y": 101}):
        assert normalized_teaching_beat({**raw, "scratch": [invalid]}, TARGET, TARGET, "")["scratch"] == []


def test_normalizer_preserves_distinct_position_ambiguity_without_id():
    _, anchors = paper()
    anchors.append({**anchors[0], "id": "other", "box": [.4, .5, .12, .05]})
    beat = normalized_teaching_beat(
        {"speech": "圈出nonzero这个限制。", "page": [{"kind": "circle", "text": "nonzero"}]},
        TARGET, TARGET, "", anchors, True)
    assert beat["page"] == []
