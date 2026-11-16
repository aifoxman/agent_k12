"""Validate a client-computed page transform against both actual photographs.

No model, OCR, or third-party vision runtime is needed. A transform is merely a
proposal: textured patches distributed over the page and the selected question
must agree. Flat paper, a matching question number, and client confidence alone
never establish page identity.
"""

import math
from io import BytesIO

from PIL import Image


IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1]


def complete_answer_regions(questions: list[dict]) -> list[dict]:
    """Include the writing gap only when a reliable next item bounds its end.

    This is selection geometry, never an answer or a new printed condition.
    Do not extrapolate below the final question or across different columns.
    """
    result = []
    for item in questions:
        x, y, w, h = item["box"]
        regions = list(item.get("answer_regions") or [])
        following = []
        for other in questions:
            if other["id"] == item["id"] or other.get("read_confidence", 0) < .85:
                continue
            ox, oy, ow, _ = other["box"]
            overlap = max(0, min(x + w, ox + ow) - max(x, ox))
            if (oy >= y + h and overlap / min(w, ow) >= .7 and
                    .5 <= ow / w <= 2 and abs(x - ox) < .12):
                following.append(oy)
        if following:
            gap = min(following) - y - h
            if .015 <= gap <= .25:
                regions.append([x, y + h, w, max(0, gap - .006)])
        result.append({**item, "answer_regions": regions})
    return result


def project(matrix, x, y):
    denominator = matrix[6] * x + matrix[7] * y + matrix[8]
    if abs(denominator) < .1:
        raise ValueError("Singular page mapping")
    return ((matrix[0] * x + matrix[1] * y + matrix[2]) / denominator,
            (matrix[3] * x + matrix[4] * y + matrix[5]) / denominator)


def map_box(matrix, box):
    x, y, w, h = box
    points = [project(matrix, px, py) for px, py in
              ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
    left, top = min(p[0] for p in points), min(p[1] for p in points)
    right, bottom = max(p[0] for p in points), max(p[1] for p in points)
    if left < 0 or top < 0 or right > 1 or bottom > 1:
        raise ValueError("Question leaves the photograph")
    return [left, top, right - left, bottom - top]


def valid_transform(reference: bytes, current: bytes, matrix: list, question_box: list) -> bool:
    if (not isinstance(matrix, list) or len(matrix) != 9
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or abs(v) > 8 for v in matrix)):
        return False
    if reference == current:
        return all(abs(a - b) < 1e-6 for a, b in zip(matrix, IDENTITY, strict=True))
    try:
        map_box(matrix, question_box)
        with Image.open(BytesIO(reference)) as source, Image.open(BytesIO(current)) as destination:
            source = source.convert("L")
            destination = destination.convert("L")
            source.thumbnail((640, 640))
            destination.thumbnail((640, 640))
            old, new = source.load(), destination.load()
            matched, tested, local_matched, local_tested = [], 0, 0, 0
            qx, qy, qw, qh = question_box
            # A 2D patch defeats repeated notebook rules and single-line matches.
            def inspect(x, y):
                first, second = [], []
                for dy in (-3, 0, 3):
                    for dx in (-3, 0, 3):
                        px, py = x + dx / source.width, y + dy / source.height
                        u, v = project(matrix, px, py)
                        if not .01 < u < .99 or not .01 < v < .99:
                            continue
                        first.append(old[round(px * (source.width - 1)), round(py * (source.height - 1))])
                        second.append(new[round(u * (destination.width - 1)), round(v * (destination.height - 1))])
                if len(first) != 9 or max(first) - min(first) < 65:
                    return None
                a, b = sum(first) / 9, sum(second) / 9
                va = sum((p - a) ** 2 for p in first)
                vb = sum((p - b) ** 2 for p in second)
                return sum((p - a) * (q - b) for p, q in zip(first, second, strict=True)) / max(1, (va * vb) ** .5)
            for iy in range(2, 39, 2):
                for ix in range(2, 39, 2):
                    x, y = ix / 40, iy / 40
                    correlation = inspect(x, y)
                    if correlation is None:
                        continue
                    tested += 1
                    if correlation >= .82:
                        matched.append((x, y))
            for iy in range(12):
                for ix in range(24):
                    correlation = inspect(qx + qw * (ix + .5) / 24,
                                          qy + qh * (iy + .5) / 12)
                    if correlation is not None:
                        local_tested += 1
                        local_matched += correlation >= .82
            return (tested >= 18 and len(matched) >= 14 and len(matched) / tested >= .62
                    and local_tested >= 8 and local_matched / local_tested >= .72
                    and max(x for x, _ in matched) - min(x for x, _ in matched) >= .25
                    and max(y for _, y in matched) - min(y for _, y in matched) >= .25)
    except (OSError, ValueError, ZeroDivisionError):
        return False


def stroke_scores(questions, strokes):
    """Length fraction in the union of stem and answer areas, not box size."""
    scores = []
    for question in questions:
        regions = [question["box"], *(question.get("answer_regions") or [])]
        inside = lambda x, y: any(bx <= x <= bx + bw and by <= y <= by + bh
                                 for bx, by, bw, bh in regions)
        total = hit = 0.0
        endpoints = 0
        for stroke in strokes:
            if stroke:
                endpoints += inside(*stroke[-1])
            for a, b in zip(stroke, stroke[1:]):
                length = math.dist(a, b)
                samples = max(1, math.ceil(length * 400))
                total += length
                hit += length * sum(inside(a[0] + (b[0] - a[0]) * (i + .5) / samples,
                                          a[1] + (b[1] - a[1]) * (i + .5) / samples)
                                    for i in range(samples)) / samples
        if total:
            scores.append((hit / total, endpoints, question))
    return sorted(scores, key=lambda item: (item[0], item[1]), reverse=True)
