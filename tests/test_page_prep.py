"""Whole-page question matching should tolerate incomplete spoken numbers."""

import json
from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw

from k12_server import page_prep
from k12_server.page_prep import choose_question, image_fingerprint, reference_numbers, same_paper


def question(identifier, ordinal, path, progress):
    return {"id": identifier, "ordinal": ordinal, "path": path,
            "progress": progress, "read_confidence": .94}


def test_spoken_hierarchy_and_visible_progress_select_current_small_question():
    questions = [
        question("done", 0, ["二", "2", "(2)"], "done"),
        question("current", 1, ["二", "3", "(1)"], "partial"),
        question("next", 2, ["二", "3", "(2)"], "blank"),
        question("later", 3, ["二", "4", "(1)"], "blank"),
    ]
    assert reference_numbers("第二大题的第3小题的第(1)小题") == [2, 3, 1]
    assert choose_question(questions, "第二大题的第3小题的第(1)小题", spoken=True)["id"] == "current"
    assert choose_question(questions, "第三小题", spoken=True)["id"] == "current"
    assert choose_question(questions, "我在这题下面画了线", spoken=True)["id"] == "current"
    assert choose_question(questions, "第二大题第三小题第二问", spoken=True)["id"] == "next"
    assert choose_question(questions, "第二大题第二小题第二问", spoken=True)["id"] == "done"
    assert choose_question(questions, "第二大题第一小题第二问", spoken=True)["id"] == "current"
    assert choose_question(questions, "第九题", spoken=True) is None
    assert choose_question(questions, "第二大题第二小题第二问", reviewing=True)["id"] == "done"


def test_ambiguous_partial_reference_requires_clarification():
    questions = [
        question("one", 0, ["一", "1", "(1)"], "done"),
        question("two", 1, ["一", "1", "(2)"], "done"),
        question("three", 2, ["一", "2", "(2)"], "blank"),
    ]
    assert choose_question(questions, "第二问", reviewing=True) is None


def test_generic_method_words_cannot_silently_choose_question_one():
    questions = [question("one", 0, ["1"], "done"), question("two", 1, ["2"], "done")]
    for text in ["说明一种实质解题思路", "我们一起想一想", "先讲第一步", "读清这一题"]:
        assert reference_numbers(text) == []
        assert choose_question(questions, text, spoken=True) is None
    assert reference_numbers("先讲第一步，再看第4题") == [4]
    assert reference_numbers("二、3、(1)") == [2, 3, 1]
    questions[1]["label"] = "研学挑战、2"
    assert choose_question(questions, "研学挑战、2")["id"] == "two"


def test_streamed_unnumbered_questions_keep_distinct_nonprinted_identities(monkeypatch):
    item = {"path": [], "label": "未编号题", "evidence": "完整可见的题干条件",
            "box": [.1, .1, .8, .2], "progress": "blank", "confidence": .99}
    other = {**item, "box": [.1, .6, .8, .2]}
    monkeypatch.setattr(page_prep, "stream_model", lambda *_a: iter([
        json.dumps(item) + "\n" + json.dumps(item) + "\n" + json.dumps(other) + "\n"]))
    monkeypatch.setattr(page_prep, "post_model", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("No fallback needed")))
    image = BytesIO()
    Image.new("RGB", (1000, 1000), "white").save(image, "JPEG")
    items = page_prep.inventory_page(SimpleNamespace(ai_model="model"), image.getvalue(), on_question=lambda *_a: None)
    assert len(items) == 2
    assert items[0]["path"] != items[1]["path"]
    assert all(page_prep._number(q["path"][0]) is None for q in items)
    assert all(q["label"] == "未编号题" for q in items)
    questions = [{**q, "id": str(i), "ordinal": i, "read_confidence": .99} for i, q in enumerate(items)]
    assert choose_question(questions, "第1题", spoken=True) is None


def test_page_fingerprint_accepts_new_ink_but_rejects_changed_layout():
    def fingerprint(offset=0, handwriting=False):
        page = Image.new("RGB", (900, 1200), "white")
        draw = ImageDraw.Draw(page)
        for y in range(130, 1050, 95):
            draw.rectangle((100 + offset, y, 650 + offset, y + 8), fill="black")
            draw.rectangle((100 + offset, y + 26, 400 + offset, y + 32), fill="black")
        if handwriting:
            draw.line((300, 580, 420, 596), fill="black", width=5)
        output = BytesIO()
        page.save(output, format="JPEG")
        return image_fingerprint(output.getvalue())

    original = fingerprint()
    assert same_paper(original, fingerprint(handwriting=True))
    assert not same_paper(original, fingerprint(offset=120))


def test_spoken_answer_accepts_equivalent_meaning_but_not_partial_or_uncertain(monkeypatch):
    question = {"label": "二、3、(1)", "evidence": "一小时的一半是多少分钟？",
                "read_confidence": .96}
    solution = {"answer": "30分钟", "steps": "60分钟除以2得30分钟",
                "confidence": .97, "ambiguous": False}
    verdict = {"verdict": "correct", "confidence": .96, "summary": "一半小时就是三十分钟"}
    payloads = []

    def answer(_settings, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": json.dumps(verdict, ensure_ascii=False)}}]}

    monkeypatch.setattr(page_prep, "post_model", answer)
    settings = SimpleNamespace(ai_model="gpt-6-sol")
    assert page_prep.assess_spoken_answer(settings, question, solution, "半小时，也就是三十分钟") == (
        "对，回答正确。一半小时就是三十分钟。")
    assert "孩子原话" in payloads[0]["messages"][1]["content"]

    verdict.update(verdict="incomplete", confidence=.99, summary="")
    assert page_prep.assess_spoken_answer(settings, question, solution, "30") is None
    verdict.update(verdict="correct", confidence=.89, summary="三十分钟")
    assert page_prep.assess_spoken_answer(settings, question, solution, "三十分钟") is None
    solution["ambiguous"] = True
    assert page_prep.assess_spoken_answer(settings, question, solution, "三十分钟") is None
    assert len(payloads) == 3


def test_solver_minimum_scope_requires_a_bound_covering_other_allowed_constructions(monkeypatch):
    payloads = []

    def post(_settings, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": json.dumps({
            "answer": "只能确认一种可行做法，最少性还需比较允许的其他布局",
            "steps": "明确所选框架，并检查其他布局能否保留更多原有部分",
            "confidence": .8, "ambiguous": True})}}]}

    monkeypatch.setattr(page_prep, "post_model", post)
    monkeypatch.setattr(page_prep, "focus_question_image", lambda *_args: (b"crop", [0, 0, 1, 1]))
    result = page_prep.solve_question(SimpleNamespace(ai_model="solver"), b"page", {
        "label": "当前题目", "evidence": "将图形重新排列，问最少移动多少", "box": [0, 0, 1, 1]})
    instruction = payloads[0]["messages"][0]["content"]
    assert "覆盖题目允许其他方案的界限理由" in instruction
    assert "不能只数选定布局的缺口" in instruction
    assert "不套固定枚举" in instruction
    assert result["ambiguous"] is True and result["confidence"] == .8
    assert "只能确认一种可行做法" in result["answer"]
