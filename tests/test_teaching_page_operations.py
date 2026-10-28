"""Visible-object operations remain distinct from precise geometry and OCR identity."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from k12_server.teaching_ink import (
    normalized_teaching_beat,
    reveals_final_answer,
    stream_teaching_beats,
)

TARGET = [.05, .05, .9, .9]
REGIONS = [
    {"id": "source", "kind": "region", "text": "左侧向前伸出的部分",
     "box": [.15, .45, .15, .12], "confidence": .96},
    {"id": "gap", "kind": "region", "text": "中间凹槽",
     "box": [.5, .35, .12, .22], "confidence": .97},
]
MOVE = {"kind": "arrow", "purpose": "move", "from_anchor_id": "source",
        "to_anchor_id": "gap", "x1": .99, "y1": .99, "x2": 0, "y2": 0}


def beat(speech, marks, *, anchors=None, reliable=True, allow_final=True, answer="未知结果"):
    return normalized_teaching_beat({"speech": speech, "page": marks}, [0, 0, 1, 1],
                                    TARGET, answer, anchors or deepcopy(REGIONS), True,
                                    allow_final, reliable, allow_final)


def test_move_uses_current_region_edges_instead_of_model_endpoints():
    result = beat("把左侧向前伸出的部分搬到中间凹槽。", [MOVE])["page"]
    assert len(result) == 1
    arrow = result[0]
    assert arrow["purpose"] == "move" and arrow["label"] == "搬入示意"
    assert arrow["from_anchor_id"] == "source" and arrow["to_anchor_id"] == "gap"
    assert arrow["x1"] == pytest.approx(.3) and arrow["x2"] == pytest.approx(.5)
    assert .45 <= arrow["y1"] <= .57 and .35 <= arrow["y2"] <= .57
    assert arrow["anchor_text"] in "把左侧向前伸出的部分搬到中间凹槽。"


def test_region_aliases_keep_original_speech_cues_without_loosening_text_identity():
    speech = "圈出底层左端突出部分，再把底层左端突出部分移到中间缺口。"
    result = beat(speech, [{"kind": "circle", "anchor_id": "source"}, MOVE])["page"]
    assert [item["kind"] for item in result] == ["circle", "arrow"]
    assert all(item["anchor_text"] in speech for item in result)
    text = {**REGIONS[0], "kind": "text"}
    assert beat(speech, [{"kind": "underline", "anchor_id": "source"}], anchors=[text])["page"] == []


def test_explicit_spoken_region_circle_is_recovered_without_guessing_a_box():
    result = beat("先圈出底层左端突出部分。", [])["page"]
    assert result == [{"kind": "circle", "anchor_id": "source", "text": REGIONS[0]["text"],
                       "box": REGIONS[0]["box"], "anchor_text": "底层左端突出部分"}]


@pytest.mark.parametrize("side", ["左", "右"])
@pytest.mark.parametrize("action", ["搬入", "补进", "填到"])
def test_movement_resolves_supported_alias_in_its_own_operation_sentence(side, action):
    anchors = deepcopy(REGIONS)
    anchors[0]["text"] = f"{side}侧伸出的部分"
    speech = (f"看{side}侧伸出的部分，有两个小方块。把{side}侧伸出的这两个{action}中间的凹槽，"
              "先填下层：前面放一个，后面再放一个。")
    result = beat(speech, [MOVE], anchors=anchors)["page"]
    assert len(result) == 1 and result[0]["purpose"] == "move"
    assert f"{side}侧伸出的这两个{action}中间的凹槽" == result[0]["anchor_text"]


@pytest.mark.parametrize("speech", [
    "看左侧向前伸出的部分和中间凹槽。把这些搬入这里。",
    "先看左侧向前伸出的部分。再移入中间凹槽。",
    "把左侧向前伸出的部分搬走。中间凹槽等一下再处理。",
    "看左侧向前伸出的部分。把右端突出的这些搬入中间凹槽。",
    "看左侧向前伸出的部分。不要把左侧突出的这些搬入中间凹槽。",
])
def test_clause_local_cues_do_not_borrow_missing_references_or_override_polarity(speech):
    assert beat(speech, [MOVE])["page"] == []


@pytest.mark.parametrize("side,number", [("左", "2"), ("右", "两")])
def test_unique_named_source_can_be_referenced_by_same_side_and_count_in_action(side, number):
    anchors = deepcopy(REGIONS)
    anchors[0]["text"] = f"图形{side}侧伸出的部分"
    speech = (f"看图形{side}侧伸出的部分，有两个小正方体。"
              f"{side}侧{number}个搬入凹口底层，前后各放一个。")
    page = beat(speech, [MOVE], anchors=anchors)["page"]
    assert len(page) == 1 and page[0]["purpose"] == "move"
    assert page[0]["anchor_text"] == f"{side}侧{number}个搬入凹口底层"


@pytest.mark.parametrize("speech", [
    "左侧2个搬入凹口底层。",  # No named source/count introduction.
    "看左侧向前伸出的部分，有两个方块。左侧搬入凹口底层。",
    "看左侧向前伸出的部分，有两个方块。左侧3个搬入凹口底层。",
    "看左侧向前伸出的部分，有两个方块。右侧2个搬入凹口底层。",
    "看左侧向前伸出的部分，有两个方块。左侧2个搬入右侧缺口。",
    "看左侧向前伸出的部分，有两个方块。左侧2个搬入另一个缺口。",
    "看左侧向前伸出的部分，有两个方块。左侧2个搬入这里。",
    "不要搬左侧向前伸出的部分，有两个方块。左侧2个搬入凹口底层。",
    "看左侧向前伸出的部分，旁边的别的物体有两个。左侧2个搬入凹口底层。",
])
def test_short_side_count_reference_does_not_guess_identity_quantity_or_destination(speech):
    assert beat(speech, [MOVE])["page"] == []


@pytest.mark.parametrize("extra", [
    {"id": "other", "kind": "region", "text": "左边主体", "box": [.1, .7, .1, .1], "confidence": .96},
    {"id": "other", "kind": "region", "text": "后侧缺口", "box": [.6, .7, .1, .1], "confidence": .96},
])
def test_short_movement_reference_rejects_multiple_side_objects_or_openings(extra):
    speech = "看左侧向前伸出的部分，有两个方块。左侧2个搬入凹口底层。"
    assert beat(speech, [MOVE], anchors=[*deepcopy(REGIONS), extra])["page"] == []


def test_short_source_reference_cannot_grant_noanswer_or_geometry_permissions():
    speech = "看左侧向前伸出的部分，有两个方块。左侧2个搬入凹口底层。"
    assert beat(speech, [MOVE], allow_final=False)["page"] == []
    assert beat(speech, [{**MOVE, "purpose": None}])["page"] == []


@pytest.mark.parametrize("description,expected", [
    ("箭头表示从左侧搬到中间凹口。", "搬移方向是从左侧搬到中间凹口。"),
    ("看箭头，把左侧方块搬入中间凹口。", "把左侧方块搬入中间凹口。"),
    ("沿着这条箭头把左侧方块搬入中间凹口。", "把左侧方块搬入中间凹口。"),
    ("如图中箭头所示，把左侧方块搬入中间凹口。", "把左侧方块搬入中间凹口。"),
])
def test_rejected_arrow_cannot_leave_a_false_visual_instruction(description, expected):
    raw = {"speech": description, "page": [{**MOVE, "to_anchor_id": "missing"}]}
    result = normalized_teaching_beat(raw, TARGET, TARGET, "未知", REGIONS, True, True, True, True)
    assert result["page"] == [] and result["speech"] == expected


def test_valid_arrow_keeps_its_visual_reference_and_invalid_notes_are_rechecked():
    speech = "把左侧向前伸出的部分搬到中间凹槽。箭头表示这个搬移方向。"
    assert beat(speech, [MOVE])["speech"] == speech
    raw = {"speech": "看箭头，把左侧向前伸出的部分搬入中间凹槽。", "page": [
        {"kind": "note", "anchor_id": "source", "text": "看箭头，把左侧向前伸出的部分搬入中间凹槽"}]}
    result = normalized_teaching_beat(raw, TARGET, TARGET, "未知", REGIONS, True, True, True, True)
    assert "箭头" not in result["speech"] and result["page"] == []


def test_an_actual_scratch_arrow_keeps_its_visual_reference():
    raw = {"speech": "看箭头，把两处关系对应起来。", "scratch": [
        {"kind": "arrow", "x1": 10, "y1": 20, "x2": 70, "y2": 20}]}
    result = normalized_teaching_beat(raw, TARGET, TARGET, "未知", REGIONS, True, True, True, True)
    assert result["speech"] == raw["speech"] and result["scratch"][0]["kind"] == "arrow"


@pytest.mark.parametrize("speech", ["不要只看箭头，要比较数量。", "不能沿着箭头猜位置。",
                                    "如果看箭头，需要先知道它表示什么。"])
def test_visual_reference_consistency_does_not_rewrite_a_warning_or_hypothesis(speech):
    assert beat(speech, [])["speech"] == speech


@pytest.mark.parametrize("speech", [
    "不要把左侧向前伸出的部分搬到中间凹槽。",
    "如果把左侧向前伸出的部分搬到中间凹槽会怎样？",
    "请你把左侧向前伸出的部分搬到中间凹槽。",
    "左侧向前伸出的部分和中间凹槽形状不同。",
    "把中间凹槽移到左侧向前伸出的部分。",
    "把右边突出部分移到中间缺口。",
    "把这里移到那里。",
])
def test_move_requires_positive_current_source_action_and_destination(speech):
    assert beat(speech, [MOVE])["page"] == []


@pytest.mark.parametrize("change", [
    {"to_anchor_id": "source"}, {"to_anchor_id": "missing"}, {"confidence": .5},
    {"purpose": None}, {"kind": "line"},
])
def test_no_move_or_geometry_permission_from_invalid_region_reference(change):
    assert beat("把左侧向前伸出的部分搬到中间凹槽。", [{**MOVE, **change}])["page"] == []


def test_move_requires_reliable_solution_and_high_confidence_regions():
    assert beat("把左侧向前伸出的部分搬到中间凹槽。", [MOVE], reliable=False)["page"] == []
    anchors = deepcopy(REGIONS)
    anchors[1]["confidence"] = .8
    assert beat("把左侧向前伸出的部分搬到中间凹槽。", [MOVE], anchors=anchors)["page"] == []


def test_region_alias_ambiguity_and_opposite_direction_are_rejected():
    anchors = deepcopy(REGIONS) + [{**REGIONS[0], "id": "another", "text": "左边凸出的积木",
                                   "box": [.15, .65, .1, .1]}]
    assert beat("把左端突出部分移到中间缺口。", [MOVE], anchors=anchors)["page"] == []
    assert beat("圈出右边突出部分。", [{"kind": "circle", "anchor_id": "source"}])["page"] == []


def test_small_boundary_overlap_has_forward_interior_indicators():
    anchors = deepcopy(REGIONS)
    anchors[1]["box"] = [.297, .4, .12, .22]
    arrow = beat("把左侧向前伸出的部分搬到中间凹槽。", [MOVE], anchors=anchors)["page"][0]
    assert .15 <= arrow["x1"] <= .3
    assert .297 <= arrow["x2"] <= .417
    assert arrow["x2"] > arrow["x1"]


@pytest.mark.parametrize("box", [[.16, .46, .08, .08], [.2, .46, .15, .12], [.15, .45, .15, .12]])
def test_nested_or_heavily_overlapping_regions_cannot_imply_two_movement_locations(box):
    anchors = deepcopy(REGIONS)
    anchors[1]["box"] = box
    assert beat("把左侧向前伸出的部分搬到中间凹槽。", [MOVE], anchors=anchors)["page"] == []


def test_region_note_is_a_spoken_short_operation_or_checked_arithmetic_core():
    note = {"kind": "note", "anchor_id": "source", "text": "移入中间缺口"}
    page = beat("把底层左端突出部分移入中间缺口。", [note])["page"]
    assert page[0]["text"] == "移入中间缺口" and page[0]["box"] == REGIONS[0]["box"]
    formula = {"kind": "note", "anchor_id": "gap", "text": "检查：3×2＝6"}
    page = beat("中间凹槽补好以后，三乘二等于六。", [formula])["page"]
    assert page[0]["text"] == "3×2＝6" and page[0]["anchor_text"] == "三乘二等于六"


@pytest.mark.parametrize("speech,text,reliable", [
    ("把底层左端突出部分移入中间缺口。", "移入中间缺口", False),
    ("这里三乘二等于六。", "3×2＝6", True),
    ("中间凹槽补好以后，三乘二等于六。", "3×2＝7", True),
    ("中间凹槽这样补，因为体积保持不变所以能填满。", "因为体积保持不变所以能填满", True),
])
def test_region_notes_do_not_invent_operations_or_move_reasoning_onto_the_photo(speech, text, reliable):
    assert beat(speech, [{"kind": "note", "anchor_id": "gap", "text": text}],
                reliable=reliable)["page"] == []


def test_no_answer_permission_still_filters_region_note_and_voice():
    assert beat("中间凹槽是答案6。", [{"kind": "note", "anchor_id": "gap", "text": "3×2＝6"}],
                allow_final=False, answer="6") is None
    assert beat("中间凹槽先比较两层。", [{"kind": "note", "anchor_id": "gap", "text": "3×2＝6"}],
                allow_final=False, answer="6")["page"] == []


@pytest.mark.parametrize("question,allowed", [
    ("请完整讲解过程并给出结论和检查。", True),
    ("请完整讲解，但不要告诉我答案。", False),
    ("只给我一个提示。", False),
    ("请教一种方法，解释为什么这样做。", False),
])
def test_region_operations_follow_actual_request_scope_and_keep_condition_circle(question, allowed):
    raw = {"speech": "看左侧向前伸出的部分。把左侧向前伸出的部分移入中间凹槽。",
           "page": [{"kind": "circle", "anchor_id": "source"}, MOVE,
                    {"kind": "note", "anchor_id": "gap", "text": "移入中间凹槽"}],
           "scratch": []}
    target = {"label": "题目", "box": TARGET, "evidence": "图中积木搬移", "read_confidence": .96,
              "anchors": deepcopy(REGIONS)}
    solution = {"answer": "未知结果", "steps": "核对来源与目标，比较构造并检查。",
                "confidence": .96, "ambiguous": False}
    result = list(stream_teaching_beats(SimpleNamespace(ai_model="mock"), b"page", b"crop",
                                       TARGET, target, solution, "小学", 1, question=question,
                                       model_stream=lambda *_a: iter([json.dumps(raw) + "\n"])))
    kinds = [mark["kind"] for mark in result[0][1]["page"]]
    assert kinds == (["circle", "arrow", "note"] if allowed else ["circle"])


def test_region_operation_permission_is_explicit_and_cannot_be_inferred_from_model_purpose():
    raw = {"speech": "把左侧向前伸出的部分移入中间凹槽。", "page": [MOVE]}
    result = normalized_teaching_beat(raw, TARGET, TARGET, "未知结果", REGIONS, True, True, True)
    assert result["page"] == []  # All normal permissions still require the server's scope opt-in.
    assert beat(raw["speech"], [MOVE], allow_final=False)["page"] == []


@pytest.mark.parametrize("conclusion", [
    "至少需要移动4个小正方体。", "所以最少需要移动4个小正方体。",
    "至少需要移动四个小正方体。", "最少搬动四块。", "至少要挪动四块。",
    "最少是四个。", "最少为4块。", "最少等于四个。",
])
def test_noanswer_rejects_explicit_minimum_conclusion_in_speech_and_board(conclusion):
    assert reveals_final_answer(conclusion, "4")
    assert reveals_final_answer(conclusion, "四个")
    raw = {"speech": conclusion, "scratch": []}
    assert normalized_teaching_beat(raw, TARGET, TARGET, "4", allow_final_answer=False) is None
    raw = {"speech": "先比较不同的移动方案。", "scratch": [
        {"kind": "text", "role": "prose", "x": 2, "y": 2, "text": conclusion}]}
    assert normalized_teaching_beat(raw, TARGET, TARGET, "4", allow_final_answer=False) is None
    assert normalized_teaching_beat(raw, TARGET, TARGET, "4", allow_final_answer=True)


@pytest.mark.parametrize("condition", [
    "题目给了4块积木。", "已知有四个小正方体。", "题干说至少有4块积木。",
    "先保留4个已知方块。", "第4题先比较不同方案。", "试试把4分成两组。",
    "至少需要移动十四个。", "最少需要移动四十个。", "最少需要移动0.4个。",
    "至少需要移动负四个。",
])
def test_noanswer_does_not_ban_given_quantities_or_partial_number_matches(condition):
    assert not reveals_final_answer(condition, "4")
    assert normalized_teaching_beat({"speech": condition}, TARGET, TARGET, "4")
