"""Pixel evidence supplements model coordinates without inventing question geometry."""

from __future__ import annotations

import io
import math

import pytest
from PIL import Image, ImageDraw, ImageFont

from k12_server.ai import tight_teaching_anchor_image, verify_teaching_anchors

SIZE = (640, 480)
TARGET = [.1, .05, .8, .8]


def picture(draw=None):
    image = Image.new("RGB", SIZE, "white")
    if draw:
        draw(ImageDraw.Draw(image))
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def segment(start, end, identifier="edge"):
    return {"id": identifier, "kind": "segment", "confidence": .97,
            "x1": start[0] / SIZE[0], "y1": start[1] / SIZE[1],
            "x2": end[0] / SIZE[0], "y2": end[1] / SIZE[1]}


def point(center, identifier="point"):
    return {"id": identifier, "kind": "point", "text": "可见端点", "confidence": .97,
            "box": [(center[0] - 7) / SIZE[0], (center[1] - 7) / SIZE[1],
                    14 / SIZE[0], 14 / SIZE[1]]}


def test_tight_crop_preserves_exact_pixel_inverse_mapping_and_drops_large_empty_margins():
    target = [.421, .283, .224, .237]
    crop, region = tight_teaching_anchor_image(picture(), target)
    x, y, w, h = target
    left = math.floor(x * SIZE[0] - w * SIZE[0] * .08)
    top = math.floor(y * SIZE[1] - h * SIZE[1] * .08)
    right = math.ceil((x + w) * SIZE[0] + w * SIZE[0] * .08)
    bottom = math.ceil((y + h) * SIZE[1] + h * SIZE[1] * .08)
    assert region == [left / SIZE[0], top / SIZE[1],
                      (right - left) / SIZE[0], (bottom - top) / SIZE[1]]
    with Image.open(io.BytesIO(crop)) as output:
        assert max(output.size) <= 1200
        assert output.width > right - left
    # A point measured in the crop maps to the same full-photo pixel, without rounded region drift.
    local_x, local_y = .3, .7
    assert (region[0] + local_x * region[2]) * SIZE[0] == pytest.approx(left + local_x * (right - left))
    assert (region[1] + local_y * region[3]) * SIZE[1] == pytest.approx(top + local_y * (bottom - top))


def test_text_ink_does_not_move_a_named_word_onto_a_nearby_component():
    font = ImageFont.load_default(size=40)
    expected = []

    def draw(canvas):
        expected.extend(canvas.textbbox((270, 190), "AB", font=font))
        canvas.text((270, 190), "AB", font=font, fill="black")
        canvas.line((240, 140, 240, 290), fill="black", width=3)

    image = picture(draw)
    left, top, right, bottom = expected
    anchor = {"id": "word", "text": "AB", "confidence": .97,
              "box": [(left + 14) / SIZE[0], (top + 9) / SIZE[1],
                      (right - left + 6) / SIZE[0], (bottom - top + 4) / SIZE[1]]}
    result = verify_teaching_anchors(image, [anchor], TARGET, "AB是题目中可见的文字")
    assert result == []
    original = {**anchor, "box": [(left - 2) / SIZE[0], (top - 2) / SIZE[1],
                                  (right - left + 4) / SIZE[0], (bottom - top + 4) / SIZE[1]]}
    retained = verify_teaching_anchors(image, [original], TARGET, "AB")
    assert retained[0]["text"] == "AB"
    assert retained[0]["box"] == pytest.approx(original["box"], abs=.000001)


def test_a_shifted_numeric_label_cannot_snap_onto_a_different_visible_number():
    font = ImageFont.load_default(size=30)
    bounds = []

    def draw(canvas):
        bounds.extend(canvas.textbbox((280, 190), "84", font=font))
        canvas.text((280, 190), "84", font=font, fill="black")

    image = picture(draw)
    left, top, right, bottom = bounds
    anchor = {"id": "word", "text": "21", "confidence": .97,
              "box": [(left + 12) / SIZE[0], (top + 8) / SIZE[1],
                      (right - left + 4) / SIZE[0], (bottom - top + 3) / SIZE[1]]}
    assert verify_teaching_anchors(image, [anchor], TARGET, "题干含21与84") == []


def test_a_real_slanted_stroke_is_refined_without_forcing_an_axis_or_constructing_a_new_line():
    start, end = (150, 90), (450, 190)
    image = picture(lambda draw: draw.line((*start, *end), fill="black", width=3))
    guessed = segment((164, 100), (464, 200))
    result = verify_teaching_anchors(image, [guessed], TARGET, "")
    assert len(result) == 1 and result[0]["id"] == "edge"
    line = result[0]
    slope = (line["y2"] - line["y1"]) * SIZE[1] / ((line["x2"] - line["x1"]) * SIZE[0])
    assert slope == pytest.approx(1 / 3, abs=.001)
    for x, y in [(line["x1"] * SIZE[0], line["y1"] * SIZE[1]),
                 (line["x2"] * SIZE[0], line["y2"] * SIZE[1])]:
        assert abs((y - 90) - (x - 150) / 3) < 3


def test_two_equally_plausible_parallel_strokes_are_not_arbitrarily_chosen():
    image = picture(lambda draw: (
        draw.line((140, 130, 470, 130), fill="black", width=3),
        draw.line((140, 150, 470, 150), fill="black", width=3)))
    assert verify_teaching_anchors(image, [segment((140, 140), (470, 140))], TARGET, "") == []


def test_blank_photo_cannot_validate_a_model_word_point_or_line():
    anchors = [segment((120, 120), (420, 120)), point((120, 120)),
               {"id": "word", "text": "条件", "confidence": .97, "box": [.3, .3, .1, .1]}]
    assert verify_teaching_anchors(picture(), anchors, TARGET, "条件") == []
    assert verify_teaching_anchors(b"not an image", anchors, TARGET, "条件") == []


def test_condition_with_a_short_horizontal_glyph_keeps_both_parts_not_only_the_tall_glyph():
    def draw(canvas):
        canvas.line((250, 210, 274, 210), fill="black", width=2)
        canvas.line((293, 195, 293, 225), fill="black", width=2)
        canvas.line((281, 204, 305, 204), fill="black", width=2)
        canvas.line((281, 214, 305, 214), fill="black", width=2)

    anchor = {"id": "word", "text": "一半", "confidence": .97,
              "box": [248 / 640, 190 / 480, 64 / 640, 40 / 480]}
    result = verify_teaching_anchors(picture(draw), [anchor], TARGET, "一半")
    assert len(result) == 1
    assert result[0]["box"][0] * SIZE[0] <= 250
    assert (result[0]["box"][0] + result[0]["box"][2]) * SIZE[0] >= 305


@pytest.mark.parametrize(("word", "rows", "box"), [
    ("一", [210], [248, 202, 34, 16]), ("二", [205, 215], [248, 199, 34, 22]),
])
def test_isolated_chinese_horizontal_strokes_are_not_deleted_by_a_glyph_height_heuristic(word, rows, box):
    image = picture(lambda draw: [draw.line((250, row, 278, row), fill="black", width=2) for row in rows])
    anchor = {"id": "word", "text": word, "confidence": .97,
              "box": [box[0] / 640, box[1] / 480, box[2] / 640, box[3] / 480]}
    retained = verify_teaching_anchors(image, [anchor], TARGET, word)
    assert retained[0]["box"] == pytest.approx(anchor["box"], abs=.000001)


def test_two_collinear_short_segments_are_not_chosen_by_tangential_translation():
    image = picture(lambda draw: (
        draw.line((170, 180, 230, 180), fill="black", width=3),
        draw.line((240, 180, 300, 180), fill="black", width=3)))
    assert verify_teaching_anchors(image, [segment((205, 180), (265, 180))], TARGET, "") == []


def test_visible_end_dots_do_not_authorize_a_line_where_no_stroke_exists():
    image = picture(lambda draw: (
        draw.ellipse((147, 97, 153, 103), fill="black"),
        draw.ellipse((447, 197, 453, 203), fill="black")))
    assert verify_teaching_anchors(image, [segment((150, 100), (450, 200))], TARGET, "") == []


def test_point_must_have_visible_junction_evidence_not_a_mathematically_predicted_position():
    image = picture(lambda draw: (
        draw.line((180, 140, 360, 140), fill="black", width=3),
        draw.line((180, 140, 180, 300), fill="black", width=3)))
    anchors = [point((180, 140), "corner"), point((270, 140), "middle"), point((270, 220), "computed")]
    assert [item["id"] for item in verify_teaching_anchors(image, anchors, TARGET, "")] == ["corner"]


def test_already_aligned_reversed_edge_keeps_its_direction():
    image = picture(lambda draw: draw.line((150, 100, 450, 200), fill="black", width=3))
    anchor = segment((450, 200), (150, 100))
    result = verify_teaching_anchors(image, [anchor], TARGET, "")
    assert result[0]["x1"] > result[0]["x2"] and result[0]["y1"] > result[0]["y2"]


@pytest.mark.parametrize("guessed", [
    ((150, 90), (450, 198)),
    ((154, 96), (454, 204)),
    ((450, 198), (150, 90)),
])
def test_small_direction_errors_fit_the_unique_visible_slanted_stroke(guessed):
    image = picture(lambda draw: draw.line((150, 90, 450, 190), fill="black", width=3))
    result = verify_teaching_anchors(image, [segment(*guessed)], TARGET, "")
    assert len(result) == 1
    line = result[0]
    assert (line["x2"] > line["x1"]) == (guessed[1][0] > guessed[0][0])
    for suffix in ("1", "2"):
        x, y = line["x" + suffix] * SIZE[0], line["y" + suffix] * SIZE[1]
        assert 148 <= x <= 453
        assert abs(y - (90 + (x - 150) / 3)) < 2


def test_angular_fit_rejects_two_equally_supported_nearby_slanted_lines():
    image = picture(lambda draw: (
        draw.line((150, 90, 450, 190), fill="black", width=3),
        draw.line((150, 110, 450, 210), fill="black", width=3)))
    assert verify_teaching_anchors(image, [segment((150, 100), (450, 208))], TARGET, "") == []


def test_angular_fit_does_not_bridge_an_unsupported_gap_in_a_predicted_edge():
    image = picture(lambda draw: (
        draw.line((150, 90, 285, 135), fill="black", width=3),
        draw.line((315, 145, 450, 190), fill="black", width=3)))
    assert verify_teaching_anchors(image, [segment((150, 90), (450, 198))], TARGET, "") == []


def test_direction_correction_stays_local_in_angle_and_search_distance():
    image = picture(lambda draw: draw.line((150, 90, 450, 190), fill="black", width=3))
    assert verify_teaching_anchors(image, [segment((150, 70), (450, 220))], TARGET, "") == []
    assert verify_teaching_anchors(image, [segment((150, 140), (450, 248))], TARGET, "") == []
