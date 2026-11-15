"""Pre-solution reading uses literal conditions, never a guessed answer."""

import io

from PIL import Image, ImageDraw

from k12_server.reading_review import normalize_reading, reading_review


def paper():
    image = Image.new("RGB", (640, 960), "white")
    ImageDraw.Draw(image).rectangle((90, 190, 210, 210), fill="black")
    data = io.BytesIO()
    image.save(data, "JPEG")
    return data.getvalue()


def question():
    return {"id": "q1", "label": "第1题", "box": [.1, .1, .8, .6],
            "evidence": "前面有5人，后面有6人，这一队一共有多少人？",
            "confidence": .96, "read_confidence": .96, "progress": "blank"}


def test_reading_is_available_without_any_solution_and_does_not_introduce_an_answer():
    item = question()
    item["reading"] = {"conditions": ["前面有5人", "后面有6人"],
                       "ask": "这一队一共有多少人", "anchors": []}
    review = reading_review(item, paper())
    assert review["phase"] == "reading" and review["reply_mode"] == "none"
    assert "前面有5人" in review["speech"] and "后面有6人" in review["speech"]
    assert "多少人" in review["speech"] and "12" not in review["speech"]
    assert review["scratch"] == []
    assert review["reading_key"] == reading_review(item, paper())["reading_key"]


def test_reading_rejects_fabricated_conditions_inferred_results_and_missing_question():
    item = question()
    for value in [None, {"conditions": ["共有12人"], "ask": "多少人"},
                  {"conditions": ["前面有5人"], "ask": "计算总人数12人"},
                  {"conditions": "前面有5人", "ask": "多少人"}]:
        assert normalize_reading(value, item, paper()) is None
    item["reading"] = {"conditions": ["前面有5人"], "ask": "多少人"}
    item["read_confidence"] = .7
    assert reading_review(item, paper()) is None
    item["read_confidence"] = .96
    item["progress"] = "occluded"
    assert reading_review(item, paper()) is None


def test_reading_marks_exclude_blank_positions_outside_words_and_inferred_geometry():
    item = question()
    value = {"conditions": ["前面有5人"], "ask": "多少人", "anchors": [
        {"id": "blank", "text": "前面有5人", "box": [.5, .5, .1, .02], "confidence": .99},
        {"id": "outside", "text": "前面有5人", "box": [.01, .01, .05, .01], "confidence": .99},
        {"id": "inferred", "kind": "point", "text": "中点", "box": [.2, .2, .01, .01], "confidence": .99},
    ]}
    reading = normalize_reading(value, item, paper())
    assert reading and not reading["anchors"]

