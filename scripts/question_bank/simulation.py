"""Deterministic camera perturbations; oracle coordinates never enter API forms."""
from __future__ import annotations

import hashlib
import io
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


def photographed(image: Image.Image, variant: str, seed: str) -> Image.Image:
    image = image.convert("RGB")
    rng = random.Random(hashlib.sha256(seed.encode()).hexdigest())
    if variant == "clean":
        return image.copy()
    if variant == "blur":
        return image.filter(ImageFilter.GaussianBlur(1.5))
    if variant == "jpeg":
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=38)
        return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")
    if variant == "shadow":
        shade = Image.new("L", image.size)
        draw = ImageDraw.Draw(shade)
        for x in range(image.width):
            draw.line((x, 0, x, image.height), fill=round(120 + 135 * x / max(1, image.width - 1)))
        return Image.composite(image, ImageEnhance.Brightness(image).enhance(.5), shade)
    if variant in {"rotation", "movement", "perspective"}:
        if variant == "rotation":
            return image.rotate(rng.choice([-3, 3]), resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        if variant == "movement":
            return image.transform(image.size, Image.Transform.AFFINE, (1, 0, -image.width * .035, 0, 1, image.height * .025), fillcolor="white")
        return image.transform(image.size, Image.Transform.PERSPECTIVE, (1, .025, 0, .01, 1, 0, .000015, -.00001), Image.Resampling.BICUBIC, fillcolor="white")
    raise ValueError("Unknown camera variant")


def student_paper(crop: Image.Image, answer: str, font_path: str | None, *, variant="clean", seed="") -> Image.Image:
    crop = crop.convert("RGB")
    width = max(1000, crop.width)
    height = round(crop.height * width / crop.width)
    crop = crop.resize((width, height))
    font_size = 38
    font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default(size=font_size)
    # The separate simulated answer area never overwrites printed source conditions.
    lines = [answer[i:i + max(1, width // (font_size + 2) - 4)]
             for i in range(0, len(answer), max(1, width // (font_size + 2) - 4))]
    answer_height = max(180, 65 + len(lines) * 54)
    sheet = Image.new("RGB", (width + 100, height + answer_height + 180), "#fdfcf8")
    draw = ImageDraw.Draw(sheet)
    draw.text((50, 20), "模拟作业 · 非真实学生笔迹", font=font, fill="#777777")
    sheet.paste(crop, (50, 90))
    top = height + 120
    draw.line((50, top - 20, width + 50, top - 20), fill="#cccccc", width=2)
    draw.text((50, top), "我的作答：", font=font, fill="#555555")
    for i, line in enumerate(lines):
        draw.text((260, top + i * 54), line, font=font, fill="#224b98")
    return photographed(sheet, variant, seed)


def rendered_text_regions(oracle: dict, image_path: Path, root: Path, variant="clean") -> list[dict]:
    """Translate original crop coordinates into the current simulated JPEG."""
    if variant != "clean" or not oracle.get("crop"):
        return [region for row in oracle.get("rows", []) for region in row.get("keyword_regions", [])] if variant == "clean" else []
    with Image.open(root / oracle["crop"]) as crop, Image.open(image_path) as sheet:
        width = max(1000, crop.width)
        height = round(crop.height * width / crop.width)
        if sheet.width != width + 100:
            return []
        regions = []
        for item in oracle.get("keyword_boxes", []):
            x, y, w, h = item["box"]
            regions.append({"text": item["text"], "box": [(50 + x * width) / sheet.width,
                            (90 + y * height) / sheet.height, w * width / sheet.width, h * height / sheet.height]})
        return regions


def percentile(values: list[float], fraction: float) -> float | None:
    values = sorted(v for v in values if math.isfinite(v))
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def safe_image(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Image escapes the run directory")
    return path
