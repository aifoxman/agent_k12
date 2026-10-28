"""Sentence-edge punctuation does not become a fake condition or a number alias."""

from __future__ import annotations

import pytest

from k12_server.ai import spoken_anchor_text
from k12_server.teaching_ink import normalized_teaching_beat


@pytest.mark.parametrize("anchor,speech,actual", [
    ("可以重复。", "再圈出“可以重复”，说明同一个数能再用。", "可以重复"),
    ("，可以重复；", "先看可以重复这个条件。", "可以重复"),
    ("一共有多少种方法？", "题目问“一共有多少种方法”，要找全。", "一共有多少种方法"),
    ("不算新的方法。", "读“不算新的方法”，不能因为换位置而重复计数。", "不算新的方法"),
    ("不能重复。", "先圈出“不能重复”这个限制。", "不能重复"),
    ("不能重复。", "不能重复。", "不能重复"),
    (".允许重复。.", "允许重复这个条件要读清。", "允许重复"),
    ("36，", "看三十六这个数。", "三十六"),
    ("1.5。", "标出一点五。", "一点五"),
    ("1.5%。", "这里是1.5%。", "1.5%"),
    ("2+3=5。", "二加三等于五。", "二加三等于五"),
    ("12.", "这里原样写着12.，照原文核对。", "12."),
])
def test_only_sentence_edge_punctuation_is_ignored_and_actual_spoken_span_is_returned(
        anchor, speech, actual):
    assert spoken_anchor_text(anchor, speech) == actual


@pytest.mark.parametrize("anchor,speech", [
    ("（0除外）。", "先读0除外这个限制。"),
    ("(0除外)，", "先读零除外这个限制。"),
    ("除外）。", "只说除外。"),
    ("相加，求总数。", "相加求总数。"),
    ("不算新的方法。", "这是新的方法。"),
    ("不能重复。", "可以重复。"),
    ("12。", "112。"),
    ("12。", "一百一十二。"),
    ("36，", "九百三十六。"),
    ("36，", "936。"),
    ("1.5。", "1.55。"),
    ("1.5。", "十一点五。"),
    ("12.", "数值是12.5。"),
    (".5", "数值是0.5。"),
    ("12.", "数值是12。"),
    ("1.5%。", "一点五。"),
    ("1.5+2.5。", "一点五减二点五。"),
    ("！？。", "这是一个条件。"),
])
def test_trimming_never_removes_brackets_internal_punctuation_math_or_value_boundaries(anchor, speech):
    assert spoken_anchor_text(anchor, speech) is None


@pytest.mark.parametrize("kind", ["circle", "underline", "check"])
def test_prepared_word_mark_keeps_original_text_box_and_only_changes_spoken_cue(kind):
    anchor = {"id": "condition", "text": "可以重复。", "role": "condition",
              "confidence": .98, "box": [.2, .3, .16, .04]}
    original = {**anchor, "box": list(anchor["box"])}
    beat = normalized_teaching_beat(
        {"speech": "圈出“可以重复”，说明同一个数能再用。",
         "page": [{"kind": kind, "anchor_id": anchor["id"]}]},
        [0, 0, 1, 1], [.1, .1, .8, .7], "", [anchor], True)
    assert anchor == original
    assert beat["page"][0]["text"] == anchor["text"]
    assert beat["page"][0]["box"] == anchor["box"]
    assert beat["page"][0]["anchor_text"] == "可以重复"


def test_omitted_circle_is_not_restored_from_a_negated_instruction():
    anchor = {"id": "condition", "text": "可以重复。", "confidence": .98,
              "box": [.2, .3, .16, .04]}
    beat = normalized_teaching_beat(
        {"speech": "不要圈出“可以重复”，先核对完整条件。", "page": []},
        [0, 0, 1, 1], [.1, .1, .8, .7], "", [anchor], True)
    assert beat["page"] == []


def test_missing_mark_id_can_only_match_one_actual_word_position_after_sentence_punctuation():
    anchor = {"id": "condition", "text": "可以重复。", "confidence": .98,
              "box": [.2, .3, .16, .04]}
    raw = {"speech": "圈出“可以重复”这个条件。", "page": [{"kind": "circle", "text": "可以重复"}]}
    beat = normalized_teaching_beat(raw, [0, 0, 1, 1], [.1, .1, .8, .7], "", [anchor], True)
    assert beat["page"][0]["anchor_id"] == "condition"
    other = {**anchor, "id": "other", "text": "可以重复", "box": [.5, .5, .16, .04]}
    assert normalized_teaching_beat(raw, [0, 0, 1, 1], [.1, .1, .8, .7], "", [anchor, other], True)["page"] == []


@pytest.mark.parametrize("page", [[], [{"kind": "circle", "text": "12"}]])
def test_terminal_numeric_dot_cannot_hide_a_second_potential_word_location(page):
    anchors = [{"id": "number", "text": "12", "box": [.2, .2, .1, .03], "confidence": .97},
               {"id": "number-dot", "text": "12.", "box": [.6, .5, .1, .03], "confidence": .97}]
    beat = normalized_teaching_beat(
        {"speech": "圈出12这个数。", "page": page}, [0, 0, 1, 1], [.1, .1, .8, .7], "", anchors, True)
    assert beat["page"] == []
    # This only removes an automatic guess: explicit current-photo references
    # and the strict decimal matching contract keep their original meaning.
    explicit = normalized_teaching_beat(
        {"speech": "圈出12这个数。", "page": [{"kind": "circle", "anchor_id": "number"}]},
        [0, 0, 1, 1], [.1, .1, .8, .7], "", anchors, True)
    assert explicit["page"][0]["anchor_id"] == "number"
    assert spoken_anchor_text("12.", "12.5") is None
