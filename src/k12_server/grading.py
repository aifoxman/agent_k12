"""Readable page regions and conservative merging for full-page grading."""

from __future__ import annotations

import io
from collections.abc import Iterable

from PIL import Image, UnidentifiedImageError

from .ai import normalized_box, normalized_mark_box

Region = tuple[float, float, float, float]


def focus_question_image(image_data: bytes, box: list[float] | None) -> tuple[bytes, list[float]]:
    """Enlarge a saved question area while retaining the full page as separate context."""
    located = normalized_box(box)
    if not located:
        return image_data, [0, 0, 1, 1]
    try:
        with Image.open(io.BytesIO(image_data)) as image:
            width, height = image.size
            x, y, box_width, box_height = located
            center_x, center_y = x + box_width / 2, y + box_height / 2
            # Include nearby printed instructions and the other end of long marks.
            focus_width = min(1, max(.56, box_width * 2.4))
            focus_height = min(1, max(.34, box_height * 2.7))
            left = max(0, min(1 - focus_width, center_x - focus_width / 2))
            top = max(0, min(1 - focus_height, center_y - focus_height / 2))
            pixel_box = (
                round(left * width), round(top * height),
                round((left + focus_width) * width),
                round((top + focus_height) * height),
            )
            crop = image.convert("RGB").crop(pixel_box)
            if crop.width < 1000:
                scale = min(4, 1000 / crop.width)
                crop = crop.resize((round(crop.width * scale), round(crop.height * scale)),
                                   Image.Resampling.LANCZOS)
            output = io.BytesIO()
            crop.save(output, format="JPEG", quality=88)
            return output.getvalue(), [round(left, 4), round(top, 4),
                                       round(focus_width, 4), round(focus_height, 4)]
    except (UnidentifiedImageError, OSError, ValueError):
        return image_data, [0, 0, 1, 1]


def map_hint_cue(cue: dict | None, focus_region: list[float],
                 target_box: list[float]) -> dict | None:
    """Map one grounded teaching mark from the crop back to its frozen page."""
    if not cue or not normalized_mark_box(cue.get("box")):
        return None
    x, y, width, height = cue["box"]
    left, top, region_width, region_height = focus_region
    mapped = normalized_mark_box([
        left + x * region_width, top + y * region_height,
        width * region_width, height * region_height,
    ])
    if not mapped:
        return None
    target_x, target_y, target_width, target_height = target_box
    if not (target_x - .03 <= mapped[0] and target_y - .03 <= mapped[1]
            and mapped[0] + mapped[2] <= target_x + target_width + .03
            and mapped[1] + mapped[3] <= target_y + target_height + .03):
        return None
    return {**cue, "box": mapped}


def correction_regions(image_data: bytes) -> list[tuple[str, Region, bytes]]:
    """Zoom into both halves of a portrait worksheet while keeping its original image."""
    try:
        with Image.open(io.BytesIO(image_data)) as image:
            width, height = image.size
            # A readable near-square page may contain one question whose diagram
            # and answer space fall on opposite sides of a half-page crop.
            if (width < 300 or height < 500 or height <= width * 1.05
                    or (width >= 900 and height <= width * 1.4)):
                return [("整页", (0, 0, 1, 1), image_data)]
            if width * height > 16_000_000:
                return [("整页", (0, 0, 1, 1), image_data)]
            source = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return [("整页", (0, 0, 1, 1), image_data)]
    regions = [("上半页", (0, 0, 1, .55)), ("下半页", (0, .45, 1, .55))]
    output = []
    for name, box in regions:
        top, bottom = round(height * box[1]), round(height * (box[1] + box[3]))
        crop = source.crop((0, top, width, bottom))
        if crop.width < 1100:
            scale = min(2, 1100 / crop.width)
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        crop.save(buffer, format="JPEG", quality=88)
        output.append((name, box, buffer.getvalue()))
    return output


def _full_box(box: list[float] | None, region: Region) -> list[float] | None:
    if box is None:
        return None
    return normalized_box([
        region[0] + box[0] * region[2],
        region[1] + box[1] * region[3],
        box[2] * region[2],
        box[3] * region[3],
    ])


def _same_answer_area(a: dict, b: dict) -> bool:
    first, second = a.get("box"), b.get("box")
    if not first or not second:
        return False
    center_a = first[1] + first[3] / 2
    center_b = second[1] + second[3] / 2
    return abs(center_a - center_b) < .1 and abs(first[0] - second[0]) < .2


def _withhold_solution(check: dict) -> None:
    check.update(reference_answer="", solution_idea="", solution_steps=[],
                 comparison="", diagram=None, steps=[])


def _flag_ambiguous_marks(checks: list[dict]) -> None:
    """Do not claim opposite verdicts for nearly the same marked answer area."""
    ambiguous = set()
    for index, first in enumerate(checks):
        a = first.get("box")
        if not a or first.get("status") not in {"looks_correct", "check_again"}:
            continue
        for other_index in range(index + 1, len(checks)):
            second = checks[other_index]
            b = second.get("box")
            if (not b or second.get("status") not in {"looks_correct", "check_again"}
                    or first["status"] == second["status"]
                    or first.get("question") == second.get("question")):
                continue
            overlap_width = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
            overlap_height = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
            smaller_area = min(a[2] * a[3], b[2] * b[3])
            if smaller_area and overlap_width * overlap_height / smaller_area > .7:
                ambiguous.update((index, other_index))
    for index in ambiguous:
        check = checks[index]
        check["status"] = "unclear"
        check["reason"] = "不同题目的批改区域高度重叠，不能可靠定位；请聚焦复检本题。" + check.get("reason", "")
        check["confidence"] = min(check.get("confidence", 0), .5)
        _withhold_solution(check)


def merge_correction_regions(items: Iterable[tuple[Region, dict]]) -> dict:
    """Map model marks to the original screenshot and flag conflicting duplicates."""
    results = list(items)
    if not results:
        return {"checks": [], "visible_answer_count": 0, "confidence": 0}
    checks: list[dict] = []
    estimated_total = 0
    for region, result in results:
        estimated_total += result.get("visible_answer_count", len(result.get("checks", [])))
        for original in result.get("checks", []):
            check = dict(original)
            check["box"] = _full_box(check.get("box"), region)
            check["steps"] = [
                {**step, "box": _full_box(step.get("box"), region)}
                for step in check.get("steps", [])
            ]
            key = "".join(check.get("question", "").split())
            duplicate = next(
                (old for old in checks if "".join(old.get("question", "").split()) == key
                 and _same_answer_area(old, check)),
                None,
            ) if key else None
            if duplicate:
                estimated_total -= 1
                if duplicate["status"] != check["status"] or duplicate.get("answer") != check.get("answer"):
                    duplicate["status"] = "unclear"
                    duplicate["reason"] = "两处局部分析不一致，需要家长复核原图。"
                    duplicate["explanation"] = "局部分析对题目或答案有分歧，不能可靠给出正确结论。"
                    duplicate["box"] = None
                    _withhold_solution(duplicate)
                elif check.get("confidence", 0) > duplicate.get("confidence", 0):
                    duplicate.update(check)
                continue
            checks.append(check)
    _flag_ambiguous_marks(checks)
    first = results[0][1]
    return {
        "checks": checks,
        "visible_answer_count": max(len(checks), estimated_total),
        "confidence": min(result.get("confidence", 0) for _, result in results),
        "subject": next((result.get("subject") for _, result in results if result.get("subject")), ""),
        "printed_page": next((result.get("printed_page") for _, result in results if result.get("printed_page")), ""),
        "response": first.get("response", ""),
    }
