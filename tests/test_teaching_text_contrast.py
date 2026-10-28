"""Dim paper must not become ink, while clipped/unsupported words stay rejected."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from k12_server.ai import verify_teaching_anchors

SIZE = (640, 400)
TARGET = [.05, .05, .9, .9]


def encoded(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def word_on_paper(background=125, ink=30, *, gradient=False):
    picture = Image.new("L", SIZE, background)
    if gradient:
        canvas = ImageDraw.Draw(picture)
        for x in range(SIZE[0]):
            canvas.line((x, 0, x, SIZE[1]), fill=65 + round(x * 100 / SIZE[0]))
    canvas, font = ImageDraw.Draw(picture), ImageFont.load_default(size=30)
    left, top, right, bottom = canvas.textbbox((220, 160), "nonzero", font=font)
    canvas.text((220, 160), "nonzero", font=font, fill=ink)
    anchor = {"id": "word", "kind": "text", "text": "nonzero", "confidence": .97,
              "box": [(left - 3) / SIZE[0], (top - 3) / SIZE[1],
                      (right - left + 6) / SIZE[0], (bottom - top + 6) / SIZE[1]]}
    return picture, anchor


@pytest.mark.parametrize("background,ink", [(255, 0), (170, 45), (125, 30), (85, 10)])
def test_legible_strokes_on_white_and_dim_paper_keep_the_exact_original_box(background, ink):
    picture, anchor = word_on_paper(background, ink)
    accepted = verify_teaching_anchors(encoded(picture), [anchor], TARGET, "nonzero")
    assert len(accepted) == 1
    assert accepted[0]["confidence"] == anchor["confidence"]
    assert accepted[0]["box"] == pytest.approx(anchor["box"], abs=1e-6)


def test_a_smooth_local_shadow_does_not_turn_into_border_ink_or_move_the_word():
    picture, anchor = word_on_paper(gradient=True, ink=20)
    accepted = verify_teaching_anchors(encoded(picture), [anchor], TARGET, "nonzero")
    assert accepted and accepted[0]["box"] == pytest.approx(anchor["box"], abs=1e-6)


@pytest.mark.parametrize("background", [255, 170, 125, 85])
def test_flat_empty_paper_cannot_validate_a_word_even_when_every_pixel_is_below180(background):
    picture, anchor = word_on_paper(background)
    picture = Image.new("L", SIZE, background)
    assert verify_teaching_anchors(encoded(picture), [anchor], TARGET, "nonzero") == []


def test_smooth_shadow_without_letters_and_low_contrast_letters_are_not_supported():
    picture, anchor = word_on_paper(gradient=True)
    canvas = ImageDraw.Draw(picture)
    for x in range(SIZE[0]):
        canvas.line((x, 0, x, SIZE[1]), fill=65 + round(x * 100 / SIZE[0]))
    assert verify_teaching_anchors(encoded(picture), [anchor], TARGET, "nonzero") == []
    faint, anchor = word_on_paper(125, 116)
    assert verify_teaching_anchors(encoded(faint), [anchor], TARGET, "nonzero") == []


@pytest.mark.parametrize("background", [255, 125])
def test_strong_text_strokes_cut_by_a_model_box_are_still_rejected(background):
    picture, anchor = word_on_paper(background)
    x, y, w, h = anchor["box"]
    shifted = {**anchor, "box": [x + .03, y + .015, w, h]}
    assert verify_teaching_anchors(encoded(picture), [shifted], TARGET, "nonzero") == []


@pytest.mark.parametrize("cross_border", [False, True])
def test_a_notebook_rule_cannot_impersonate_a_word(cross_border):
    picture = Image.new("L", SIZE, 125)
    canvas = ImageDraw.Draw(picture)
    anchor = {"id": "word", "text": "nonzero", "confidence": .97,
              "box": [200 / 640, 150 / 400, 130 / 640, 30 / 400]}
    ends = (190, 165, 340, 165) if cross_border else (210, 165, 320, 165)
    canvas.line(ends, fill=20, width=2)
    assert verify_teaching_anchors(encoded(picture), [anchor], TARGET, "nonzero") == []


def test_true_horizontal_chinese_glyphs_survive_on_dim_paper():
    picture = Image.new("L", SIZE, 125)
    canvas = ImageDraw.Draw(picture)
    canvas.line((220, 165, 245, 165), fill=20, width=2)
    anchor = {"id": "word", "text": "一", "confidence": .97,
              "box": [215 / 640, 156 / 400, 36 / 640, 20 / 400]}
    assert verify_teaching_anchors(encoded(picture), [anchor], TARGET, "一")


def test_relative_contrast_does_not_lower_confidence_or_change_literal_identity_guards():
    picture, anchor = word_on_paper()
    assert verify_teaching_anchors(encoded(picture), [{**anchor, "confidence": .7}], TARGET, "nonzero") == []
    assert verify_teaching_anchors(encoded(picture), [anchor], TARGET, "other condition") == []
