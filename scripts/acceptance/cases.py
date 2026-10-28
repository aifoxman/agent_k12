"""Small synthetic worksheets; evaluator truth never belongs in production prompts."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = (1200, 900)


def definitions() -> list[dict]:
    return [
        {
            "id": "arithmetic_wrong",
            "mode": "correction",
            "subject": "数学",
            "lines": ["1. 计算：27 + 18 = （       ）"],
            "answer": "35",
            "prompt": "",
            "oracle": {
                "allowed_statuses": ["check_again"],
                "correct_answer": str(27 + 18),
                "written_answer": "35",
            },
        },
        {
            "id": "units_correct",
            "mode": "correction",
            "subject": "数学",
            "lines": ["1. 在括号里填上正确的数：", "2米 = （       ）厘米"],
            "answer": "200",
            "prompt": "",
            "oracle": {
                "allowed_statuses": ["looks_correct"],
                "correct_answer": str(2 * 100),
                "written_answer": "200",
            },
        },
        {
            "id": "fraction_concept",
            "mode": "hint",
            "subject": "数学",
            "lines": ["1. 12块饼干平均分成3份，每份有多少块？"],
            "answer": "",
            "prompt": "这里的平均分是什么意思？先解释这个词，不要直接算。",
            "student_thought": "",
            "oracle": {
                "withhold_answer": str(12 // 3),
                "answer_unit": "块",
                "concept_groups": [["一样多", "同样多", "相同", "相等", "等量", "公平"]],
                "concept": "平均分要求各份数量相等",
            },
        },
        {
            "id": "units_wrong_thought",
            "mode": "hint",
            "subject": "数学",
            "lines": ["1. 一条丝带长1米，剪去25厘米，", "还剩多少厘米？"],
            "answer": "",
            "prompt": "我想用1减25，因为它们都是长度。这样想对吗？",
            "student_thought": "我想用1减25，因为它们都是长度。",
            "oracle": {
                "withhold_answer": str(100 - 25),
                "answer_unit": "厘米",
                "incorrect_thought": True,
                "concept_groups": [["单位", "米和厘米", "同一种", "统一", "换算"]],
                "concept": "减法前需要统一长度单位",
            },
        },
        {
            "id": "language_open",
            "mode": "correction",
            "subject": "语文",
            "lines": ["1. 用“因为……所以……”写一句完整的话。"],
            "answer": "因为下雨了，所以我带了雨伞。",
            "prompt": "",
            "oracle": {
                "allowed_statuses": ["looks_correct", "needs_discussion"],
                "written_answer": "因为下雨了，所以我带了雨伞。",
                "open_ended": True,
                "reason": "因果关系完整、表达合理，允许其他合理表达",
            },
        },
        {
            "id": "geometry_guidance",
            "mode": "hint",
            "subject": "数学",
            "lines": ["1. 长方形ABCD，长8厘米，宽3厘米。", "说出求周长的方法，不必算出最后结果。"],
            "answer": "",
            "prompt": "请圈出长和宽，并指着图说明周长怎么找，先不要算结果。",
            "student_thought": "",
            "diagram": "rectangle",
            "oracle": {
                "withhold_answer": str(2 * (8 + 3)),
                "answer_unit": "厘米",
                "concept_groups": [["四条", "一周", "一圈", "长加宽", "长和宽", "两条长"]],
                "concept": "周长是四条边长度之和",
                "expects_marks": True,
            },
        },
    ]


def _font(size: int, font_path: str | None = None):
    candidates = (
        [font_path]
        if font_path
        else [
            "C:/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("A Chinese font is required; pass --font with a local font path.")


def render(case: dict, image_path: Path, font_path: str | None = None) -> dict:
    image = Image.new("RGB", SIZE, "#fffdf7")
    draw = ImageDraw.Draw(image)
    font, small = _font(38, font_path), _font(25, font_path)
    draw.rounded_rectangle((30, 30, 1170, 870), radius=12, outline="#c7c5ba", width=2)
    draw.text((65, 55), f"{case['subject']}练习", font=_font(43, font_path), fill="#1f2937")
    draw.text((65, 110), "请认真读题，保留自己的思考过程。", font=small, fill="#5f655d")
    regions = []

    def text_at(text: str, xy: tuple[int, int], role="question", color="#111827"):
        draw.text(xy, text, font=font, fill=color)
        left, top, right, bottom = draw.textbbox(xy, text, font=font)
        box = [left / SIZE[0], top / SIZE[1], (right - left) / SIZE[0], (bottom - top) / SIZE[1]]
        regions.append(
            {
                "text": text,
                "box": box,
                "role": role,
                "char_x": [
                    (xy[0] + draw.textlength(text[:index], font=font)) / SIZE[0]
                    for index in range(len(text) + 1)
                ],
            }
        )
        # Exact substring boxes let the independent evaluator check model word marks.
        for term in (
            "27",
            "18",
            "2米",
            "厘米",
            "平均分",
            "12块",
            "3份",
            "1米",
            "25厘米",
            "长",
            "宽",
            "8厘米",
            "3厘米",
            "因为",
            "所以",
        ):
            start = text.find(term)
            while start >= 0:
                x = xy[0] + draw.textlength(text[:start], font=font)
                width = draw.textlength(term, font=font)
                regions.append(
                    {
                        "text": term,
                        "box": [
                            x / SIZE[0],
                            top / SIZE[1],
                            width / SIZE[0],
                            (bottom - top) / SIZE[1],
                        ],
                        "role": "word",
                    }
                )
                start = text.find(term, start + len(term))
        return box

    for index, line in enumerate(case["lines"]):
        text_at(line, (65, 180 + index * 65))
    answer_box = None
    if case["answer"]:
        draw.text((75, 345), "我的作答：", font=small, fill="#374151")
        answer_box = text_at(case["answer"], (230, 335), role="written", color="#1e40af")
        draw.line((220, 398, 1080, 398), fill="#d3d6de", width=2)
    geometry = {}
    if case.get("diagram") == "rectangle":
        points = {"A": (240, 370), "B": (720, 370), "C": (720, 580), "D": (240, 580)}
        draw.rectangle((240, 370, 720, 580), outline="#15263d", width=4)
        for label, (x, y) in points.items():
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#15263d")
            text_at(label, (x - 28, y - 45), role="point_label")
        text_at("8厘米", (425, 305), role="diagram_label")
        text_at("3厘米", (755, 445), role="diagram_label")
        geometry["points"] = {key: [x / SIZE[0], y / SIZE[1]] for key, (x, y) in points.items()}
        geometry["segments"] = [
            [geometry["points"][a], geometry["points"][b]]
            for a, b in (("A", "B"), ("B", "C"), ("C", "D"), ("D", "A"))
        ]
    draw.text((65, 810), "题目下方留空，供你继续尝试。", font=small, fill="#6b7280")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(image_path, format="JPEG", quality=95)
    return {
        **case,
        "target": "第1题",
        "image": f"papers/{case['id']}.jpg",
        "oracle": {**case["oracle"], "regions": regions, "answer_box": answer_box},
        "geometry": geometry,
    }
