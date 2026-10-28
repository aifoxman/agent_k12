"""Conservative position evidence; pixels alone never verify word or point identity."""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageFilter, UnidentifiedImageError


def tight_teaching_anchor_image(image_bytes: bytes, target_box: list[float]) -> tuple[bytes, list[float]]:
    """Crop with context, using actual pixel boundaries for the inverse transform."""
    try:
        with Image.open(BytesIO(image_bytes)) as picture:
            width, height = picture.size
            x, y, w, h = (float(value) for value in target_box)
            if not all(math.isfinite(value) for value in (x, y, w, h)) or w <= 0 or h <= 0:
                raise ValueError("invalid target")
            pad_x, pad_y = max(4, w * width * .08), max(4, h * height * .08)
            left, top = max(0, math.floor(x * width - pad_x)), max(0, math.floor(y * height - pad_y))
            right = min(width, math.ceil((x + w) * width + pad_x))
            bottom = min(height, math.ceil((y + h) * height + pad_y))
            if right - left < 8 or bottom - top < 8:
                raise ValueError("empty crop")
            crop = picture.convert("RGB").crop((left, top, right, bottom))
            scale = min(4, 1200 / max(crop.size))
            if scale > 1:
                crop = crop.resize((round(crop.width * scale), round(crop.height * scale)),
                                   Image.Resampling.LANCZOS)
            output = BytesIO()
            crop.save(output, format="JPEG", quality=94)
            return output.getvalue(), [left / width, top / height,
                                       (right - left) / width, (bottom - top) / height]
    except (OSError, UnidentifiedImageError, ValueError, TypeError, ZeroDivisionError):
        return image_bytes, [0, 0, 1, 1]


class _Ink:
    def __init__(self, picture: Image.Image, target_box: list[float]):
        self.image = picture.convert("L")
        self.width, self.height = self.image.size
        self.pixels = self.image.load()
        self.radius = min(36, max(5, round(math.hypot(
            target_box[2] * self.width, target_box[3] * self.height) * .08)))

    def gray(self, x: float, y: float) -> int:
        ix, iy = round(x), round(y)
        return self.pixels[ix, iy] if 0 <= ix < self.width and 0 <= iy < self.height else 255

    def contrast(self, x: float, y: float, nx: float, ny: float) -> float:
        center = min(self.gray(x + nx * step, y + ny * step) for step in (-1, 0, 1))
        background = (self.gray(x + nx * 5, y + ny * 5)
                      + self.gray(x - nx * 5, y - ny * 5)) / 2
        return max(0, background - center) if center < 180 else 0

    def line_quality(self, ends: tuple[float, float, float, float]) -> float:
        x1, y1, x2, y2 = ends
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 12:
            return 0
        nx, ny = -(y2 - y1) / length, (x2 - x1) / length
        count = min(64, max(12, round(length / 4)))
        contrasts = [self.contrast(x1 + (x2 - x1) * i / (count - 1),
                                   y1 + (y2 - y1) * i / (count - 1), nx, ny)
                     for i in range(count)]
        coverage = sum(value >= 40 for value in contrasts) / count
        return coverage * .9 + min(1, sum(contrasts) / count / 180) * .1

    def fitted_segments(self, original: tuple[float, float, float, float]) -> list[tuple]:
        """Fit small direction errors to nearby continuous ink, never extend a segment.

        The model still supplies the segment and its approximate direction. Sampling
        a narrow normal band once keeps the angular search bounded; it is not a
        page-wide line detector and cannot invent a construction from end dots.
        """
        x1, y1, x2, y2 = original
        length = math.hypot(x2 - x1, y2 - y1)
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        nx, ny = -uy, ux
        count = min(64, max(20, round(length / 4)))
        rows = []
        for index in range(count):
            tangent = length * index / (count - 1)
            runs, run = [], []
            for normal in range(-self.radius, self.radius + 1):
                contrast = self.contrast(x1 + ux * tangent + nx * normal,
                                         y1 + uy * tangent + ny * normal, nx, ny)
                if contrast >= 40:
                    run.append((normal, contrast))
                elif run:
                    runs.append(run)
                    run = []
            if run:
                runs.append(run)
            centers = [(sum(n * c for n, c in run) / sum(c for _, c in run))
                       for run in runs]
            rows.append((tangent, centers))

        candidates = []
        max_slope = math.tan(math.radians(4))
        for half_degree in range(-8, 9):
            slope = math.tan(math.radians(half_degree / 2))
            for middle in range(-self.radius, self.radius + 1, 2):
                if abs(middle) + abs(slope) * length / 2 > self.radius:
                    continue
                supports = []
                for tangent, centers in rows:
                    predicted = middle + slope * (tangent - length / 2)
                    nearest = min(centers, key=lambda normal: abs(normal - predicted), default=None)
                    if nearest is not None and abs(nearest - predicted) <= 2.5:
                        supports.append((tangent, nearest))
                if len(supports) / count < .88:
                    continue
                # Least-squares direction is taken from observed stroke centers.
                # Refit after dropping isolated intersections or glyph fragments.
                for _ in range(2):
                    mean_t = sum(t for t, _ in supports) / len(supports)
                    mean_n = sum(n for _, n in supports) / len(supports)
                    denominator = sum((t - mean_t) ** 2 for t, _ in supports)
                    if not denominator:
                        break
                    fitted_slope = sum((t - mean_t) * (n - mean_n)
                                       for t, n in supports) / denominator
                    intercept = mean_n - fitted_slope * mean_t
                    supports = [(t, n) for t, n in supports
                                if abs(n - intercept - fitted_slope * t) <= 1.75]
                    if len(supports) / count < .88:
                        break
                if len(supports) / count < .88 or not denominator:
                    continue
                first, last = intercept, intercept + fitted_slope * length
                if abs(fitted_slope) > max_slope or max(abs(first), abs(last)) > self.radius:
                    continue
                ends = (x1 + nx * first, y1 + ny * first,
                        x2 + nx * last, y2 + ny * last)
                score = self.line_quality(ends)
                if score >= .88:
                    candidates.append((score, max(abs(first), abs(last)),
                                       (first + last) / 2, 0, ends))
        return candidates

    def segment(self, anchor: dict) -> dict | None:
        original = tuple(anchor[name] * (self.width if name.startswith("x") else self.height)
                         for name in ("x1", "y1", "x2", "y2"))
        if self.line_quality(original) >= .96:
            return anchor
        x1, y1, x2, y2 = original
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 20:
            return None
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        nx, ny = -uy, ux
        candidates = []
        # Translation follows observed ink; no horizontal/vertical assumptions or new lines.
        for normal in range(-self.radius, self.radius + 1, 2):
            for tangent in range(-self.radius, self.radius + 1, 3):
                dx, dy = normal * nx + tangent * ux, normal * ny + tangent * uy
                ends = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
                score = self.line_quality(ends)
                if score >= .88:
                    candidates.append((score, math.hypot(dx, dy), normal, tangent, ends))
        candidates.extend(self.fitted_segments(original))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0] - item[1] * .0005), reverse=True)
        best = candidates[0]
        if any((max(abs((item[4][i] - best[4][i]) * nx
                        + (item[4][i + 1] - best[4][i + 1]) * ny) for i in (0, 2)) >= 6
                or (abs(item[2] - best[2]) < 4 and abs(item[3] - best[3]) >= length * .65))
               and item[0] >= best[0] - .035 for item in candidates):
            return None  # Two plausible nearby strokes: never pick one by geometry semantics.
        bx1, by1, bx2, by2 = best[4]
        fitted_length = math.hypot(bx2 - bx1, by2 - by1)
        ux, uy = (bx2 - bx1) / fitted_length, (by2 - by1) / fitted_length
        nx, ny = -uy, ux
        # Only trim unsupported overhang. Never extend a line into a computed construction.
        samples = [(i / max(1, round(fitted_length)), self.contrast(
            bx1 + (bx2 - bx1) * i / max(1, round(fitted_length)),
            by1 + (by2 - by1) * i / max(1, round(fitted_length)), nx, ny))
                   for i in range(round(fitted_length) + 1)]
        supported = [fraction for fraction, contrast in samples if contrast >= 40]
        if not supported:
            return None
        start, end = min(supported), max(supported)
        interior = [contrast >= 40 for fraction, contrast in samples if start <= fraction <= end]
        longest_gap = gap = 0
        for supported_pixel in interior:
            gap = 0 if supported_pixel else gap + 1
            longest_gap = max(longest_gap, gap)
        if sum(interior) / len(interior) < .94 or longest_gap > max(3, fitted_length * .035):
            return None
        result = {**anchor, "x1": (bx1 + (bx2 - bx1) * start) / self.width,
                  "y1": (by1 + (by2 - by1) * start) / self.height,
                  "x2": (bx1 + (bx2 - bx1) * end) / self.width,
                  "y2": (by1 + (by2 - by1) * end) / self.height}
        return result

    def text(self, anchor: dict) -> dict | None:
        # No OCR here: never move a named word onto a nearby ink component.
        # A dim paper surface is not ink. Estimate its local brightness without
        # moving or expanding the supplied box, then check the darker strokes.
        x, y, w, h = anchor["box"]
        left, top = max(0, math.floor(x * self.width)), max(0, math.floor(y * self.height))
        right, bottom = min(self.width, math.ceil((x + w) * self.width)), min(self.height, math.ceil((y + h) * self.height))
        if right - left < 3 or bottom - top < 3:
            return None
        crop = self.image.crop((left, top, right, bottom))
        window = min(31, max(5, 2 * round(min(crop.size) * .4) + 1))
        paper = crop.filter(ImageFilter.MaxFilter(window)).load()
        pixels = crop.load()
        ink = [(px, py) for py in range(crop.height) for px in range(crop.width)
               if pixels[px, py] < 180
               and paper[px, py] - pixels[px, py] >= max(22, paper[px, py] * .16)]
        if len(ink) < 4 or any(px in (0, crop.width - 1) or py in (0, crop.height - 1) for px, py in ink):
            return None
        # A notebook rule alone cannot support an ordinary word. Preserve actual
        # short horizontal glyphs rather than imposing a generic glyph-height rule.
        ink_height = max(py for _, py in ink) - min(py for _, py in ink) + 1
        if (ink_height <= max(2, crop.height * .12)
                and anchor.get("text", "").strip() not in {"一", "二", "三", "-", "−", "—", "=", "＝"}):
            return None
        return anchor

    def point(self, anchor: dict) -> dict | None:
        x, y, w, h = anchor["box"]
        cx, cy = (x + w / 2) * self.width, (y + h / 2) * self.height
        if self.gray(cx, cy) >= 180:
            return None
        rays = []
        for index in range(16):
            angle = index * math.pi / 8
            ux, uy = math.cos(angle), math.sin(angle)
            support = sum(min(self.gray(cx + ux * r - uy * d, cy + uy * r + ux * d)
                              for d in (-1, 0, 1)) < 180 for r in range(3, 9))
            if support >= 5:
                rays.append(index)
        junction = any(2 <= min(abs(a - b), 16 - abs(a - b)) <= 6 for a in rays for b in rays)
        return anchor if junction else None

    def region(self, anchor: dict) -> dict | None:
        # A model names the visible object. Pixels only reject empty/flat boxes;
        # they cannot certify its identity, quantity, hidden faces or a solution.
        # Keep the supplied box: never snap an object onto a different neighbour.
        x, y, w, h = anchor["box"]
        left, top = max(0, math.floor(x * self.width)), max(0, math.floor(y * self.height))
        right, bottom = min(self.width, math.ceil((x + w) * self.width)), min(self.height, math.ceil((y + h) * self.height))
        if right - left < 4 or bottom - top < 4:
            return None
        region = self.image.crop((left, top, right, bottom))
        low, high = region.getextrema()
        if high - low < 35:
            return None
        histogram = region.histogram()
        dark = sum(histogram[:min(180, high - 25)])
        return anchor if dark >= max(4, region.width * region.height * .002) else None


def verify_anchor_pixels(image_bytes: bytes, anchors: list[dict], target_box: list[float]) -> list[dict]:
    try:
        with Image.open(BytesIO(image_bytes)) as picture:
            ink = _Ink(picture, target_box)
            verified = []
            for anchor in anchors:
                kind = anchor.get("kind", "text")
                result = (ink.segment(anchor) if kind == "segment"
                          else ink.point(anchor) if kind == "point"
                          else ink.region(anchor) if kind == "region" else ink.text(anchor))
                if result:
                    verified.append(result)
            return verified
    except (OSError, UnidentifiedImageError, ValueError, TypeError, KeyError, ZeroDivisionError):
        return []
