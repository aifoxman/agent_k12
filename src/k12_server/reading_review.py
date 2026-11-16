"""A read-only introduction, prepared before any solution is requested."""

import hashlib
import json
import re

from .ai import normalized_text_anchors, verify_teaching_anchors
from .teaching_ink import normalized_teaching_beat


def reading_anchors_complete(reading: dict | None) -> bool:
    if not reading or not reading.get("conditions") or not reading.get("ask"):
        return False
    compact = lambda value: re.sub(r"\s+", "", value)
    terms = [compact(anchor.get("text", "")) for anchor in reading.get("anchors", [])]
    return all(any(term and term in compact(text) for term in terms)
               for text in [*reading["conditions"], reading["ask"]])


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
    anchors = reading["anchors"]
    compact = lambda value: re.sub(r"\s+", "", value)
    grounded = lambda text: any(compact(a["text"]) in compact(text) for a in anchors)
    conditions = list(reading["conditions"])
    # Begin with a real cue the child can see immediately. This changes only
    # the order of the same literal givens, never adds a condition or an answer.
    if not grounded(reading["ask"]):
        conditions.sort(key=lambda text: not grounded(text))
    label = question["label"]
    if re.fullmatch(r"\d+", label):
        label = f"第{label}题"
    first, *remaining = conditions
    speech = (f"先看{label}，题目告诉我们“{first}”。"
              + "".join(f"还有一个条件是“{text}”。" for text in remaining)
              + f"要我们求的是“{reading['ask']}”。")
    if grounded(reading["ask"]):
        speech = (f"先看{label}，这道题要我们求的是“{reading['ask']}”。"
                  + "".join(f"题目告诉我们“{text}”。" for text in conditions))
    beat = normalized_teaching_beat({
        "speech": speech, "step_id": 1, "step_title": "读题审题", "step_end": True,
        "teaching_move": "give_clue", "reply_mode": "none", "needs_parent": False,
        "page": [{"kind": "underline", "anchor_id": anchor["id"],
                  "confidence": anchor["confidence"]} for anchor in anchors], "scratch": [],
    }, [0, 0, 1, 1], question["box"], "", anchors, True)
    if not beat:
        return None
    identity = json.dumps([question.get("id"), question["label"], question["evidence"],
                           reading["conditions"], reading["ask"]], ensure_ascii=False)
    beat.update(phase="reading", reading_key=hashlib.sha256(identity.encode()).hexdigest())
    return beat
