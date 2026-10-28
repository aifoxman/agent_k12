"""Validated, incremental drawing instructions for a spoken homework hint."""

from __future__ import annotations

import ast
import base64
import json
import math
import re
from collections import Counter
from collections.abc import Iterator
from fractions import Fraction

from .ai import (
    anchor_match_text,
    bounded_confidence,
    normalized_mark_box,
    normalized_solution_diagram,
    normalized_spoken_math,
    normalized_teaching_turn,
    normalized_text_anchors,
    resolved_teaching_level,
    spoken_anchor_text,
    spoken_math_span,
    stream_model,
    teaching_goal,
    teaching_guidance,
    teaching_scope_guidance,
    teaching_stage,
    verify_teaching_anchors,
    withholds_final_answer,
)
from .config import Settings
from .grading import map_hint_cue

MAX_TEACHING_BEATS = 48
MAX_TEACHING_SPEECH = 360
MAX_TEACHING_SCRATCH = 32


def compact_note_text(value: str) -> str:
    for source, target in (("乘以", "×"), ("除以", "÷"), ("等于", "="),
                           ("加", "+"), ("减", "-"), ("乘", "×"), ("除", "÷"),
                           ("＋", "+"), ("－", "-"), ("−", "-"), ("＝", "="),
                           ("*", "×"), ("/", "÷")):
        value = value.replace(source, target)
    return re.sub(r"[\s，,。；;：:（）()]", "", value)


def _arithmetic_equal(expression: str) -> bool:
    """Verify bounded literal arithmetic without executing model text."""
    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            return Fraction(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            return left / right
        raise ValueError("Nonliteral arithmetic")

    try:
        sides = expression.replace("×", "*").replace("÷", "/").split("=")
        if len(sides) < 2 or len(expression) > 60:
            return False
        values = []
        for side in sides:
            tree = ast.parse(side, mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 32:
                return False
            values.append(evaluate(tree.body))
        return all(value == values[0] for value in values[1:])
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
        return False


def projected_spoken_note(text: str, speech: str) -> tuple[str, str] | None:
    """Keep a model-provided note or its literal, spoken arithmetic core."""
    normalized, spans = normalized_spoken_math(text)
    number = r"-?[0-9]+(?:\.[0-9]+)?"
    expression = rf"{number}(?:[+\-×÷]{number})*"
    candidates = list(re.finditer(rf"(?<![0-9.]){expression}(?:={expression})+(?![0-9.])", normalized))
    full = spoken_math_span(text, speech)
    if full and all(_arithmetic_equal(match.group()) for match in candidates):
        return text, full
    for match in candidates:
        formula = match.group()
        if not any(operator in formula for operator in "+-×÷") or not _arithmetic_equal(formula):
            continue
        actual = spoken_math_span(formula, speech)
        if actual:
            original = text[spans[match.start()][0]:spans[match.end() - 1][1]]
            return original, actual
    return None


def reveals_final_answer(text: str, answer: str) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    final = re.sub(r"\s+", "", answer).casefold()
    if not final:
        return False
    normalized_final, _ = normalized_spoken_math(answer)
    quantities = set(re.findall(r"-?[0-9]+(?:\.[0-9]+)?", normalized_final))
    if len(quantities) == 1:
        quantity = re.escape(next(iter(quantities))) + r"(?![0-9.])"
        spoken, _ = normalized_spoken_math(text)
        # This is a claimed minimum operation count, not a ban on using a
        # number already present among the question's given conditions.
        minimum = (r"(?:最少(?:也)?(?:需要|需|要)?|至少(?:也)?(?:需要|需|要))"
                   r"(?:移动|搬动|搬移|挪动|搬|移|挪)" + quantity)
        if re.search(minimum, spoken) or re.search(r"最少(?:是|为|=)" + quantity, spoken):
            return True
    if len(final) > 1:
        if final in compact:
            return True
        for number in re.findall(r"\d+(?:\.\d+)?", final):
            if compact == number or re.search(
                r"(?:答案|结果|所以|[=＝:：])[^。；，]{0,4}" + re.escape(number)
                + r"(?:$|[。；，])", compact,
            ):
                return True
        return False
    return compact == final or bool(re.search(r"(?:答案|结果|所以|[=＝:：])[^。；，]{0,4}" + re.escape(final) + r"(?:$|[。；，])", compact))


def step_board(speech: str, turn: dict) -> list[dict]:
    """Keep one current teaching note when a text generator omits its board."""
    if turn.get("teaching_move") in {"clarify_target", "parent_handoff"}:
        return []
    if re.search(r"看不清|没有看到|没看到|无法确定|还不能确定|不能可靠", speech):
        return []
    sentences = re.findall(r"[^。！？!?]+[。！？!?]?", speech)
    # Prefer an actual stated reason/strategy, never expand an action into invented reasoning.
    sentences.sort(key=lambda value: not bool(re.search(
        r"因为|所以|因此|表示|意味着|关系|相同|同样|如果|先.+再", value)))
    for sentence in sentences:
        if sentence.endswith(("？", "?")):
            continue
        if re.match(r"你想|你要|好的|明白了", sentence):
            continue
        note = re.split(r"(?:^|[，,；;])(?:现在)?(?:请你|请先|请|你先|你可以|接下来你)", sentence)[0]
        note = note.strip(" ，,；;。")
        if 4 <= len(note) <= 240:
            return [{"kind": "text", "role": "prose", "x": 8, "y": 12,
                     "text": f"当前一步：{note}"}]
    return []


def fallback_teaching_beat(speech: str, target_box: list[float],
                           cue: dict | None = None) -> dict:
    """Keep a located hint visible on paper and in the draft when drawing generation fails."""
    turn = normalized_teaching_turn({}, speech)
    return {
        "speech": speech,
        "page": [cue] if cue else [],
        "scratch": step_board(speech, turn),
        "step_end": True,
        **turn,
    }


def unresolved_teaching_beat(target: dict) -> dict:
    """A pending/error solve is a brief status, never a substitute concept lesson."""
    if target.get("_solution_inflight"):
        speech = "我正在核对这道题的具体解题步骤，先不判断答案，请稍等一下。"
    elif target.get("_solution_error"):
        speech = "这道题的解题核对刚才没有完成，请稍后再试，先保留你写的内容。"
    else:
        speech = "我还没核清题目条件怎样对应到具体操作，先不下结论。"
    return {"speech": speech, "page": [], "scratch": [], "teaching_move": "clarify_target",
            "reply_mode": "none", "needs_parent": False, "step_end": True}


def _fallback_speech_parts(speech: str, limit: int) -> list[str]:
    """Partition the original string at nearby punctuation without rewriting it."""
    parts, offset = [], 0
    while offset < len(speech):
        end = min(offset + limit, len(speech))
        if end < len(speech):
            window = speech[offset:end]
            boundaries = [match.end() for match in re.finditer(
                r"[。！？!?\n]|(?<!\d)\.(?!\d)", window)]
            usable = [boundary for boundary in boundaries if boundary >= min(45, limit // 2)]
            if not usable:
                usable = [match.end() for match in re.finditer(r"[；;，,]", window)
                          if match.end() >= max(45, limit // 2)]
            if usable:
                end = offset + usable[-1]
        part = speech[offset:end]
        if part.strip():
            parts.append(part)
        elif parts:
            parts[-1] += part
        else:
            # Keep leading whitespace attached to the first spoken fragment.
            next_end = min(end + limit, len(speech))
            parts.append(speech[offset:next_end])
            end = next_end
        offset = end
    return parts


def fallback_teaching_beats(speech: str, target_box: list[float] | None = None,
                            cue: dict | None = None, turn: dict | None = None) -> list[dict]:
    """Keep ordinary/fallback explanations complete, paced, and locally grounded.

    This partitions existing speech; it never creates a derivation or geometry.
    Unlike the legacy single-beat helper, only a cue actually named in the
    current fragment is shown, and child handoff is reserved for the final beat.
    """
    if not isinstance(speech, str) or not speech.strip():
        return []
    final_turn = normalized_teaching_turn(turn or {}, speech)
    # A real child handoff is its own final beat; it must not erase the board of
    # the explanation before it or be repeated after each explanatory clause.
    handoff_start = len(speech)
    sentences = list(re.finditer(r"[^。！？!?]+[。！？!?]?", speech))
    if final_turn["reply_mode"] in {"spoken", "writing"} and len(sentences) > 1:
        last_sentence = sentences[-1]
        if last_sentence.group().strip():
            handoff_start = last_sentence.start()
    limit = 150
    parts = _fallback_speech_parts(speech[:handoff_start], limit)
    if handoff_start < len(speech):
        parts.append(speech[handoff_start:])
    # The scene has a bounded beat count. Widen rather than drop any tail if an
    # upstream text response exceeds the usual size or contains many clauses.
    while len(parts) > MAX_TEACHING_BEATS:
        limit = max(limit + 1, math.ceil(limit * len(parts) / MAX_TEACHING_BEATS))
        parts = _fallback_speech_parts(speech[:handoff_start], limit)
        if handoff_start < len(speech):
            parts.append(speech[handoff_start:])
    continuing_move = final_turn["teaching_move"]
    if continuing_move in {"wait_for_writing", "parent_handoff"}:
        continuing_move = "give_clue"
    anchor_text = str((cue or {}).get("anchor_text") or (cue or {}).get("text") or "").strip()
    beats, cue_shown = [], False
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        local_turn = final_turn if last else {"teaching_move": continuing_move,
                                             "reply_mode": "none", "needs_parent": False}
        local_cue = None
        actual_cue = spoken_anchor_text(anchor_text, part) if cue and not cue_shown else None
        if actual_cue:
            local_cue, cue_shown = dict(cue), True
            if actual_cue != anchor_text:
                local_cue["anchor_text"] = actual_cue
        beat = fallback_teaching_beat(part, target_box or [], local_cue)
        beat.update(local_turn)
        board_turn = final_turn if final_turn["needs_parent"] else local_turn
        beat["scratch"] = step_board(part.strip(), board_turn)
        beat.update(step_id=index + 1, step_end=True)
        beats.append(beat)
    return beats


def _named_mark_anchor(mark: dict, anchored: dict[str, dict], speech: str) -> dict | None:
    identifier = str(mark.get("anchor_id") or "")
    if identifier:
        return anchored.get(identifier)
    # Recover an omitted id only from one exact, spoken current-photo reference.
    # Coordinates submitted by the generator never participate in this lookup.
    if mark.get("kind") not in {"circle", "underline", "box", "check"}:
        return None
    text = str(mark.get("text") or "").strip()
    matches = [anchor for anchor in anchored.values()
               if anchor.get("kind", "text") in {"text", "region"}
               and _anchor_identity_text(text) == _anchor_identity_text(anchor["text"])
               and spoken_anchor_text(text, speech)]
    if len(matches) == 1:
        return matches[0]
    groups = _text_anchor_groups(matches)
    return groups[0][0] if len(groups) == 1 and len(groups[0]) == len(matches) else None


def _explicit_spoken_marks(speech: str, anchors: dict[str, dict]) -> list[dict]:
    """Restore a stated circle/underline operation, never invent a geometry or sum."""
    marks = []
    for anchor in anchors.values():
        if anchor.get("kind", "text") not in {"text", "region"}:
            continue
        actual = (_region_spoken_text(anchor, speech, anchors) if anchor.get("kind") == "region"
                  else spoken_anchor_text(anchor["text"], speech))
        same_text = [item for item in anchors.values()
                     if _anchor_identity_text(item["text"]) == _anchor_identity_text(anchor["text"])]
        if not actual or (len(same_text) != 1 and (
                any(item.get("kind", "text") != "text" for item in same_text)
                or len(_text_anchor_groups(same_text)) != 1)):
            continue
        if any(mark.get("anchor_id") in {item["id"] for item in same_text} for mark in marks):
            continue
        position = speech.find(actual)
        clause_start = max((speech.rfind(stop, 0, position) for stop in "。！？!?；;"), default=-1) + 1
        clause_end = min((end for stop in "。！？!?；;"
                          if (end := speech.find(stop, position)) >= 0), default=len(speech))
        clause = speech[clause_start:clause_end]
        if re.search(r"不要|不用|不能|别|暂不|先不|看不清|不确定|可能|请你|你先|自己", clause):
            continue
        before = speech[clause_start:position][-12:]
        action = re.search(r"(圈出|圈住|圈起|圈一下|框出|框住|划出|划线|画下划线)[^。！？!?；;]{0,6}$", before)
        if not action:
            continue
        kind = "underline" if action.group(1) in {"划出", "划线", "画下划线"} else "circle"
        if anchor.get("kind") == "region":
            kind = "box" if action.group(1).startswith("框") else "circle"
            if action.group(1).startswith(("划", "画")):
                continue
        marks.append({"kind": kind, "anchor_id": anchor["id"]})
    return marks


def _region_signature(text: str) -> tuple[dict[str, str], set[str]]:
    directions = {}
    for axis, patterns in {
        "horizontal": {"left": r"左侧|左边|左端|左方|左面", "right": r"右侧|右边|右端|右方|右面",
                       "middle": r"中间|中央|中部"},
        "vertical": {"top": r"上方|上边|上部|上层|顶部|顶层", "bottom": r"下方|下边|下部|下层|底部|底层"},
        "depth": {"front": r"前侧|前排|前方|向前|前面", "back": r"后侧|后排|后方|后面"},
    }.items():
        matches = [name for name, pattern in patterns.items() if re.search(pattern, text)]
        if len(matches) == 1:
            directions[axis] = matches[0]
        elif matches:
            directions[axis] = "ambiguous"
    features = {name for name, pattern in {
        "protrusion": r"突出|凸出|伸出|外伸", "gap": r"缺口|凹口|凹槽|空缺|凹陷|空槽",
        "body": r"主体|主要部分", "block": r"积木|小正方体|方块|图块",
        "triangle": r"三角形", "rectangle": r"长方形", "square": r"正方形",
    }.items() if re.search(pattern, text)}
    return directions, features


def _region_spoken_text(anchor: dict, speech: str, anchors: dict[str, dict]) -> str | None:
    """Allow only unique visible direction/feature aliases; never reinterpret OCR text."""
    regions = [item for item in anchors.values() if item.get("kind") == "region"]
    exact = spoken_anchor_text(anchor["text"], speech)
    if exact and sum(anchor_match_text(item["text"]) == anchor_match_text(anchor["text"])
                     for item in regions) == 1:
        return exact
    # Separate the source and destination noun phrases before matching direction.
    separators = r"[，,。；;！？!?：:\n]+|(?:搬|移|挪|放|补|填)(?:动|走|入|到|进)?|(?:到|进入|放入)"
    cursor, phrases = 0, []
    for match in re.finditer(separators, speech):
        phrases.append(speech[cursor:match.start()])
        cursor = match.end()
    phrases.append(speech[cursor:])
    for phrase in phrases:
        phrase = re.sub(r"^(?:我们|现在|接着|然后|先|再|请看|圈出|圈住|圈起|框出|框住|看|把|将|从|在)+", "", phrase.strip())
        if not 2 <= len(phrase) <= 48:
            continue
        directions, features = _region_signature(phrase)
        if not directions or not features or "ambiguous" in directions.values():
            continue
        candidates = []
        for item in regions:
            other_directions, other_features = _region_signature(item["text"])
            shared = directions.keys() & other_directions.keys()
            if (shared and all(directions[key] == other_directions[key] for key in shared)
                    and features & other_features):
                candidates.append(item["id"])
        if candidates == [anchor["id"]]:
            return phrase
    return None


def _movement_reference(anchor: dict, clause: str, anchors: dict[str, dict],
                         previous: str, source: bool) -> str | None:
    strong = _region_spoken_text(anchor, clause, anchors)
    if strong:
        return strong
    regions = [item for item in anchors.values() if item.get("kind") == "region"]
    directions, features = _region_signature(anchor["text"])
    if source:
        side = directions.get("horizontal")
        if "protrusion" not in features or side not in {"left", "right"}:
            return None
        # A bare side/count can only refer back to a uniquely named source in
        # this beat, with the same explicitly spoken count. It grants no geometry.
        same_side = [item for item in regions
                     if _region_signature(item["text"])[0].get("horizontal") == side]
        if len(same_side) != 1:
            return None
        side_words = "左侧|左边|左端|左方|左面" if side == "left" else "右侧|右边|右端|右方|右面"
        match = re.search(rf"(?:{side_words})(?:的)?([0-9]+|[一二两三四五六七八九十百]+)(?:个|块)", clause)
        if not match:
            return None
        number, _ = normalized_spoken_math(match.group(1))
        if not re.fullmatch(r"[1-9][0-9]*", number):
            return None
        for introduction in re.split(r"[。！？!?；;\n]", previous[-120:]):
            if re.search(r"不要|不能|不用|别|不是|不动|保留|不确定|可能|假设|如果", introduction):
                continue
            name = _region_spoken_text(anchor, introduction, anchors)
            if not name:
                continue
            after_name, _ = normalized_spoken_math(introduction.split(name, 1)[1][:24])
            if re.search(r"^(?:这里|一共|共有|共|有|是|为|能看到|可以看到|一共有|可以数出){0,2}"
                         + re.escape(number) + r"(?![0-9.])(?:个|块)", after_name):
                return match.group()
        return None
    if "gap" not in features or sum("gap" in _region_signature(item["text"])[1] for item in regions) != 1:
        return None
    # A named visible opening is not a newly calculated landing position. Layer
    # qualifiers describe a subpart; the arrow still indicates the whole region.
    pattern = (r"(?:(?:左侧|左边|右侧|右边|中间|中央|前侧|后侧)(?:的)?){0,2}"
               r"(?:凹口|缺口|凹槽|空缺)(?:的)?(?:底层|下层|上层|前面|后面)?")
    for match in re.finditer(pattern, clause):
        if re.search(r"另|别的|其他|新的|想象", clause[max(0, match.start() - 4):match.start()]):
            continue
        named_directions, _ = _region_signature(match.group())
        if any(named_directions[key] != directions[key] for key in directions.keys() & named_directions.keys()):
            continue
        return match.group()
    return None


def _without_missing_arrow_claim(speech: str) -> str:
    """Remove common unfulfilled visual references, preserving the operation itself."""
    for pattern, replacement in [
        (r"(?:这条|这个|图中(?:的)?)?箭头(?:表示|代表|说明)(?:的)?(?:是)?", "搬移方向是"),
        (r"(?:请看|看看|看)(?:这条|这个|图中(?:的)?)?箭头[，,:：]?", ""),
        (r"(?:沿着|沿|顺着|按照)(?:这条|这个|图中(?:的)?)?箭头[，,:：]?", ""),
        (r"(?:正如|如)(?:这条|这个|图中(?:的)?)?箭头所示[，,:：]?", ""),
    ]:
        current = speech

        def substitute(match, current=current, replacement=replacement):
            prefix = re.split(r"[。！？!?；;\n]", current[:match.start()])[-1][-8:]
            if re.search(r"不要|不能|不用|不必|别|不是|不只|勿|假如|如果", prefix):
                return match.group()  # A warning or hypothesis does not promise visible ink.
            return replacement

        speech = re.sub(pattern, substitute, current)
    return speech.strip()


def _movement_arrow(mark: dict, anchored: dict[str, dict], speech: str,
                    reliable: bool) -> dict | None:
    """Connect visible regions as a labelled operation, never as geometric endpoints."""
    source = anchored.get(str(mark.get("from_anchor_id") or ""))
    target = anchored.get(str(mark.get("to_anchor_id") or ""))
    if (not reliable or not source or not target or source["id"] == target["id"]
            or source.get("kind") != "region" or target.get("kind") != "region"
            or min(source["confidence"], target["confidence"]) < .9):
        return None
    cue = None
    for match in re.finditer(r"[^。！？!?；;\n]+", speech):
        clause = match.group()
        if re.search(r"不要|不能|不用|别|暂不|先不|不确定|可能|假设|如果|是否|能否|请你|自己", clause):
            continue
        # An introductory sentence may use the full name, while the actual
        # operation uses another supported direction/feature phrase. Resolve
        # both references within this operation, never borrow an earlier cue.
        previous = speech[:match.start()]
        source_cue = _movement_reference(source, clause, anchored, previous, True)
        target_cue = _movement_reference(target, clause, anchored, previous, False)
        if not source_cue or not target_cue:
            continue
        start, end = clause.find(source_cue), clause.find(target_cue)
        between = clause[start + len(source_cue):end] if 0 <= start < end else ""
        if re.search(r"(?:搬|移|挪|放|补|填)[^。！？!?；;]{0,10}(?:到|进|入|向|至|在)", between):
            cue = clause[start:end + len(target_cue)]
            break
    if not cue:
        return None
    a, b = source["box"], target["box"]
    overlap = (max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
               * max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])))
    area_a, area_b = a[2] * a[3], b[2] * b[3]
    if overlap / min(area_a, area_b) > .2 or overlap / (area_a + area_b - overlap) > .15:
        return None  # Reject containment and heavy overlap; allow small boundary uncertainty.
    sx, sy, tx, ty = a[0] + a[2] / 2, a[1] + a[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    dx, dy = tx - sx, ty - sy
    if math.hypot(dx, dy) < max(.005, .35 * min(math.hypot(a[2], a[3]), math.hypot(b[2], b[3]))):
        return None
    source_scale = min(a[2] / (2 * abs(dx)) if dx else math.inf,
                       a[3] / (2 * abs(dy)) if dy else math.inf)
    target_scale = min(b[2] / (2 * abs(dx)) if dx else math.inf,
                       b[3] / (2 * abs(dy)) if dy else math.inf)
    if source_scale + target_scale >= .99:
        # Nearby boxes can touch or slightly overlap. Interior indicators remain
        # in the actual visible regions and cannot become a reversed zero-length line.
        source_scale, target_scale = min(source_scale * .5, .25), min(target_scale * .5, .25)
    return {"kind": "arrow", "purpose": "move", "label": "搬入示意",
            "from_anchor_id": source["id"], "to_anchor_id": target["id"],
            "anchor_id": "move:" + source["id"] + ":" + target["id"], "anchor_text": cue,
            "x1": round(sx + dx * source_scale, 6), "y1": round(sy + dy * source_scale, 6),
            "x2": round(tx - dx * target_scale, 6), "y2": round(ty - dy * target_scale, 6)}


def grounded_teaching_solution(solution: object, read_confidence: float) -> bool:
    """Allow a checked example for open-ended drawing tasks, but not unreadable ones."""
    if not isinstance(solution, dict) or read_confidence < .75:
        return False
    answer = str(solution.get("answer") or "").strip()
    steps = str(solution.get("steps") or "").strip()
    if not answer or not steps or bounded_confidence(solution.get("confidence")) < .85:
        return False
    if not solution.get("ambiguous"):
        return True
    if re.search(r"看不清|不清晰|无法|不能可靠|暂不能|需更清晰|无法唯一", answer):
        return False
    return bool(re.search(r"可拼|可画|可沿|可将|可以|例如|合理|不唯一|多种|一种", answer))


def _normalized_teaching_scratch(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    if len(value) > MAX_TEACHING_SCRATCH:
        # A prefix can delete the labels that explain retained arrows or cells.
        # Show an explicit limitation rather than presenting that prefix as a diagram.
        return [{"kind": "text", "role": "prose", "x": 2, "y": 2,
                 "text": "这一步示意需要分段展示，暂不显示不完整的图。"}]
    elements = []
    value = [{"x": 2, "y": 2, **element} if isinstance(element, dict)
             and element.get("kind") == "text" and element.get("role") == "prose" else element
             for element in value]
    # The general correction diagram validator accepts 24 primitives. Validate
    # teaching batches separately without changing the correction contract.
    for start in range(0, len(value), 24):
        diagram = normalized_solution_diagram({
            "description": "辅导草稿", "elements": value[start:start + 24]})
        if diagram:
            elements.extend(diagram["elements"])
    return elements


def local_concept_scratch(raw: dict, beat: dict, target: dict) -> list[dict]:
    """Keep verified condition notes separately from a restricted concept sketch."""
    evidence = str(target.get("evidence") or "").strip()
    confidence = bounded_confidence(target.get("read_confidence", target.get("confidence")))
    if confidence < .85 or not evidence:
        return []
    scratch = beat["scratch"]
    board = []
    for item in scratch:
        if item["kind"] != "text" or item.get("role") != "prose":
            continue
        condition = re.sub(r"^(?:题目条件|题干|已知|条件)[：:]\s*", "", item["text"]).strip()
        if (len(condition) >= 2 and condition in evidence
                and not re.search(r"[=＝+＋×÷]", condition)):
            board.append(item)
    basis = raw.get("scratch_basis")
    if not isinstance(basis, list) or not 1 <= len(basis) <= 3:
        return board
    if any(not isinstance(quote, str) or len(quote.strip()) < 2
           or quote.strip() not in evidence for quote in basis):
        return board
    sketch = [item for item in scratch if item not in board]
    if (not any(item["kind"] != "text" for item in sketch)
            or not any("示意图" in item.get("text", "") for item in sketch)
            or "示意图" not in beat["speech"]):
        return board
    # Coordinates describe layout; unchecked numerical claims or derivations do not.
    if any(re.search(r"\d|[=＝+＋×÷]", item.get("text", "")) for item in sketch):
        return board
    return scratch


def normalized_teaching_beat(raw: object, focus_region: list[float],
                             target_box: list[float], answer: str,
                             anchors: list[dict] | None = None,
                             require_anchors: bool = False,
                             allow_final_answer: bool = False,
                             allow_worked_notes: bool = False,
                             allow_region_operations: bool = False) -> dict | None:
    """Admit coherent speech and bounded marks tied to the located question."""
    if not isinstance(raw, dict):
        return None
    speech = str(raw.get("speech") or "").strip()
    if not 2 <= len(speech) <= MAX_TEACHING_SPEECH or (not allow_final_answer and reveals_final_answer(speech, answer)):
        teaching_stage("beat_rejected", reason="speech_policy")
        return None
    page, dropped = [], Counter()
    anchored = {item["id"]: item for item in normalized_text_anchors(anchors or [], target_box)}
    marks = raw.get("page") if isinstance(raw.get("page"), list) else []
    raw_mark_count = len(marks)
    if require_anchors and not marks:
        marks = _explicit_spoken_marks(speech, anchored)
    dropped["item_limit"] = max(0, len(marks) - 12)
    for mark in marks[:12]:
        if not isinstance(mark, dict) or mark.get("kind") not in {
            "underline", "circle", "box", "arrow", "line", "label", "check", "note",
        }:
            dropped["invalid_kind"] += 1
            continue
        kind = mark["kind"]
        if mark.get("space") not in (None, "page", "focus"):
            dropped["invalid_space"] += 1
            continue
        if kind == "arrow" and mark.get("purpose") == "move":
            move = _movement_arrow(mark, anchored, speech,
                                   allow_region_operations and allow_worked_notes and allow_final_answer)
            if move and bounded_confidence(mark.get("confidence", .9)) >= .9:
                page.append(move)
            else:
                dropped["move_not_grounded"] += 1
            continue
        anchor = _named_mark_anchor(mark, anchored, speech)
        if require_anchors and kind not in {"line", "arrow"} and not anchor:
            dropped["anchor_missing"] += 1
            continue
        source_confidence = anchor.get("confidence", 0) if anchor else 0
        if kind in {"line", "arrow"}:
            endpoints = [anchored.get(str(mark.get(name) or ""))
                         for name in ("from_anchor_id", "to_anchor_id")]
            if all(item and item.get("kind") == "point" for item in endpoints):
                source_confidence = min(item["confidence"] for item in endpoints)
        if bounded_confidence(mark.get("confidence", source_confidence)) < (.9 if kind in {"label", "check", "note"} else .82):
            dropped["confidence"] += 1
            continue
        if kind == "note":
            text = str(mark.get("text") or "").strip()
            projection = projected_spoken_note(text, speech) if (
                allow_worked_notes and anchor and len(text) <= 60) else None
            if anchor and anchor.get("kind") == "region":
                actual = _region_spoken_text(anchor, speech, anchored)
                if (not allow_region_operations or not allow_final_answer
                        or not actual or not projection or len(projection[0]) > 24
                        or re.search(r"因为|所以|因此|说明|证明|策略|方法|为什么", projection[0])
                        or not re.search(r"搬|移|挪|放|填|补|保留|圈|框|标|描|连|对齐|转|折|剪|分|合|[=＝]", projection[0])):
                    projection = None
            if (not projection or not 2 <= len(text) <= 60
                    or (not allow_final_answer and reveals_final_answer(text, answer))):
                dropped["note_not_spoken_or_permitted"] += 1
                continue
            text, actual = projection
            if not allow_final_answer and reveals_final_answer(text, answer):
                dropped["answer_policy"] += 1
                continue
            if anchor.get("kind") == "segment":
                left, top = min(anchor["x1"], anchor["x2"]), min(anchor["y1"], anchor["y2"])
                left, top = min(left, .999), min(top, .999)
                box = [left, top, max(abs(anchor["x2"] - anchor["x1"]), .001),
                       max(abs(anchor["y2"] - anchor["y1"]), .001)]
            else:
                box = anchor["box"]
            box = normalized_mark_box(box)
            if box:
                page.append({"kind": "note", "anchor_id": anchor["id"], "box": box, "text": text,
                             **({"anchor_text": actual} if actual != text else {})})
            continue
        if require_anchors and kind in {"line", "arrow"}:
            start = anchored.get(str(mark.get("from_anchor_id") or ""))
            end = anchored.get(str(mark.get("to_anchor_id") or ""))
            if start and end and start.get("kind") == end.get("kind") == "point":
                mark = {**mark, "space": "page", "x1": start["x"], "y1": start["y"],
                        "x2": end["x"], "y2": end["y"],
                        "anchor_id": ":".join(sorted((start["id"], end["id"])))}
            elif anchor and anchor.get("kind") == "segment":
                mark = {**mark, "space": "page",
                        **{name: anchor[name] for name in ("x1", "y1", "x2", "y2")}}
            else:
                dropped["geometry_anchor_missing"] += 1
                continue
        if kind in {"line", "arrow"} and all(name in mark for name in ("x1", "y1", "x2", "y2")):
            try:
                values = [mark[name] for name in ("x1", "y1", "x2", "y2")]
                if any(isinstance(value, bool) for value in values):
                    continue
                x1, y1, x2, y2 = (float(value) for value in values)
                if not all(math.isfinite(value) and 0 <= value <= 1 for value in (x1, y1, x2, y2)):
                    dropped["geometry_range"] += 1
                    continue
                if mark.get("space") != "page":
                    left, top, width, height = focus_region
                    x1, x2 = left + x1 * width, left + x2 * width
                    y1, y2 = top + y1 * height, top + y2 * height
                x, y, width, height = target_box
                if ((x1, y1) == (x2, y2) or not all(
                    x - .03 <= px <= x + width + .03 and y - .03 <= py <= y + height + .03
                    for px, py in ((x1, y1), (x2, y2))
                )):
                    dropped["geometry_range"] += 1
                    continue
                cue = spoken_anchor_text(str(mark.get("spoken_cue") or "").strip(), speech)
                if not cue:
                    cue = spoken_anchor_text(str(anchor.get("text") or ""), speech) if anchor else None
                page.append({"kind": kind, "x1": round(x1, 6), "y1": round(y1, 6),
                             "x2": round(x2, 6), "y2": round(y2, 6),
                             **({"anchor_id": mark["anchor_id"]} if mark.get("anchor_id") else {}),
                             **({"anchor_text": cue} if cue else {})})
            except (TypeError, ValueError):
                dropped["geometry_range"] += 1
                continue
            continue
        if require_anchors and kind == "label":
            text = str(mark.get("text") or "").strip()
            if (not anchor or anchor.get("kind") != "point"
                    or not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff]{1,3}", text)
                    or text not in speech or (not allow_final_answer and reveals_final_answer(text, answer))):
                dropped["label_not_grounded"] += 1
                continue
            page.append({"kind": kind, "box": anchor["box"], "anchor_id": anchor["id"], "text": text})
            continue
        if kind in {"underline", "circle", "box", "check"} and (require_anchors or kind == "check"):
            if not anchor or anchor.get("kind", "text") == "segment":
                dropped["anchor_missing"] += 1
                continue
            if anchor.get("kind") == "region" and kind not in {"circle", "box"}:
                dropped["region_kind"] += 1
                continue
            if anchor.get("kind") == "point":
                if kind not in {"circle", "box"}:
                    dropped["point_kind"] += 1
                    continue
            else:
                actual = (_region_spoken_text(anchor, speech, anchored) if anchor.get("kind") == "region"
                          else spoken_anchor_text(anchor["text"], speech))
                if not actual:
                    dropped["anchor_not_spoken"] += 1
                    continue
            cue = {"kind": kind, "box": anchor["box"], "anchor_id": anchor["id"],
                   "text": anchor["text"]}
            if anchor.get("kind", "text") != "point" and actual != anchor["text"]:
                cue["anchor_text"] = actual
            if kind == "check":
                cue.update(label="已找到条件", purpose="condition_found")
            page.append(cue)
            continue
        if require_anchors and kind in {"line", "arrow"}:
            dropped["geometry_anchor_missing"] += 1
            continue
        box = normalized_mark_box(mark.get("box"))
        if not box:
            dropped["invalid_box"] += 1
            continue
        text = str(mark.get("text") or "").strip()
        if mark["kind"] == "label" and (not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff]{1,3}", text)
                                        or (not allow_final_answer and reveals_final_answer(text, answer))):
            continue
        cue = {"kind": mark["kind"], "box": box}
        if mark["kind"] == "label":
            cue["text"] = text
        # New beats use coordinates of the full-page image. Keep crop coordinates
        # for older generators and fixtures that omit the space field.
        mapped = (map_hint_cue(cue, [0, 0, 1, 1], target_box)
                  if mark.get("space") == "page" else map_hint_cue(cue, focus_region, target_box))
        if mapped:
            page.append(mapped)
    scratch = []
    for element in _normalized_teaching_scratch(raw.get("scratch")):
        if (not allow_final_answer and element["kind"] == "text"
                and reveals_final_answer(element["text"], answer)):
            teaching_stage("beat_rejected", reason="scratch_answer_policy")
            return None
        scratch.append(element)
    # A point circle and its A/B label form one emphasis, not two separate marks.
    selected, groups, lines, notes = [], set(), set(), set()
    for mark in page:
        key = mark.get("anchor_id") or json.dumps(
            mark.get("box") or [mark.get(name) for name in ("x1", "y1", "x2", "y2")])
        if mark["kind"] in {"line", "arrow"}:
            # Conditions already circled must not consume the budget for explaining an actual edge.
            if key not in lines and len(lines) >= 4:
                dropped["line_limit"] += 1
                continue
            lines.add(key)
            selected.append(mark)
            continue
        if mark["kind"] == "note":
            if key not in notes and len(notes) >= 2:
                dropped["note_limit"] += 1
                continue
            notes.add(key)
            selected.append(mark)
            continue
        if key not in groups and len(groups) >= 2:
            dropped["emphasis_limit"] += 1
            continue
        groups.add(key)
        selected.append(mark)
    step_id = raw.get("step_id")
    title = raw.get("step_title")
    if not any(mark["kind"] == "arrow" for mark in [*selected, *scratch]):
        corrected = _without_missing_arrow_claim(speech)
        if corrected != speech:
            teaching_stage("speech_visual_reference_adjusted", reason="arrow_unavailable")
            if len(corrected) < 2:
                return None
            # Revalidate note projections/cues against the actual spoken result.
            return normalized_teaching_beat({**raw, "speech": corrected}, focus_region,
                                           target_box, answer, anchors, require_anchors,
                                           allow_final_answer, allow_worked_notes, allow_region_operations)
    teaching_stage("beat_normalized", raw_marks=raw_mark_count, recovered_marks=max(0, len(marks) - raw_mark_count),
                   accepted_marks=len(selected), anchors=len(anchored),
                   **{"drop_" + key: value for key, value in dropped.items() if value})
    return {"speech": speech, "page": selected, "scratch": scratch,
            **normalized_teaching_turn(raw, speech),
            "step_end": raw.get("step_end") is not False,
            **({"step_id": step_id} if isinstance(step_id, int) and not isinstance(step_id, bool)
               and 1 <= step_id <= MAX_TEACHING_BEATS else {}),
            **({"step_title": title.strip()[:24]} if isinstance(title, str) and title.strip() else {})}


def _complete_lesson_turn(beat: dict, complete: bool) -> dict:
    """A full second-tier lesson does not require an answer before continuing."""
    if (not complete or beat.get("needs_parent")
            or beat.get("teaching_move") in {"clarify_target", "parent_handoff"}):
        return beat
    return {**beat, "reply_mode": "none",
            "teaching_move": ("give_clue" if beat.get("teaching_move") == "wait_for_writing"
                              else beat["teaching_move"])}


def _spoken_reasoning_board(beat: dict, target: dict, goal: str, answer: str,
                            allow_final: bool, raw: dict, mode: str = "jsonl") -> dict:
    """Recover only an already-spoken reason; never derive new mathematics."""
    original_count = len(beat["scratch"])
    read_confidence = target.get("read_confidence", target.get("confidence", 0))
    confidence = (bounded_confidence(read_confidence)
                  if isinstance(read_confidence, (int, float)) and not isinstance(read_confidence, bool) else 0)
    speech = beat["speech"]
    raw_scratch = raw.get("scratch")
    omitted = raw_scratch is None or (isinstance(raw_scratch, list) and not raw_scratch)
    if (not original_count and omitted and goal in {"explain_method", "worked_example"}
            and confidence >= .85 and target.get("found") is not False
            and str(target.get("evidence") or "").strip()
            and not beat.get("needs_parent")
            and beat["teaching_move"] not in {"clarify_target", "parent_handoff"}
            and not re.search(r"看不清|没有看到|没看到|无法确定|还不能确定|不能可靠", speech)):
        reasons = [sentence for sentence in re.findall(r"[^。！？!?]+[。！？!?]?", speech)
                   if not re.match(r"^(?:现在|接下来)?(?:请|你(?:先|可以|来)|先(?:写|看|画|圈|读|试)|试着|自己|动笔)", sentence)
                   and re.search(
                       r"因为|所以|因此|意味着|意思是|表示|关系|分类|"
                       r"分别|重复|不算新|不会漏|如果|先[^。！？!?]{1,80}再|"
                       r"按[^。！？!?]{0,12}(?:顺序|个数|大小)|"
                       r"检查[^。！？!?]{0,80}(?:有没有|是不是|是否|条件|单位|重复|漏)", sentence)]
        for sentence in reasons:
            note = step_board(sentence, beat)
            if (note and note[0]["text"].removeprefix("当前一步：") in sentence
                    and (allow_final or not reveals_final_answer(note[0]["text"], answer))):
                beat = {**beat, "scratch": note}
                break
    teaching_stage("teaching_scratch", mode=mode,
                   raw_scratch=len(raw["scratch"]) if isinstance(raw.get("scratch"), list) else 0,
                   raw_scratch_list=isinstance(raw.get("scratch"), list),
                   accepted_scratch=len(beat["scratch"]),
                   restored=not original_count and bool(beat["scratch"]))
    return beat


def _anchor_identity_text(text: str) -> str:
    # A terminal numeric dot might be sentence punctuation or an incomplete
    # decimal. Count it as a possible alias for ambiguity only; actual spoken
    # matching still requires the original full numeric value and punctuation.
    value = anchor_match_text(text)
    return value[:-1] if re.search(r"[0-9]\.$", value) else value


def _same_text_position(left: dict, right: dict) -> bool:
    """Recognize aliases of one word box without merging separate occurrences."""
    if (left.get("kind", "text") != "text" or right.get("kind", "text") != "text"
            or _anchor_identity_text(left["text"]) != _anchor_identity_text(right["text"])):
        return False
    x, y, w, h = left["box"]
    bx, by, bw, bh = right["box"]
    intersection = max(0, min(x + w, bx + bw) - max(x, bx)) * max(
        0, min(y + h, by + bh) - max(y, by))
    union = w * h + bw * bh - intersection
    return bool(union and intersection / union >= .75
                and abs(x + w / 2 - bx - bw / 2) <= min(w, bw) * .15
                and abs(y + h / 2 - by - bh / 2) <= min(h, bh) * .15)


def _text_anchor_groups(anchors: list[dict]) -> list[list[dict]]:
    # Complete-link groups: a chain of nearby boxes cannot erase ambiguity.
    groups: list[list[dict]] = []
    for anchor in anchors:
        if anchor.get("kind", "text") != "text":
            continue
        for group in groups:
            if all(_same_text_position(anchor, other) for other in group):
                group.append(anchor)
                break
        else:
            groups.append([anchor])
    return groups


def _unique_text_anchors(anchors: list[dict]) -> list[dict]:
    groups = _text_anchor_groups(anchors)
    unique = []
    for group in groups:
        text = _anchor_identity_text(group[0]["text"])
        if sum(_anchor_identity_text(other[0]["text"]) == text for other in groups) != 1:
            continue
        roles = {item["role"] for item in group if item.get("role")}
        anchor = max(group, key=lambda item: item["confidence"])
        if len(roles) == 1:
            anchor = {**anchor, "role": next(iter(roles))}
        elif len(roles) > 1:
            anchor = {key: value for key, value in anchor.items() if key != "role"}
        unique.append(anchor)
    return unique


def _short_text_box(anchor: dict, target_box: list[float]) -> bool:
    _, _, width, height = target_box
    _, _, word_width, word_height = anchor["box"]
    return (word_width <= width * .6 and word_height <= min(.08, height * .3)
            and word_width * word_height <= width * height * .1)


def _first_reading_beat(beat: dict, page_image: bytes, focus_region: list[float],
                        target: dict, answer: str, allow_final: bool, reliable: bool,
                        allow_operations: bool, marked: list[dict] | None = None) -> dict:
    """Underline only already-spoken, unique, short current-photo text conditions.

    This restores omitted read-along ink without another model call or a made-up
    target-sized highlight. The ordinary normalizer still owns every permission.
    """
    if beat["teaching_move"] in {"clarify_target", "parent_handoff"} or beat["needs_parent"]:
        return beat
    anchors = normalized_text_anchors(target.get("anchors"), target["box"], target["evidence"])
    existing = {mark.get("anchor_id") for mark in beat["page"]
                if mark["kind"] in {"underline", "circle", "box", "check"}}
    marked = [] if marked is None else marked
    marked.extend(anchor for anchor in anchors if anchor["id"] in existing
                  and anchor.get("kind", "text") == "text")
    count = max(0, 2 - len(existing))
    if not count:
        return beat
    speech = re.sub(r"\s+", "", beat["speech"])
    evidence = re.sub(r"\s+", "", target["evidence"])
    candidates = []
    for anchor in _unique_text_anchors(anchors):
        if (anchor["id"] in existing or anchor["confidence"] < .9 or anchor.get("role") == "label"
                or any(_same_text_position(anchor, previous) for previous in marked)):
            continue
        text = re.sub(r"\s+", "", anchor["text"])
        actual = spoken_anchor_text(anchor["text"], beat["speech"])
        if (not 2 <= len(text) <= 20 or not actual or text not in evidence
                or re.fullmatch(r"(?:第)?[0-9一二三四五六七八九十]+(?:题|小题|[.、)])?", text)):
            continue
        position = speech.index(re.sub(r"\s+", "", actual))
        clause = re.split(r"[。！？!?；;]", speech[:position])[-1]
        if re.search(r"看不清|无法辨认|不确定|未核对|还没核对|不要|不用|不能|别|暂不|先不|请你|你先|自己", clause):
            continue
        if not _short_text_box(anchor, target["box"]):
            continue
        priority = bool(re.search(r"至少|至多|最少|最多|不超过|不少于|不重复|不同|相同|"
                                  r"除外|非零|多少|几种|相加|合计|一共|单位|依据|原因|任选", text))
        candidates.append((not priority, position, anchor))
    candidates.sort(key=lambda item: item[:2])
    if not candidates:
        return beat
    try:
        accepted = verify_teaching_anchors(page_image, [item[2] for item in candidates[:count]],
                                          target["box"], target["evidence"])
    except (OSError, ValueError, TypeError):
        return beat
    if not accepted:
        return beat
    restored = normalized_teaching_beat(
        {**beat, "page": [{"kind": "underline", "anchor_id": anchor["id"],
                          "confidence": anchor["confidence"]} for anchor in accepted] + beat["page"]},
        focus_region, target["box"], answer, anchors, True, allow_final, reliable, allow_operations)
    if restored:
        restored_ids = {mark.get("anchor_id") for mark in restored["page"]
                        if mark["kind"] in {"underline", "circle", "box", "check"}}
        marked.extend(anchor for anchor in accepted if anchor["id"] in restored_ids)
        teaching_stage("reading_marks_restored", accepted=sum(
            mark["kind"] == "underline" and mark.get("anchor_id") in {a["id"] for a in accepted}
            for mark in restored["page"]))
    return restored or beat


def _spoken_operation_notes(beat: dict, raw: dict, page_image: bytes,
                            focus_region: list[float], target: dict, answer: str,
                            allow_final: bool, reliable: bool, allow_operations: bool) -> dict:
    """Project submitted, spoken literal arithmetic onto a related real word box."""
    if (not allow_final or not reliable or not allow_operations
            or beat["teaching_move"] in {"clarify_target", "parent_handoff"} or beat["needs_parent"]):
        return beat
    scratch = raw.get("scratch")
    room = max(0, 2 - sum(mark["kind"] == "note" for mark in beat["page"]))
    if not isinstance(scratch, list) or not room:
        return beat
    anchors = normalized_text_anchors(target.get("anchors"), target["box"], target["evidence"])
    related = [anchor for anchor in _unique_text_anchors(anchors)
               if anchor["confidence"] >= .9 and anchor.get("role") in {"condition", "question"}
               and _short_text_box(anchor, target["box"])]
    number = r"-?[0-9]+(?:\.[0-9]+)?"
    expression = rf"{number}(?:[+\-×÷]{number})*"
    existing = {normalized_spoken_math(mark["text"])[0] for mark in beat["page"]
                if mark["kind"] == "note"}
    accepted_texts = {item["text"] for item in beat["scratch"] if item["kind"] == "text"}
    proposals = []
    for item in scratch[:MAX_TEACHING_SCRATCH]:
        if not isinstance(item, dict) or item.get("kind") != "text":
            continue
        submitted = str(item.get("text") or "").strip()
        validated = _normalized_teaching_scratch([item])
        if (submitted not in accepted_texts or not validated
                or validated[0].get("text") != submitted):
            continue
        normalized, spans = normalized_spoken_math(submitted)
        for match in re.finditer(rf"(?<![0-9.]){expression}(?:={expression})+(?![0-9.])", normalized):
            formula = match.group()
            if (formula in existing or not any(operator in formula for operator in "+-×÷")
                    or not _arithmetic_equal(formula)):
                continue
            text = submitted[spans[match.start()][0]:spans[match.end() - 1][1]]
            if not 2 <= len(text) <= 60:
                continue
            operands = set(re.findall(number, formula.split("=")[0]))
            for clause in re.split(r"[。！？!?；;\n]", beat["speech"]):
                if (not spoken_math_span(text, clause)
                        or re.search(r"不要|不能|并非|不是|不等于|不确定|未核对|还没核对|可能|假设|比如|例如", clause)):
                    continue
                candidates = []
                for anchor in related:
                    if not spoken_anchor_text(anchor["text"], clause):
                        continue
                    values = set(re.findall(number, normalized_spoken_math(anchor["text"])[0]))
                    if anchor["role"] == "question" or values & operands:
                        candidates.append(anchor)
                questions = [anchor for anchor in candidates if anchor["role"] == "question"]
                candidates = questions or candidates
                if len(candidates) != 1:
                    continue
                proposals.append({"kind": "note", "anchor_id": candidates[0]["id"], "text": text})
                existing.add(formula)
                break
            if len(proposals) >= room:
                break
        if len(proposals) >= room:
            break
    if not proposals:
        return beat
    try:
        accepted = verify_teaching_anchors(page_image, [anchor for anchor in related
            if anchor["id"] in {mark["anchor_id"] for mark in proposals}], target["box"], target["evidence"])
    except (OSError, ValueError, TypeError):
        return beat
    accepted_ids = {anchor["id"] for anchor in accepted}
    proposals = [mark for mark in proposals if mark["anchor_id"] in accepted_ids]
    if not proposals:
        return beat
    restored = normalized_teaching_beat(
        {**beat, "page": beat["page"] + proposals}, focus_region, target["box"], answer,
        anchors, True, allow_final, reliable, allow_operations)
    if restored:
        teaching_stage("operation_notes_restored", accepted=len(restored["page"]) - len(beat["page"]))
    return restored or beat


def stream_teaching_beats(settings: Settings, page_image: bytes, focus_image: bytes,
                          focus_region: list[float], target: dict, solution: dict,
                          grade: str | None, level: int, previous_hint: str = "",
                          student_thought: str = "", question: str = "",
                          session_context: str = "", age_band: str = "",
                          assessment: dict | None = None,
                          allow_diagram: bool = True,
                          model_stream=None, textbook: str | None = None,
                          goal: str | None = None,
                          allow_final_answer: bool | None = None) -> Iterator[tuple[str, dict | str]]:
    """Parse one JSON object per streamed line; never expose partial model markup."""
    answer = str(solution.get("answer") or "").strip()
    level = resolved_teaching_level(question, student_thought, level)
    goal = goal or teaching_goal(question, student_thought, level)
    complete = level == 2 and goal == "worked_example"
    reliable = allow_diagram and grounded_teaching_solution(
        solution, target.get("read_confidence", target.get("confidence", 0)))
    allow_final_answer = bool(
        reliable and not withholds_final_answer(question, student_thought)
        and goal != "independent_attempt"
        and (allow_final_answer is True or
             (allow_final_answer is None and goal == "worked_example")))
    allow_region_operations = reliable and goal == "worked_example" and allow_final_answer
    unresolved_full = goal == "worked_example" and not reliable
    if unresolved_full and (target.get("_solution_inflight") or target.get("_solution_error")):
        yield "beat", unresolved_teaching_beat(target)
        return
    instruction = (
        f"你是纸笔作业辅导老师。按当前卡点所需的完整因果输出JSONL，最多{MAX_TEACHING_BEATS}行；"
        "这是安全上限，不是必须讲满或只能讲一小步。禁止Markdown、数组、代码块和额外文字。"
        "每行必须是一个完整 JSON 对象："
        '{"speech":"本段连贯的操作与理由","teaching_move":"give_clue","reply_mode":"none",'
        '"step_id":1,"step_title":"当前要讲清的一步","step_end":false,'
        '"needs_parent":false,"page":[{"kind":"underline|circle|box|check|line|arrow|label|note",'
        '"space":"page","anchor_id":"a1","box":[x,y,w,h],"text":"A","confidence":0.95}],'
        '"scratch":[{"kind":"text","role":"prose","text":"本步理由与比较依据"}]}。'
        "按教学意图组织连贯讲解，一个有标题的逻辑步通常60到150个汉字，"
        "把本步的对象、操作、理由和得到的关系讲在一起；不要按字数、逗号或一句一停切碎因果。"
        "同一步有顺序指认或绘制时，可用1到3个自然语音片共用step_id和step_title，"
        "每片把当前操作及其理由讲清，片内speech最多360字，不为凑长度复述或扩写。"
        "逻辑步按教学目的分阶段，不按句子或分类分支编号；同一读图、搬移、比较论证或检查阶段的"
        "连续语音片复用step_id，仅阶段最后一片step_end=true。阶段数量随题意决定，"
        "不要把证明表中的每一行都升级成新逻辑步。"
        "下面两行仅演示同阶段的分片格式，不照抄示例内容：\n"
        '{"speech":"先对应图里这一处条件，说明它表示什么。","step_id":1,'
        '"step_title":"对应图中条件","step_end":false,"page":[],"scratch":[]}\n'
        '{"speech":"再对应另一处条件，把两处怎样联系讲清。","step_id":1,'
        '"step_title":"对应图中条件","step_end":true,"page":[],"scratch":[]}\n'
        "不要把本步所有图先塞在第一片、后面长时间只说话；每片page只提交此刻讲到的1到2组操作。"
        "首句直接指向当前一个条件并配对应圈线，不复述用户要求或用‘你要我完整示范’等元开场；"
        "需要依次比较两处时，分别在说到各处的语音片提交标记，不在首片圈出后续全部条件。"
        "本步最后一片step_end=true；其余false，step_end只表达教学意图边界，"
        "不是孩子口述/动笔回合；默认连续讲完整个请求，不要每步追问或用wait_for_writing截断后续推理。"
        "page线段用spoken_cue写本句实际说出的指向词，随标记传出，使说到的词与画到的边对应。"
        "scratch只写这一小段所解释的理由/关系，不提前把后几步板书都写出。"
        f"每片scratch最多{MAX_TEACHING_SCRATCH}个元素，格线、箭头和文字标签都计入；"
        "复杂示意应分为连续语音片增量展示，共用同一step_id及0到100坐标，"
        "每片只增加当前需要的完整一组图形与标签，不要一次超量或省掉来源、目标、行列标签。"
        "标注定位与核验规则只供教师内部遵守，不要把‘原图没有精确定位点’、"
        "‘缺锚点所以不猜连线’或核验系统状态读给孩子；"
        "改用自然教学说明，例如‘用俯视示意看清前后排和搬到哪里’，仍不得猜原图位置。"
        "若本句仅指认一个已知条件，scratch不能先写尚未讲到的字母定义或后续推导；"
        "等需要解释该关系时再同步补相应板书，短句长度不得靠省略对象和因果来满足。"
        "page 的0到1坐标必须相对第一张完整纸面图，不能用第二张局部图的坐标；只标清楚可见的题干条件、图形或孩子步骤；不确定就为空。"
        "精确圈词、划线、条件勾选必须引用给定 anchors 的 anchor_id，并在本段读出原词；"
        "没有可靠锚点不猜词框。check只表示已找到条件，绝不是答案正确或作业完成。"
        "每层都在相应读题语音片中逐字点读同一行的短条件或所求，并同步提交小范围underline/check；"
        "跨行条件分别引用各行实际词框，不能把整段跨行原文挂到其中一小块。"
        "老师先在原图同步示范圈划，再可邀请孩子在纸上模仿，不能用请你自己划线代替本轮实际标记。"
        "标题、题号和整题大圈不能替代关键条件或所求的标记；无可靠位置就说清该处仍待核对，不谎称已画。"
        "只有本片实际提交对应可靠锚点标记时，才说已经圈出或划出；若标记条件不足，直接讲该条件，不声称已画。"
        "图形对象也可引用kind=region的当次可见区域锚点，用circle或box指认，并在语音读出其具体描述；"
        "region只是可见对象位置，不证明隐藏结构、数量或解答，不得当作精确几何点边画辅助线。"
        "可见区域指认优先复用给定的简短描述；方位与特征明确一致时，可用左边/左侧、伸出/突出、缺口/凹口等短说法，"
        "不能只说‘这里’，不能把含主体的大圈说成只含某几块，也不能借别的同类区域代指。"
        "line/arrow必须用from_anchor_id/to_anchor_id引用两个已给定point锚点，"
        "或用anchor_id引用可见segment；坐标由服务端决定，不能自由猜位置。"
        '例如{"kind":"line","from_anchor_id":"p1","to_anchor_id":"p2","confidence":0.95}；'
        '描现有边用{"kind":"line","anchor_id":"s1","confidence":0.95}。'
        "搬移过程另用arrow且purpose=move：from_anchor_id/to_anchor_id分别引用本次两个不同、边界可靠的region；"
        "仅本轮允许给出完整示范与结论时使用搬入箭头和region操作note；提示或明确不要答案时不展示完整搬法和结果批注，仍可圈出条件。"
        "本段必须明确说出来源、搬到/放入的动作和目标可见区域，不能指向推测的隐藏空位。"
        "若动作句用方位加数量的短指代，须在本片先明确引入唯一来源的可见特征和同一数量；"
        "目标只有在当前照片中是唯一可见凹口时才可省略方位，不用‘这里/那里’代替来源和目标。"
        '例如{"kind":"arrow","purpose":"move","from_anchor_id":"r_source",'
        '"to_anchor_id":"r_target","confidence":0.95}。'
        "服务端从真实框画带‘搬入示意’标识的关系箭头；它只说明搬移方向，不表示精确单块落点或几何辅助线。"
        "通常直接讲来源、搬移动作和目标即可，不必口头预告标记。"
        "若说‘看箭头’或‘箭头表示’，本片必须同时提交对应的合法page.arrow，不能只用语音声称已经画了。"
        "可靠解法中的本题短操作或算式可用note，必须anchor_id引用当前文字、点或边，"
        "text最多60字且在本段speech实际说出同一操作/算式；算符可用加减乘除等于口述。"
        "note也可引用region：本段须指认同一对象，批注只留最多24字的当前操作或实际说出的算式核心，不写长理由。"
        "note.text必须是本片speech中实际连续说出的一段短原话；先自然说出这条短批注，再解释理由。"
        "除数词和加减乘除等于的已有等价口述外，不改写动词、方位、对象或语序，不让界面猜同义句；"
        "只提交实际说出的短操作或完整算式核心，不能把未说过的结论标题或长理由贴上原图。"
        "note不自行给位置，不抄长段理由，位置由锚点与界面排版决定；解法未核验时禁止note。"
        "在实际运算片自然逐字点读相关条件或所求锚点，并提交对应page.note展示本片实际说出的操作或完整算式；"
        "分类比较与覆盖理由留在scratch，具体计算与合并结果不能全部只留在草稿。"
        "note关联同句真实提到的条件或所求，不能借无关标题挂结果；若该处没有可靠相关锚点则不猜位置。"
        "label必须引用point锚点；同一重点的圈和标签使用同一anchor_id。"
        "缺可靠几何锚点时不在原图补点、连几何辅助线或命名；不影响可靠region上的圈框、短操作及搬入示意。"
        "在原图上给已知点命名时，必须在同一行 page 中给对应位置的 label 标记，"
        "text 填 A、B 等短标签，box 是该点附近的小框；还应画必要的辅助线，但不得猜测看不清的端点。"
        "scratch 是草稿画布，坐标0到100，可用 text、line、arrow、rect、circle；"
        "图内文字用role=label并保留与图形对应的x/y；长步骤说明用role=prose按段落阅读。"
        "线和箭头用 x1,y1,x2,y2，矩形用 x,y,w,h，圆用 x,y,r。"
        "page用于本题具体操作：圈用到的条件、题目要点划线、标点边及画辅助线；"
        "有可靠几何锚点时直接在原题描出本句所说的边或连接，不能只圈数字就算图形讲解。"
        "关键图形关系须有可见辅助：已有对应可靠边线锚点时实际引用并描边，不能整课只口述而page线段全空；"
        "若缺可靠位置但关系已核对，改画明确标注示意图的最小局部关系，不能仍只口头描述。"
        "scratch用于为什么、策略、关系推导与必要的概念示意，不能把整张原题搬到scratch替代原题操作。"
        "先在左侧原题依本段实际指认画条件underline/check、来源circle、可见缺口box，"
        "再用可靠区域间的搬入示意和短note展示过程；有可靠region时不能把这些操作全部移到草稿。"
        "scratch优先保留方法、比较、最少性理由与检查的短prose，不重画整题的ASCII格表或整套搬移图。"
        "没有可靠region时只用可靠文字锚点标条件，不猜对象框或目标位置，也不把大题框当已圈出具体来源。"
        "原题上可可靠标出的解题操作与图画勾勒放在page，草稿记录其理由和关系；"
        "无法可靠落原题才用明确局部示意，不能为了把步骤留在原题而猜端点。"
        "教看图分组或画法时，用与本句同步的线段或小组示范当前局部例子，让孩子看清"
        "如何操作；仅反复贴顶点标签或抄整段说明，不能代替图形引导。"
        "原题描边或分组线只用当次可靠point/segment锚点；可见对象的圈框和搬入示意用region，不能冒充几何边；"
        "没有对应锚点时不猜位置，"
        "可在scratch画最小局部示意并在语音说明这是示意图，不冒充原题精确位置或猜连接关系。"
        "完整解释本次卡点及必要前后关系；是否给最终值遵守下方答案许可，不能省略关键理由。"
        "每个讲解概念、方法或步骤的beat保留必要的分步草稿："
        "短板书用role=prose，图形关系用局部示意；只写本段新增要点，不逐字抄全部语音。"
        "只教一步也要保留这一步的板书，可用几个beat逐段写清；不能把一步提示理解成scratch=[]。"
        "纯等待、简单确认或定位澄清可不新增草稿；交回孩子任务后停止讲解，已讲草稿仍供对照。"
        "草稿从上到下安排且不得互相覆盖；每行只讲当前正在画的内容，句子短且自然，不重复已画内容。"
        "拼图或作图题的提示只示范当前局部；明确完整示范且本轮答案许可允许时，"
        "可以展示已经可靠核对的具体操作过程和必要结果，不能只讲通用道理后把尚未解释的关键操作丢给孩子。"
        "题目要求最少、最多或最优时，区分可行方案和覆盖允许其他方案的界限理由；"
        "不能把选定布局缺几块直接当全局最少证明。用孩子能懂的本题依据说明为什么换拼法或布局"
        "也不能更省，不固定证明样式，不堆因子枚举。完整请求应尽量讲完这项必要理由后再作最优结论；"
        "若确实只能核实一种可行方案或所选布局的下界，说清这一范围，不假称已经证明所有方案。"
        "教师已核对的推导依据不是要逐字念给孩子的口头稿。复杂分类和完整界限证据优先整理成"
        "role=prose的结构化短板书：各类怎么分、最多能保留多少或至少要改变什么、为什么，"
        "每行只写可核对的关键关系，保留覆盖其他允许方案的证据。口头指着当前图或这份短板书"
        "解释关键比较与因果，不逐分支朗读全部算式和分类清单，也不能只报结论让孩子猜中间依据。"
        "同一证明阶段可分连续语音片共用步骤编号，先讲比较标准，再讲怎样从板书排除更省的可能，"
        "让孩子能跟图听清；不要为全面性把后台推导变成长篇口述穷举。已有移动示意继续保留，"
        "新分类优先用短文字板书；若确需新增矢量图，必须安排在不覆盖既有图形的区域。"
        "完整搬移或作图示范须说明保留主体或选取起点的可见依据，不要求额外的全局最优证明。"
        "源对象与目标位置应引用已有可靠区域并在语音中对应指认；region只供圈框、短批注和标明搬入示意的方向箭头，不能作精确几何端点。"
        "无法精确落原图时，局部示意要保留当前证据已核对的相对方向、层或排，用源与目标标签说明对应，"
        "图上的上下、前后须与叙述和图例一致；分层图上层置上、下层置下，不能只靠反向文字补救。"
        "再检查操作后的形状或关系；不能用无名方框和几条箭头冒充可照做的完整示范。"
        "若相对位置或目标结构也未核对，就明确该项限制并澄清，不补隐藏结构、数量或猜端点。"
        "若隐藏结构或数量无法可靠核验，应明确指出哪一项仍不确定并澄清，不能编造完整解答。"
        "只有题干已可靠核对，才能画与题意对应的草稿。"
    )
    instruction += teaching_guidance(age_band)
    instruction += teaching_scope_guidance(level, goal)
    instruction += (
        "本轮答案许可：完整解法可靠且明确请求完整示范，允许必要计算、最终结论和回代检查；"
        "结论必须来自当前已核对题目，讲清为什么成立，再留独立应用或解释任务。"
        if allow_final_answer else
        "本轮答案许可：不展示最终答案；完整讲清当前方法和关键因果，尊重提示或不要答案的请求。")
    instruction += "教材配置仅供术语参考，未知时不得宣称课程同步；题干和图形仍以当前纸面为准。"
    if not allow_diagram:
        instruction += (
            "本轮完整解法未通过可靠性核验，禁止重建完整题图、推测原图连接或展示数值推导。"
            "缺完整解法不等于禁止概念示意：若当前题干已可靠核对，且其中明确的条件或概念足以"
            "支持一个局部关系，可只画这一个最小概念示意；它不能声称复现原图位置或全部连接。"
            "已核对条件的文字板书可以保留：用role=prose逐字写题干条件，可加题目条件前缀；"
            "仅复制当前给定数值，不写未经核验的中间结果或算式。纯文字板书无需伪造几何图形。"
            "若画局部概念图，每行额外提供scratch_basis数组，逐字引用当前纸面题干中支持该局部关系的原文，"
            "不能引用孩子的请求、历史记录或未核验解法。scratch必须同时含实际图形和写着"
            "示意图的文字标识，语音也明确说这是示意图；图中文字不标数字、数值或算式。"
            "孩子明确请求画图且条件足以支持时，给这一个局部示意，不能仅用不能精确描原图作答。"
            "若题干可读置信度不足0.85、没有可引用依据或局部关系仍不确定，scratch=[]并简短澄清。"
            "原图仍只允许已有可靠锚点的标记，不能借示意许可在原图补点、猜线。"
        )
        if goal == "worked_example":
            instruction += (
                "本次请求是完成当前题的具体解法，不是泛讲概念。若解题步骤还在核对，"
                "不要拿无关的正方体、三角形等概念图或定义冒充这道题的解题步骤。"
                "只简短回应仍需核对的具体关系；若确有模糊、缺失或矛盾，指出哪个可见位置和哪条条件，"
                "最多问一个能解决该缺口的问题。不要对孩子说题目不可靠、置信度或后台核验等内部术语，"
                "也不能因为立体图存在正常遮挡就一律要求看见里面或背面。"
                "常规积木题可在题干和图示支持时明确采用实心堆叠、逐列或逐层计数的约定，"
                "但不能暗中添加空洞、隐藏块数或连接关系；若不同合理约定确实给不同结果，才具体澄清。"
            )
    instruction += ("teaching_move只能是clarify_target/answer_concept/give_clue/review_attempt/"
                    "wait_for_writing/parent_handoff；reply_mode只能是spoken/writing/none。"
                    "只有确实交回孩子口述或动笔时才结束本轮；教师板书或自问自答不是孩子回合，"
                    "用reply_mode=none，继续讲完必要因果。不要在方法未解释清楚时机械抛任务结束。")
    details = (f"年龄段:{age_band or '未知'}；年级:{grade or '未知'}；教材:{textbook or '未知'}；"
               f"孩子本次问题:{question[:500]}；"
               f"题目:{target['label']}；纸面题干:{target['evidence']}；"
               f"题干可读置信度:{target.get('read_confidence', target.get('confidence', 0))}；"
               f"教师内部解题依据:{str(solution.get('steps') or '')[:1200] if allow_diagram else '完整解法未核验，不作示意依据'}；"
               f"已核对答案（仅按本轮答案许可使用）:{answer[:300] if allow_diagram else '未核验，不提供'}；提示层级:{level}；"
               f"上一轮提示:{previous_hint[:300]}；孩子刚才说:{student_thought[:300]}。"
               f"近期教学记录（不是当前答案证据）:{session_context[:1500]}；"
               f"口述核验（不是掌握证明）:{json.dumps(assessment or {}, ensure_ascii=False)}；"
               f"当次照片文字、点边及可见区域锚点:{json.dumps(target.get('anchors') or [], ensure_ascii=False)}；"
               "第一图为整页，第二图为局部题目。")
    payload = {"model": settings.ai_model, "temperature": .2, "messages": [
        {"role": "system", "content": instruction},
        {"role": "user", "content": [
            {"type": "text", "text": details},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(page_image).decode()}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(focus_image).decode()}},
        ]},
    ]}
    pending = ""
    unparsed = []
    count = 0
    reading_marks: list[dict] = []
    for delta in (model_stream or stream_model)(settings, payload):
        pending += delta
        if len(pending) > 64000:
            break
        while "\n" in pending and count < MAX_TEACHING_BEATS:
            line, pending = pending.split("\n", 1)
            try:
                raw = json.loads(line.strip())
                beat = normalized_teaching_beat(raw, focus_region,
                                                target["box"], answer, target.get("anchors"), True,
                                                allow_final_answer, reliable, allow_region_operations)
                if unresolved_full and beat:
                    beat = beat if beat["teaching_move"] in {"clarify_target", "parent_handoff"} else None
                    if beat:
                        beat["scratch"] = []
            except (ValueError, TypeError):
                beat = None
            if not beat and count == 0:
                unparsed.append(line + "\n")
            if beat:
                if not allow_diagram:
                    beat["scratch"] = [] if unresolved_full else local_concept_scratch(raw, beat, target)
                    beat["page"] = [mark for mark in beat["page"] if mark.get("anchor_id")]
                elif not beat["scratch"] and goal == "scaffold":
                    beat["scratch"] = step_board(beat["speech"], beat)
                beat = _spoken_reasoning_board(beat, target, goal, answer, allow_final_answer, raw)
                beat = _complete_lesson_turn(beat, complete)
                beat = _first_reading_beat(beat, page_image, focus_region, target, answer,
                                           allow_final_answer, reliable, allow_region_operations, reading_marks)
                beat = _spoken_operation_notes(beat, raw, page_image, focus_region, target, answer,
                                               allow_final_answer, reliable, allow_region_operations)
                count += 1
                yield "beat", beat
                if (beat["reply_mode"] in {"spoken", "writing"} or beat["needs_parent"]
                        or beat["teaching_move"] == "wait_for_writing"
                        or (complete and beat["teaching_move"] == "clarify_target")
                        or count >= MAX_TEACHING_BEATS):
                    return
    if count < MAX_TEACHING_BEATS and pending.strip():
        try:
            raw = json.loads(pending.strip())
            beat = normalized_teaching_beat(raw, focus_region,
                                            target["box"], answer, target.get("anchors"), True,
                                            allow_final_answer, reliable, allow_region_operations)
            if unresolved_full and beat:
                beat = beat if beat["teaching_move"] in {"clarify_target", "parent_handoff"} else None
                if beat:
                    beat["scratch"] = []
        except (ValueError, TypeError):
            beat = None
        if beat:
            if not allow_diagram:
                beat["scratch"] = [] if unresolved_full else local_concept_scratch(raw, beat, target)
                beat["page"] = [mark for mark in beat["page"] if mark.get("anchor_id")]
            elif not beat["scratch"] and goal == "scaffold":
                beat["scratch"] = step_board(beat["speech"], beat)
            beat = _spoken_reasoning_board(beat, target, goal, answer, allow_final_answer, raw)
            beat = _complete_lesson_turn(beat, complete)
            beat = _first_reading_beat(beat, page_image, focus_region, target, answer,
                                       allow_final_answer, reliable, allow_region_operations, reading_marks)
            beat = _spoken_operation_notes(beat, raw, page_image, focus_region, target, answer,
                                           allow_final_answer, reliable, allow_region_operations)
            count += 1
            yield "beat", beat
    if count == 0:
        if unresolved_full:
            yield "beat", unresolved_teaching_beat(target)
            return
        # Older text generators may ignore JSONL. Do not display malformed markup.
        plain = ("".join(unparsed) + pending).strip()
        if plain and not any(token in plain for token in ("{", "}", chr(96))):
            for part in fallback_teaching_beats(plain, target["box"]):
                beat = normalized_teaching_beat(part, focus_region, target["box"], answer,
                                                target.get("anchors"), True, allow_final_answer)
                if beat:
                    if goal in {"explain_method", "worked_example", "independent_attempt"}:
                        # These notes were made by the plain-text splitter, not
                        # supplied as a provider board; apply the same reason gate.
                        beat["scratch"] = []
                    elif allow_diagram:
                        beat["scratch"] = step_board(beat["speech"], beat)
                    elif not allow_diagram:
                        beat["scratch"] = local_concept_scratch(part, beat, target)
                    beat = _spoken_reasoning_board(beat, target, goal, answer, allow_final_answer,
                                                   {}, mode="plain_fallback")
                    beat = _complete_lesson_turn(beat, complete)
                    beat = _first_reading_beat(beat, page_image, focus_region, target, answer,
                                               allow_final_answer, reliable, allow_region_operations, reading_marks)
                    count += 1
                    yield "beat", beat
                    if beat["reply_mode"] == "spoken":
                        return
        else:
            yield "fallback", plain[:900]
