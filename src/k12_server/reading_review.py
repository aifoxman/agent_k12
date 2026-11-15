"""A read-only introduction, prepared before any solution is requested."""

import hashlib
import json
import re

from .ai import bounded_confidence, normalized_text_anchors, verify_teaching_anchors
from .teaching_ink import normalized_teaching_beat


def normalize_reading(value: object, question: dict, image: bytes) -> dict | None:
    if not isinstance(value, dict) or question.get("confidence", 0) < .85:
        return None
    evidence = re.sub(r"\s+", "", question["evidence"])
    conditions = value.get("conditions")
    ask = value.get("ask")
    if not isinstance(conditions, list) or not isinstance(ask, str):
        return None
    conditions = [text.strip() for text in conditions[:4]
                  if isinstance(text, str) and 2 <= len(text.strip()) <= 60
                  and re.sub(r"\s+", "", text) in evidence]
    ask = ask.strip()
    if not conditions or not 2 <= len(ask) <= 80 or re.sub(r"\s+", "", ask) not in evidence:
        return None
    anchors = normalized_text_anchors(value.get("anchors"), question["box"], question["evidence"])
    # Only literal printed conditions; inferred points and solution diagrams do
    # not belong to this pre-solution cache.
    anchors = [anchor for anchor in anchors if anchor.get("kind", "text") == "text"
               and anchor["confidence"] >= .9]
    anchors = verify_teaching_anchors(image, anchors, question["box"], question["evidence"])
    return {"conditions": list(dict.fromkeys(conditions)), "ask": ask, "anchors": anchors}


def reading_review(question: dict, image: bytes) -> dict | None:
    """Recheck cached word positions on this frame; never wait for a solver."""
    if question.get("read_confidence", 0) < .85 or question.get("progress") == "occluded":
        return None
    reading = question.get("reading")
    if not reading and question.get("reading_json"):
        try:
            reading = json.loads(question["reading_json"])
        except (ValueError, TypeError):
            return None
    reading = normalize_reading(reading, {**question, "confidence": question["read_confidence"]}, image)
    if not reading:
        return None
    terms = "、".join(f"“{text}”" for text in reading["conditions"])
    speech = f"先看{question['label']}。题目给出的条件是{terms}。要我们求的是“{reading['ask']}”。"
    anchors = reading["anchors"]
    beat = normalized_teaching_beat({
        "speech": speech, "step_id": 1, "step_title": "读题审题", "step_end": True,
        "teaching_move": "give_clue", "reply_mode": "none", "needs_parent": False,
        "page": [{"kind": "underline", "anchor_id": anchor["id"],
                  "confidence": anchor["confidence"]} for anchor in anchors], "scratch": [],
    }, [0, 0, 1, 1], question["box"], "", anchors, True)
    if not beat:
        return None
    identity = json.dumps([question.get("id"), question["label"], question["evidence"],
                           question["box"], reading["conditions"], reading["ask"]], ensure_ascii=False)
    beat.update(phase="reading", reading_key=hashlib.sha256(identity.encode()).hexdigest())
    return beat
