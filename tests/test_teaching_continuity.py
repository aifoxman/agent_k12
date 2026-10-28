"""Coherent explanation and current-photo operations without invented answers."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from k12_server import ai, teaching_ink

TARGET = [.1, .1, .8, .8]
WORD = {"id": "t1", "text": "12厘米", "box": [.2, .2, .12, .04], "confidence": .96}
REGION = {"id": "r1", "kind": "region", "text": "左侧可见图块",
          "box": [.3, .3, .15, .2], "confidence": .96}
EDGE = {"id": "s1", "kind": "segment", "text": "上方边线", "confidence": .96,
        "x1": .2, "y1": .3, "x2": .5, "y2": .3}


def normalize(raw, anchors=None, **kwargs):
    return teaching_ink.normalized_teaching_beat(
        raw, [0, 0, 1, 1], TARGET, "", anchors or [WORD, REGION, EDGE], True, **kwargs)


def test_coherent_operation_reason_and_check_longer_than_old_limit_survive():
    speech = (
        "先沿上方边线看清题目指的是哪一条边，再读十二厘米这个长度条件。"
        "长度表示这条边有多长，不能直接当成整个图形的面积。"
        "因为题目要求比较长度，所以先把需要比较的量换成同一种单位，然后沿原图对应的位置核对，"
        "最后检查每一个数是否来自题目给定的条件、计算后的单位是否仍是长度单位。"
    )
    assert 120 < len(speech) < 360
    beat = normalize({"speech": speech, "step_id": 2, "step_title": "对应量与单位",
                      "step_end": True, "page": [
                          {"kind": "line", "anchor_id": "s1", "spoken_cue": "上方边线"},
                          {"kind": "circle", "anchor_id": "t1"}]})
    assert beat["speech"] == speech
    assert [mark["kind"] for mark in beat["page"]] == ["line", "circle"]
    assert beat["page"][1]["anchor_text"] == "十二厘米"
    assert beat["step_title"] == "对应量与单位" and beat["step_end"]
    assert normalize({"speech": "很长的重复内容" * 100}) is None


def _labeled_table():
    elements = [
        {"kind": "text", "role": "prose", "x": 2, "y": 2, "text": "对应表示意"},
        {"kind": "rect", "x": 3, "y": 15, "w": 50, "h": 24},
        *[{"kind": "line", "x1": x, "y1": 15, "x2": x, "y2": 39}
          for x in (13, 23, 33, 43)],
        {"kind": "line", "x1": 3, "y1": 27, "x2": 53, "y2": 27},
    ]
    elements.extend({"kind": "text", "role": "label", "x": 5 + column * 10,
                     "y": 20 + row * 12, "text": f"第{row}排第{column}格"}
                    for row in range(2) for column in range(5))
    return elements


@pytest.mark.parametrize("count", [17, 24, 25, 32])
def test_complete_bounded_schematic_keeps_all_labels_including_after_old_limits(count):
    elements = _labeled_table()
    assert len(elements) == 17
    elements.extend({"kind": "text", "role": "label", "x": 60, "y": 45 + index * 2,
                     "text": f"来源位置{index}"} for index in range(count - len(elements)))
    beat = normalize({"speech": "用对应表示意看清每一排和来源位置。", "scratch": elements})
    assert beat["scratch"] == elements
    assert beat["scratch"][-1]["text"] == elements[-1]["text"]


@pytest.mark.parametrize("count", [33, 1000])
def test_oversized_schematic_is_explicitly_withheld_instead_of_cutting_its_labels(count):
    speech = "沿上方边线先核对这个条件，再看对应示意。"
    elements = [{"kind": "line", "x1": 10, "y1": 10, "x2": 20, "y2": 20}] * count
    beat = normalize({"speech": speech, "page": [{"kind": "line", "anchor_id": "s1",
                                                   "spoken_cue": "上方边线"}],
                      "scratch": elements})
    assert beat["speech"] == speech and beat["page"][0]["anchor_id"] == "s1"
    assert len(beat["scratch"]) == 1
    assert beat["scratch"][0]["kind"] == "text"
    assert "分段展示" in beat["scratch"][0]["text"]
    assert "不完整" in beat["scratch"][0]["text"]


def test_extra_validation_batch_rejects_bad_coordinates_and_still_enforces_no_answer():
    notes = [{"kind": "text", "role": "label", "x": 2, "y": 2, "text": "条件"}] * 24
    invalid = [{"kind": "line", "x1": True, "y1": 2, "x2": 20, "y2": 30},
               {"kind": "text", "x": 101, "y": 3, "text": "越界标签"}]
    beat = normalize({"speech": "先核对条件，再决定怎样计算。", "scratch": notes + invalid})
    assert beat["scratch"] == notes
    raw = {"speech": "先核对条件，再决定怎样计算。", "scratch": notes + [
        {"kind": "text", "role": "label", "x": 2, "y": 4, "text": "最终答案17"}]}
    assert teaching_ink.normalized_teaching_beat(
        raw, [0, 0, 1, 1], TARGET, "17", [], True, allow_final_answer=False) is None


def test_prompt_keeps_annotation_constraints_internal_and_requests_bounded_complete_scenes():
    payloads = []

    def stream(_settings, payload):
        payloads.append(payload)
        yield "\n".join(json.dumps(beat) for beat in [
            {"speech": "用俯视示意看清前后排，再把来源和目标对应起来。",
             "step_id": 1, "step_title": "对应图中条件", "step_end": False,
             "page": [], "scratch": []},
            {"speech": "同一个比较阶段还要说明原因，因为条件相同，所以可以逐项对应。",
             "step_id": 1, "step_title": "对应图中条件", "step_end": True,
             "page": [], "scratch": [
                 {"kind": "text", "role": "prose", "x": 2, "y": 40,
                  "text": "比较依据：逐项对应已知条件；其他允许情形也按同一标准检查。"}]}]) + "\n"

    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "当前题目", "evidence": "按可见位置对应图形", "box": TARGET,
         "read_confidence": .96},
        {"answer": "一种可行构造", "steps": "逐项对应已知条件并检查构造", "confidence": .96},
        "三年级", 1, question="请完整示范构造和检查过程。", model_stream=stream))
    assert result and result[0][1]["speech"].startswith("用俯视示意")
    assert [beat["step_id"] for kind, beat in result if kind == "beat"] == [1, 1]
    assert [beat["step_end"] for kind, beat in result if kind == "beat"] == [False, True]
    assert "其他允许情形" in result[1][1]["scratch"][0]["text"]
    instruction = payloads[0]["messages"][0]["content"]
    assert "每片scratch最多32个元素" in instruction
    assert "复杂示意应分为连续语音片增量展示" in instruction
    assert "不要一次超量或省掉来源、目标、行列标签" in instruction
    assert "标注定位与核验规则只供教师内部遵守" in instruction
    assert "或核验系统状态读给孩子" in instruction
    assert "不能把选定布局缺几块直接当全局最少证明" in instruction
    assert "为什么换拼法或布局也不能更省" in instruction
    assert "不堆因子枚举" in instruction
    assert "分层图上层置上、下层置下" in instruction
    assert "不要把证明表中的每一行都升级成新逻辑步" in instruction
    assert instruction.count('"step_title":"对应图中条件"') == 2
    assert "不逐分支朗读全部算式和分类清单" in instruction
    assert "role=prose的结构化短板书" in instruction
    assert "不覆盖既有图形的区域" in instruction


@pytest.mark.parametrize("question,compact", [
    ("请完整讲解思路、解题过程和检查。", True),
    ("请完整讲解，但不要报答案。", True),
    ("请只给一个提示。", False),
    ("我想先自己试试。", False),
])
def test_compact_full_lesson_policy_keeps_proof_and_respects_request_scope(question, compact):
    goal = ai.teaching_goal(question)
    policy = ai.teaching_scope_guidance(1, goal)
    assert ("总口语优先约400到700字" in policy) is compact
    if compact:
        assert "约4到6个语义阶段" in policy
        assert "逐类写明适用范围、关键依据和所得界限" in policy
        assert "为什么已覆盖其他允许方案" in policy
        assert "关键一两项如何推得" in policy
        assert "不能只说看草稿就跳过关键因果" in policy
        assert "不是截断上限" in policy and "纯文字" in policy
        assert "仍随当前语音落在有可靠位置的原题上" in policy
    assert "明确不要答案时始终尊重" in policy


def test_short_spoken_comparison_keeps_all_classification_evidence_on_the_board():
    payloads = []
    proof = [
        "比较标准：先按已核对的限制分成两类；每种允许方案都属于其中一类。",
        "第一类：相同条件逐项对应，由此得到这一类所需的改变界限。",
        "第二类：按另一项限制逐项核对，得到同样的界限。",
        "两类已穷尽；可行构造达到共同界限，检查后才能作最优结论。",
    ]
    raw = [
        {"speech": "先用同一个标准比较两类方案，草稿列出了分类范围。", "step_id": 3,
         "step_title": "比较允许方案", "step_end": False, "page": [],
         "scratch": [{"kind": "text", "role": "prose", "x": 2, "y": 2 + index * 8,
                      "text": text} for index, text in enumerate(proof)]},
        {"speech": "第一类按相同条件逐项核对，第二类也受同一个界限约束；两类覆盖所有允许方案。",
         "step_id": 3, "step_title": "比较允许方案", "step_end": True,
         "page": [], "scratch": []},
    ]

    def stream(_settings, payload):
        payloads.append(payload)
        yield "\n".join(json.dumps(beat, ensure_ascii=False) for beat in raw) + "\n"

    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "当前题目", "evidence": "比较两类允许的方案", "box": TARGET,
         "read_confidence": .96},
        {"answer": "已核对结论", "steps": "分类覆盖全部允许方案，构造达到共同界限。", "confidence": .96},
        "小学", 1, question="请完整讲解方法和证明。", model_stream=stream))
    beats = [beat for kind, beat in result if kind == "beat"]
    assert len(beats) == 2 and [beat["step_end"] for beat in beats] == [False, True]
    assert [item["text"] for item in beats[0]["scratch"]] == proof
    assert all(beat["speech"] == original["speech"] for beat, original in zip(beats, raw, strict=True))
    assert "总口语优先约400到700字" in payloads[0]["messages"][0]["content"]


def test_spoken_length_preference_does_not_truncate_a_generated_complete_lesson():
    speech = ("沿当前已核对的条件逐项比较，说明每一个关系从哪里来，以及为什么这样检查。" * 6)
    raw = [{"speech": speech, "step_id": index + 1, "step_end": True, "page": [],
            "scratch": [{"kind": "text", "role": "prose", "x": 2, "y": 2,
                         "text": f"第{index + 1}阶段：保留本阶段的完整依据。"}]} for index in range(4)]
    assert 700 < sum(len(beat["speech"]) for beat in raw)
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="tutor"), b"page", b"crop", TARGET,
        {"label": "当前题目", "evidence": "按条件逐项比较", "box": TARGET, "read_confidence": .96},
        {"answer": "已核对结论", "steps": "逐项比较并检查所有允许情况。", "confidence": .96},
        "小学", 1, question="请完整讲解方法和检查。",
        model_stream=lambda *_args: iter(["\n".join(json.dumps(beat) for beat in raw) + "\n"])))
    assert [beat["speech"] for kind, beat in result if kind == "beat"] == [beat["speech"] for beat in raw]
    assert result[-1][1]["scratch"][0]["text"] == raw[-1]["scratch"][0]["text"]


def test_missing_pair_confidence_uses_both_current_point_anchors_without_free_coordinates():
    points = [{"id": name, "kind": "point", "text": text, "box": box, "confidence": .96}
              for name, text, box in [("p1", "左端点", [.2, .3, .02, .02]),
                                      ("p2", "右端点", [.5, .3, .02, .02])]]
    raw = {"speech": "沿这两个端点之间描出可见边。", "page": [
        {"kind": "line", "from_anchor_id": "p1", "to_anchor_id": "p2"}]}
    mark = normalize(raw, points)["page"][0]
    assert mark["x1"] == .21 and mark["x2"] == .51
    assert normalize(raw, points[:1])["page"] == []


@pytest.mark.parametrize("kind", ["circle", "underline", "box", "check"])
def test_missing_redundant_confidence_uses_current_anchor_but_low_explicit_score_still_rejects(kind):
    raw = {"speech": "看十二厘米这个条件。", "page": [{"kind": kind, "anchor_id": "t1"}]}
    assert normalize(raw)["page"][0]["box"] == WORD["box"]
    raw["page"][0]["confidence"] = .3
    assert normalize(raw)["page"] == []
    raw["page"][0].pop("confidence")
    raw["page"][0]["anchor_id"] = "unknown"
    assert normalize(raw)["page"] == []


def test_missing_id_can_only_resolve_one_exact_current_named_text_not_guessed_box():
    raw = {"speech": "圈出十二厘米这个长度条件。", "page": [
        {"kind": "circle", "text": "12厘米", "box": [.8, .8, .1, .1]}]}
    assert normalize(raw)["page"][0]["box"] == WORD["box"]
    assert len(normalize(raw, [WORD, {**WORD, "id": "duplicate"}])["page"]) == 1
    assert normalize(raw, [WORD, {**WORD, "id": "elsewhere", "box": [.6, .6, .1, .04]}])["page"] == []
    raw["page"][0]["anchor_id"] = "old-frame"
    assert normalize(raw)["page"] == []


def test_explicit_unambiguous_circle_operation_can_recover_empty_page_without_new_geometry():
    for anchor, speech in [(WORD, "先圈出十二厘米这个条件，因为后面比较的也是长度。"),
                           (REGION, "先圈出左侧可见图块，再观察它的轮廓。")]:
        mark = normalize({"speech": speech, "page": []})["page"][0]
        assert mark["anchor_id"] == anchor["id"] and mark["box"] == anchor["box"]


@pytest.mark.parametrize("speech", [
    "十二厘米是一个长度条件。", "先看这些条件。", "不要圈出十二厘米。",
    "我不确定是否该圈出十二厘米。", "现在请你圈出十二厘米。", "圈出未知的条件。",
    "沿上方边线画一条辅助线。", "把十二厘米写成12乘2等于24。",
])
def test_missing_page_never_becomes_generic_keyword_marks_or_invented_lines_notes(speech):
    assert normalize({"speech": speech, "page": []})["page"] == []


@pytest.mark.parametrize(("anchor", "speech", "expected"), [
    ("12厘米", "读出十二厘米。", "十二厘米"),
    ("第4题", "我们看第四题。", "第四题"),
    ("12厘米", "一百一十二厘米。", None),
    ("12厘米", "112厘米。", None),
    ("12厘米", "负十二厘米。", None),
    ("12厘米", "x十二厘米。", None),
    ("12厘米", "十二毫米。", None),
    ("12厘米", "一，二厘米。", None),
    ("12厘米", "1 2厘米。", None),
    ("一半", "读出二分之一。", None),
])
def test_literal_numeric_condition_equivalence_preserves_whole_values_and_units(anchor, speech, expected):
    assert ai.spoken_anchor_text(anchor, speech) == expected


@pytest.mark.parametrize("kind", ["circle", "box"])
def test_region_is_a_current_visible_object_not_a_text_word_or_mathematical_point(kind):
    anchors = ai.normalized_text_anchors([REGION], TARGET, "题干没有逐字描述这块图案")
    assert anchors[0]["kind"] == "region" and "x" not in anchors[0]
    mark = normalize({"speech": "先看左侧可见图块的形状。", "page": [
        {"kind": kind, "anchor_id": "r1", "box": [.7, .7, .1, .1]}]})["page"][0]
    assert mark["box"] == REGION["box"]
    assert normalize({"speech": "这里只讲数量关系。", "page": [
        {"kind": kind, "anchor_id": "r1"}]})["page"] == []


@pytest.mark.parametrize("mark", [
    {"kind": "underline", "anchor_id": "r1"},
    {"kind": "check", "anchor_id": "r1"},
    {"kind": "label", "anchor_id": "r1", "text": "A"},
    {"kind": "arrow", "from_anchor_id": "r1", "to_anchor_id": "r2"},
    {"kind": "line", "anchor_id": "r1"},
    {"kind": "note", "anchor_id": "r1", "text": "8+7=15"},
])
def test_region_cannot_invent_point_edge_geometry_or_worked_conclusions(mark):
    assert normalize({"speech": "左侧可见图块记作A，八加七等于十五。", "page": [mark]},
                     [REGION, {**REGION, "id": "r2"}],
                     allow_final_answer=True, allow_worked_notes=True)["page"] == []


def test_region_pixels_reject_empty_or_flat_boxes_and_never_move_the_model_position():
    image = Image.new("RGB", (640, 480), "white")
    canvas = ImageDraw.Draw(image)
    canvas.polygon([(200, 160), (240, 150), (270, 180), (230, 195)], outline="black", width=3)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    anchors = ai.verify_teaching_anchors(stream.getvalue(), [REGION], TARGET, "看图回答")
    assert anchors[0]["kind"] == "region" and anchors[0]["box"] == REGION["box"]
    for color in ("white", "black", "gray"):
        blank = io.BytesIO()
        Image.new("RGB", image.size, color).save(blank, "PNG")
        assert ai.verify_teaching_anchors(blank.getvalue(), [REGION], TARGET, "看图回答") == []
    empty = {**REGION, "box": [.6, .6, .1, .1]}
    assert ai.verify_teaching_anchors(stream.getvalue(), [empty], TARGET, "看图回答") == []


@pytest.mark.parametrize("change", [{"confidence": .89}, {"text": ""},
                                    {"box": [.95, .95, .04, .04]}, {"box": [True, .3, .1, .1]}])
def test_region_requires_readable_description_confidence_and_current_question_bounds(change):
    assert ai.normalized_text_anchors([{**REGION, **change}], TARGET) == []


def test_one_optional_visual_read_maps_region_and_geometry_together_without_new_model_calls(monkeypatch):
    calls = []

    def post(_settings, payload, timeout, fallback):
        calls.append((payload, timeout, fallback))
        return {"choices": [{"message": {"content": json.dumps({"anchors": [REGION, EDGE]})}}]}

    monkeypatch.setattr(ai, "post_model", post)
    anchors = ai.read_teaching_anchors(
        SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test", ai_model="locator",
                        ai_timeout_seconds=30), b"crop", [.2, .2, .5, .5], TARGET, "看图回答",
        geometry_needed=True, region_needed=True)
    assert len(calls) == 1 and calls[0][1:] == (30, False)
    region = next(anchor for anchor in anchors if anchor.get("kind") == "region")
    assert region["box"] == pytest.approx([.35, .35, .075, .1])
    instruction = calls[0][0]["messages"][0]["content"]
    assert "具体可见对象或局部region" in instruction and "不推断数量和隐藏结构" in instruction


@pytest.mark.parametrize("evidence,question,expected", [
    ("图形由小正方体堆成，求搬移数量。", "在原图圈出所用部分。", False),
    ("小正方体形成图中的凹槽。", "圈出左边那块积木，讲清搬移。", False),
    ("图中两边物体需要重新摆放。", "指出左边的积木。", False),
    ("图中图形有突出部分。", "在原图圈出突出的一块。", False),
    ("用积木拼图。", "把两个顶点连线。", True),
    ("用小正方体搭成图形。", "请沿这条边描边。", True),
    ("用积木搭成图形。", "连接点A和点B。", True),
    ("图中长方形的面积已知。", "请完整讲解。", True),
    ("三角形如图。", "圈出需要比较的部分。", True),
])
def test_object_positions_do_not_request_precision_geometry_for_generic_figure_words(
        evidence, question, expected):
    assert ai.teaching_geometry_needed(evidence, question) is expected


def test_object_only_visual_read_is_small_bounded_and_never_promotes_regions_to_geometry(monkeypatch):
    calls = []
    regions = [{**REGION, "id": f"r{index}", "text": f"可见图块{index}"}
               for index in range(8)]

    def post(_settings, payload, timeout, fallback):
        calls.append((payload, timeout, fallback))
        return {"choices": [{"message": {"content": json.dumps({"anchors": [EDGE, *regions]})}}]}

    monkeypatch.setattr(ai, "post_model", post)
    result = ai.read_teaching_anchors(
        SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test", ai_model="locator",
                        ai_timeout_seconds=90), b"crop", [0, 0, 1, 1], TARGET,
        "图中积木的左右部分需要搬移", region_needed=True)
    assert len(calls) == 1 and calls[0][1:] == (30, False)
    assert len(result) == 6 and all(item["kind"] == "region" for item in result)
    instruction = calls[0][0]["messages"][0]["content"]
    assert "最多6处" in instruction and "可靠对象不足时少给，不凑数" in instruction
    assert "不寻找point/segment" in instruction
    assert "优先给清楚可见的端点" not in instruction
    assert "坐标相对当前局部图归一化" in instruction


@pytest.mark.parametrize("error", [ValueError("bad JSON"), TimeoutError("slow")])
def test_object_visual_read_failure_does_not_generate_a_question_box_mark(monkeypatch, error):
    calls = []

    def post(*_args):
        calls.append(1)
        if isinstance(error, TimeoutError):
            import httpx
            raise httpx.ReadTimeout("bounded anchor read")
        raise error

    monkeypatch.setattr(ai, "post_model", post)
    result = ai.read_teaching_anchors(
        SimpleNamespace(ai_api_url="https://example.invalid", ai_api_key="test", ai_model="locator",
                        ai_timeout_seconds=9), b"crop", [0, 0, 1, 1], TARGET,
        "图中积木", region_needed=True)
    assert result == [] and calls == [1]


def test_fallback_preserves_operation_because_therefore_and_final_child_handoff_together():
    explanation = ("先对应题目的量，再统一单位。因为比较的必须是同一种量，所以不能把长度与面积直接比较。"
                   "把单位统一后再计算，最后代回题目逐项检查，确认没有漏掉条件。")
    speech = explanation * 5 + "现在请你自己列式检查。"
    beats = teaching_ink.fallback_teaching_beats(speech, TARGET, turn={
        "teaching_move": "wait_for_writing", "reply_mode": "writing"})
    assert "".join(beat["speech"] for beat in beats) == speech
    assert len(beats) < 6
    assert all(len(beat["speech"]) > 45 for beat in beats[:-1])
    assert any("因为比较的必须是同一种量，所以不能" in beat["speech"] for beat in beats)
    assert all(beat["reply_mode"] == "none" for beat in beats[:-1])
    assert beats[-1]["reply_mode"] == "writing" and not beats[-1]["scratch"]
    for beat in beats:
        assert all(note["text"].removeprefix("当前一步：") in beat["speech"] for note in beat["scratch"])


def test_multiline_plain_generator_fallback_keeps_earlier_reasons_and_last_check():
    speech = ("先圈出十二厘米这个条件，再把需要比较的量换成同单位。\n"
              "因为单位相同才能比较，所以先检查单位，不把长度当面积。\n"
              "最后逐项代回原题检查，确认没有漏条件。现在请你自己列式检查。")
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="solver"), b"page", b"crop", TARGET,
        {"label": "当前题", "evidence": "比较长度", "box": TARGET, "anchors": [WORD]},
        {}, "三年级", 1, model_stream=lambda *_args: iter([speech])))
    assert "".join(beat["speech"] for _, beat in result) == speech
    assert result[0][1]["page"][0]["anchor_id"] == "t1"
    assert "最后逐项代回原题检查" in result[-1][1]["speech"]


def test_full_lesson_prompt_keeps_coherent_method_and_page_operations_without_forced_handoff():
    payloads = []
    raw = [{"speech": "先圈出十二厘米这个已知长度，因为题目比较的是长度，我们用同单位计算。",
            "page": [], "step_id": 1, "step_title": "对应条件", "step_end": True},
           {"speech": "八加七等于十五，答案是15；再算十五减七等于八，两处条件吻合。",
            "page": [{"kind": "note", "anchor_id": "t1", "text": "8+7=15"}],
            "step_id": 2, "step_title": "计算并检查", "step_end": True},
           {"speech": "现在请你自己用同一个方法检查另一个例子。",
            "teaching_move": "wait_for_writing", "reply_mode": "writing"}]

    def stream(_settings, payload):
        payloads.append(payload)
        yield "\n".join(json.dumps(beat) for beat in raw) + "\n"

    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="solver"), b"page", b"crop", TARGET,
        {"label": "当前题", "evidence": "两个数量合并", "box": TARGET,
         "read_confidence": .96, "anchors": [WORD]},
        {"answer": "15", "steps": "合并后代回检查", "confidence": .96}, "三年级", 1,
        question="请完整讲解当前题目的思路和解题步骤，并说明怎样检查。",
        model_stream=stream))
    assert len(result) == 3 and result[0][1]["page"]
    assert result[1][1]["page"][0]["text"] == "8+7=15"
    assert result[-1][1]["reply_mode"] == "none"
    instruction = payloads[0]["messages"][0]["content"]
    assert "60到150" in instruction and "20到32" not in instruction
    assert "默认连续讲完整个请求" in instruction and "允许必要计算、最终结论和回代检查" in instruction
    assert "保留主体或选取起点的可见依据" in instruction
    assert "用源与目标标签说明对应" in instruction
    assert "不补隐藏结构、数量或猜端点" in instruction
    assert "region只供圈框、短批注和标明搬入示意的方向箭头，不能作精确几何端点" in instruction
    assert "仅本轮允许给出完整示范与结论时使用搬入箭头和region操作note" in instruction


def test_explicit_result_refusal_rejects_result_without_forcing_a_full_lesson_handoff():
    raw = [{"speech": "先圈出十二厘米这个条件，先把需要比较的量换成同样的单位。", "page": []},
           {"speech": "答案是15。", "page": []},
           {"speech": "现在请你自己动笔试试。", "teaching_move": "wait_for_writing", "reply_mode": "writing"}]
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="solver"), b"page", b"crop", TARGET,
        {"label": "当前题", "evidence": "比较题目数量", "box": TARGET,
         "read_confidence": .96, "anchors": [WORD]},
        {"answer": "15", "steps": "核对单位再计算", "confidence": .96}, "三年级", 1,
        question="请完整讲解方法，但先不要报最终答案。",
        model_stream=lambda *_args: iter(["\n".join(json.dumps(beat) for beat in raw) + "\n"])))
    assert len(result) == 2
    assert result[0][1]["page"] and result[-1][1]["reply_mode"] == "none"
    assert not any("答案是15" in beat["speech"] for _, beat in result)


def test_unverified_full_request_cannot_replace_solution_with_a_generic_concept_figure():
    concept = {"speech": "先看正方体示意图，正方体的长宽高相等。", "teaching_move": "answer_concept",
               "scratch_basis": ["正方体的长宽高相等"], "scratch": [
                   {"kind": "text", "role": "label", "x": 10, "y": 10, "text": "正方体示意图"},
                   {"kind": "rect", "x": 20, "y": 25, "w": 25, "h": 25}]}
    clarify = {"speech": "需要先核清哪两条边应当对应，请指出右侧的那条分界线？",
               "teaching_move": "clarify_target", "reply_mode": "spoken",
               "scratch_basis": concept["scratch_basis"], "scratch": concept["scratch"]}
    result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="solver"), b"page", b"crop", TARGET,
        {"label": "当前题", "evidence": "正方体的长宽高相等", "box": TARGET, "read_confidence": .96},
        {}, "三年级", 1, question="请完整讲解这道题的解题过程。", allow_diagram=False,
        model_stream=lambda *_a: iter([json.dumps(concept) + "\n" + json.dumps(clarify) + "\n"])))
    assert len(result) == 1 and result[0][1]["speech"] == clarify["speech"]
    assert result[0][1]["scratch"] == []
    # An actual concept request retains the existing bounded local-concept allowance.
    concept_result = list(teaching_ink.stream_teaching_beats(
        SimpleNamespace(ai_model="solver"), b"page", b"crop", TARGET,
        {"label": "当前题", "evidence": "正方体的长宽高相等", "box": TARGET, "read_confidence": .96},
        {}, "三年级", 1, question="正方体是什么意思，请画局部示意图。", allow_diagram=False,
        model_stream=lambda *_a: iter([json.dumps(concept) + "\n"])))
    assert concept_result[0][1]["scratch"]
