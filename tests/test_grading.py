"""A full page remains the parent screenshot while dense text gets enlarged for analysis."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from k12_server.grading import (
    correction_regions,
    focus_question_image,
    map_hint_cue,
    merge_correction_regions,
)


def test_portrait_page_is_checked_in_two_zoomed_regions():
    image = Image.new("RGB", (600, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 580, 100), fill="red")
    draw.rectangle((20, 800, 580, 880), fill="blue")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    regions = correction_regions(buffer.getvalue())
    assert [name for name, _, _ in regions] == ["上半页", "下半页"]
    assert regions[0][1] == (0, 0, 1, .55)
    assert regions[1][1] == (0, .45, 1, .55)
    with Image.open(io.BytesIO(regions[0][2])) as top:
        assert top.width >= 1100
        assert top.getpixel((50, 50))[0] > 150
    with Image.open(io.BytesIO(regions[1][2])) as bottom:
        assert bottom.width >= 1100
        assert bottom.getpixel((50, bottom.height - 50))[2] > 150


def test_readable_page_with_one_tall_diagram_stays_whole():
    image = io.BytesIO()
    Image.new("RGB", (1011, 1320), "white").save(image, format="JPEG")
    regions = correction_regions(image.getvalue())
    assert len(regions) == 1
    assert regions[0][1] == (0, 0, 1, 1)


def test_focused_recheck_crop_keeps_original_page_separate():
    image = io.BytesIO()
    Image.new("RGB", (1200, 800), "white").save(image, format="JPEG")
    focused, box = focus_question_image(image.getvalue(), [.4, .35, .12, .08])
    assert focused != image.getvalue()
    assert box[0] <= .4 and box[1] <= .35
    assert box[0] + box[2] >= .52 and box[1] + box[3] >= .43
    assert box[2] < 1 and box[3] < 1
    _, long_answer_box = focus_question_image(image.getvalue(), [.4, .2, .08, .13])
    assert long_answer_box[0] + long_answer_box[2] >= .68


def test_teaching_cue_maps_to_saved_page_only_inside_located_question():
    cue = {"kind": "underline", "box": [.5, .5, .1, .05], "label": "已知条件"}
    focus = [.2, .1, .6, .5]
    target = [.44, .3, .2, .12]
    mapped = map_hint_cue(cue, focus, target)
    assert mapped == {"kind": "underline", "box": [.5, .35, .06, .025],
                      "label": "已知条件"}
    assert map_hint_cue({**cue, "box": [.05, .05, .1, .05]}, focus, target) is None


def test_conflicting_overlap_is_unclear_and_marks_use_full_page_coordinates():
    first = {"checks": [{"question": "第3题", "status": "looks_correct", "answer": "十",
                         "box": [.2, .86, .12, .06], "confidence": .9, "steps": [],
                         "reference_answer": "十", "solution_steps": ["先核对字形"],
                         "diagram": {"caption": "字形", "elements": []}}],
             "visible_answer_count": 1, "confidence": .9}
    second = {"checks": [{"question": "第3题", "status": "check_again", "answer": "田",
                          "box": [.2, .08, .12, .06], "confidence": .9, "steps": []}],
              "visible_answer_count": 1, "confidence": .9}
    result = merge_correction_regions([
        ((0, 0, 1, .55), first),
        ((0, .45, 1, .55), second),
    ])
    assert len(result["checks"]) == 1
    assert result["checks"][0]["status"] == "unclear"
    assert result["checks"][0]["box"] is None
    assert result["checks"][0]["reference_answer"] == ""
    assert result["checks"][0]["diagram"] is None
    assert result["visible_answer_count"] == 1


def test_opposite_verdicts_on_the_same_area_are_not_shown_as_confident_marks():
    result = merge_correction_regions([((0, 0, 1, 1), {"confidence": .9, "checks": [
        {"question": "第 1 项", "status": "looks_correct", "box": [.2, .2, .2, .1],
         "reason": "初判正确", "confidence": .9},
        {"question": "第 2 项", "status": "check_again", "box": [.21, .21, .19, .09],
         "reason": "初判有误", "confidence": .9},
    ]})])
    assert [item["status"] for item in result["checks"]] == ["unclear", "unclear"]
    assert all("高度重叠" in item["reason"] for item in result["checks"])


def test_incomplete_crop_is_removed_only_with_unique_printed_and_spatial_proof():
    complete = {"question": "3", "status": "check_again", "answer": "", "box": [.2, .89, .2, .05],
                "_question_complete": True, "_question_box": [.1, .78, .8, .15],
                "_visible_question_text": "十位是9，百位和个位是5。A.559 B.595 C.955", "steps": []}
    fragment = {"question": "top fragment", "status": "unclear", "answer": "", "box": None,
                "_question_complete": False, "_question_box": [.1, 0, .8, .1],
                "_visible_question_text": "A.559 B.595 C.955", "steps": []}
    def merged(part, other=complete):
        return merge_correction_regions([
            ((0, 0, 1, .55), {"checks": [other], "visible_answer_count": 1, "confidence": .9}),
            ((0, .45, 1, .55), {"checks": [part], "visible_answer_count": 1, "confidence": .9}),
        ])
    result = merged(fragment)
    assert len(result["checks"]) == result["visible_answer_count"] == 1
    assert not any(key.startswith("_") for key in result["checks"][0])
    assert len(merged({**fragment, "_question_box": None})["checks"]) == 2
    assert len(merged({**fragment, "_visible_question_text": "A.111 B.222 C.333"})["checks"]) == 2
    assert len(merged({**fragment, "_question_box": [.1, .6, .8, .1]})["checks"]) == 2
    assert len(merged({**fragment, "answer": "different visible answer"})["checks"]) == 2
    assert len(merged(fragment, {**complete, "_question_complete": False})["checks"]) == 2
    # A legible one-line crop fragment can be shorter than the minimum
    # handwritten-answer box once mapped back to the full page.
    thin = {**fragment, "_question_box": [.1, 0, .8, .025]}
    small_complete = {**complete, "_question_box": [.1, .742, .8, .099]}
    assert len(merged(thin, small_complete)["checks"]) == 1
