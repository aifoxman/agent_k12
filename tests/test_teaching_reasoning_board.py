"""Restore current spoken reasons, without changing a provider's normalized speech."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from k12_server import teaching_ink

BOX = [0.05, 0.05, 0.85, 0.4]
TARGET = {
    "found": True,
    "label": "当前题目",
    "box": BOX,
    "evidence": "按已知条件分类并说明理由",
    "read_confidence": 0.9,
    "anchors": [],
}
SOLUTION = {"answer": "99", "steps": "依据条件分类并检查", "confidence": 0.96}

# These are the five SAVED NORMALIZED speeches of the 613-character failure.
# Reconstructing JSONL below is a local fixture, not the provider's missing raw output.
SAVED_READING_SPEECH = [
    "第4题的关键条件是“把7拆分成几个数（0除外）相加的形式”。意思是：拆出来的数合起来仍然是7，每个数都不能是0。这张照片上我暂不直接落笔圈词，你可以对照原题，在“7”下面画线，圈出“0除外”；这两处是每写一种拆法都要检查的条件。",
    "再给问题“一共有多少种不同的拆分方法”画线。它要我们找齐不同拆法，不是只写一道加法。这里按拆数游戏的通常约定：同一组数只换前后顺序，不算新方法。所以每种拆法都把小数写在前面、大数写在后面；一样大的数可以挨着写，题目没有要求各个数必须不同。",
    "右侧“小提示”已经给了分类办法：分别找拆成2个数、3个数、4个数、5个数、6个数和7个数的情形。你可以先按这些类别分行写，每一类留些空位。不同类别的加数个数不同，不会重复；把提示里的类别依次找完，就不会漏掉某一种加数个数。",
    "每一类里面，也按顺序试。先让前面的数从最小的非零整数开始，最后一个数用来补足总数。找两个数时，让前一个数逐渐增加；找更多个数时，先固定第一个数，再依次试后面的数，每个数都不能比它前面的数小。如果剩下的数已经不够照这个要求填写，就退回前一个位置，把那个数增大再试。这样能按次序找，而不用随意猜。",
    "写完一类，检查有没有0、合起来是不是7、是不是从小到大写的；再看看试过的数有没有跳过去。全部类别检查完，再数每类有几种，把各类的种数合起来，才是题目要的总种数。现在你可以先写“拆成2个数”这一类，按前一个数从小到大的顺序开始找，不急着写最后答案。",
]


def raw(speech, **change):
    return {
        "speech": speech,
        "page": [],
        "scratch": [],
        "teaching_move": "give_clue",
        "reply_mode": "none",
        "needs_parent": False,
        "step_id": 1,
        **change,
    }


def stream(
    beats,
    *,
    target=None,
    level=1,
    question="请读题并说明方法，先不要答案。",
    allow_diagram=False,
    newline=True,
):
    text = "\n".join(json.dumps(beat, ensure_ascii=False) for beat in beats) + (
        "\n" if newline else ""
    )
    return [
        value
        for kind, value in teaching_ink.stream_teaching_beats(
            SimpleNamespace(ai_model="unit-test-model"),
            b"page",
            b"crop",
            [0, 0, 1, 1],
            deepcopy(target or TARGET),
            SOLUTION,
            "三年级",
            level,
            question=question,
            allow_diagram=allow_diagram,
            model_stream=lambda *_args: iter([text]),
        )
        if kind == "beat"
    ]


@pytest.mark.parametrize("newline", [True, False])
def test_saved_613_character_failure_local_replay_recovers_reasons_without_rewriting_it(newline):
    assert sum(map(len, SAVED_READING_SPEECH)) == 613
    inputs = [
        raw(
            speech,
            step_id=[1, 1, 2, 2, 3][index],
            step_end=index in {1, 3, 4},
            teaching_move="wait_for_writing" if index == 4 else "give_clue",
            reply_mode="writing" if index == 4 else "none",
        )
        for index, speech in enumerate(SAVED_READING_SPEECH)
    ]
    snapshot = deepcopy(inputs)
    result = stream(inputs, newline=newline)
    assert len(result) == 5
    assert [beat["speech"] for beat in result] == SAVED_READING_SPEECH
    assert [beat["step_id"] for beat in result] == [1, 1, 2, 2, 3]
    assert all(beat["scratch"] and beat["page"] == [] for beat in result)
    for beat in result:
        assert len(beat["scratch"]) == 1
        note = beat["scratch"][0]
        assert note["kind"] == "text" and note["role"] == "prose"
        assert note["text"].removeprefix("当前一步：") in beat["speech"]
    assert result[-1]["teaching_move"] == "wait_for_writing"
    assert result[-1]["reply_mode"] == "writing"
    assert inputs == snapshot


@pytest.mark.parametrize(
    "level,question,allow_diagram",
    [
        (1, "请读题并说明一种方法。", False),
        (2, "请完整讲解。", True),
        (2, "请完整讲解，但不要答案。", True),
    ],
)
def test_both_teaching_scopes_keep_the_current_spoken_reason(level, question, allow_diagram):
    speech = "因为两处比较的是同一种量，所以先把单位对齐，再比较剩下的部分。"
    result = stream([raw(speech)], level=level, question=question, allow_diagram=allow_diagram)
    assert result[0]["speech"] == speech
    assert result[0]["scratch"][0]["text"].removeprefix("当前一步：") in speech


@pytest.mark.parametrize(
    "speech",
    [
        "题目要求把7拆成几个数，0除外。",
        "图中有两个相同的方块。",
        "现在请你先写一种，再自己试试。",
        "先写一种，再继续试。",
        "请把题目按个数分类。",
        "为什么先分类，再计算？",
    ],
)
def test_reading_only_questions_and_bare_handoffs_do_not_create_reasoning_notes(speech):
    result = stream([raw(speech)])
    assert result and result[0]["scratch"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"read_confidence": 0.84},
        {"read_confidence": None},
        {"read_confidence": True},
        {"evidence": ""},
        {"found": False},
    ],
)
def test_an_unknown_or_insufficiently_read_target_cannot_acquire_a_reasoning_board(change):
    result = stream([raw("因为条件相同，所以先比较它们。")], target={**TARGET, **change})
    assert result and result[0]["scratch"] == []


@pytest.mark.parametrize(
    "move,parent", [("clarify_target", False), ("parent_handoff", True), ("give_clue", True)]
)
def test_target_clarification_or_parent_handoff_does_not_restore_a_note(move, parent):
    result = stream(
        [raw("因为还需要核对关系，所以请先确认题目。", teaching_move=move, needs_parent=parent)]
    )
    assert result and result[0]["scratch"] == []


def test_uncertainty_and_an_explicit_independent_attempt_do_not_gain_a_board():
    assert stream([raw("看不清题目，因为条件未核对，所以先确认。")])[0]["scratch"] == []
    assert (
        stream(
            [raw("因为你想先自己试，所以先停在这里。")],
            question="我先自己试试。",
            allow_diagram=True,
        )[0]["scratch"]
        == []
    )


@pytest.mark.parametrize("level,question", [(1, "请说明方法。"), (2, "请完整讲解，但不要答案。")])
def test_recovery_never_resurrects_speech_rejected_by_the_noanswer_gate(level, question):
    assert (
        stream(
            [raw("因为条件已经对应，所以答案是99。")],
            level=level,
            question=question,
            allow_diagram=True,
        )
        == []
    )


def test_reason_extract_must_remain_literal_and_pass_the_answer_filter(monkeypatch):
    beat = raw("因为要对齐单位，所以先检查。")
    for invented in ["新推导：3+4=7", "所以答案是99"]:
        monkeypatch.setattr(
            teaching_ink,
            "step_board",
            lambda *_args, text=invented: [
                {"kind": "text", "role": "prose", "x": 8, "y": 12, "text": text}
            ],
        )
        assert (
            teaching_ink._spoken_reasoning_board(
                beat, TARGET, "explain_method", "99", False, raw(beat["speech"])
            )["scratch"]
            == []
        )


def test_existing_accepted_board_and_independent_normalizer_empty_semantics_are_unchanged():
    speech = "因为单位相同，所以可以直接比较。"
    note = {"kind": "text", "role": "prose", "x": 3, "y": 3, "text": "单位相同才比较"}
    result = stream([raw(speech, scratch=[note])], allow_diagram=True)
    assert result[0]["scratch"] == [note]
    normalized = teaching_ink.normalized_teaching_beat(raw(speech), [0, 0, 1, 1], BOX, "99")
    assert normalized["scratch"] == []


@pytest.mark.parametrize("scratch", ["missing", None, []])
def test_only_a_genuinely_omitted_board_can_be_recovered(scratch):
    beat = raw("因为条件对应，所以先比较已知部分。")
    if scratch == "missing":
        beat.pop("scratch")
    else:
        beat["scratch"] = scratch
    result = stream([beat])
    assert result[0]["scratch"][0]["role"] == "prose"


@pytest.mark.parametrize(
    "problem",
    [
        "uncited",
        "invented_basis",
        "unlabelled",
        "unsupported_prose",
        "invalid_item",
        "invalid_schema",
    ],
)
@pytest.mark.parametrize("newline", [True, False])
def test_rejected_nonempty_provider_board_is_never_replaced_with_recovered_prose(problem, newline):
    beat = raw(
        "这是局部示意图，表示整体平均分成两份。",
        scratch=[
            {"kind": "text", "role": "label", "x": 20, "y": 10, "text": "示意图"},
            {"kind": "rect", "x": 20, "y": 20, "w": 60, "h": 30},
        ],
    )
    if problem == "invented_basis":
        beat["scratch_basis"] = ["整体平均分成五份"]
    elif problem == "unlabelled":
        beat["scratch"] = beat["scratch"][1:]
        beat["scratch_basis"] = ["整体平均分成两份"]
    elif problem == "unsupported_prose":
        beat["scratch"] = [
            {"kind": "text", "role": "prose", "x": 2, "y": 2, "text": "整体平均分成五份"}
        ]
    elif problem == "invalid_item":
        beat["scratch"] = [None]
    elif problem == "invalid_schema":
        beat["scratch"] = {"text": "unaccepted board schema"}
    result = stream([beat], target={**TARGET, "evidence": "整体平均分成两份。"}, newline=newline)
    assert result[0]["scratch"] == []
    assert result[0]["speech"] == beat["speech"]


def test_telemetry_records_only_counts_not_the_actual_reason(monkeypatch):
    records = []
    monkeypatch.setattr(
        teaching_ink, "teaching_stage", lambda stage, **data: records.append((stage, data))
    )
    stream([raw("因为条件相同，所以先比较剩余部分。")])
    record = next(data for stage, data in records if stage == "teaching_scratch")
    assert record == {
        "mode": "jsonl",
        "raw_scratch": 0,
        "raw_scratch_list": True,
        "accepted_scratch": 1,
        "restored": True,
    }
