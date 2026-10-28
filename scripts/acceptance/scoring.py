"""Independent rules for synthetic evidence; uncertain semantics require human review."""

from __future__ import annotations

import math
import re


def _check(name: str, status: str, reason: str) -> dict:
    return {"name": name, "status": status, "reason": reason}


def _overlap(first: list, second: list) -> bool:
    return max(first[0], second[0]) <= min(first[0] + first[2], second[0] + second[2]) and max(
        first[1], second[1]
    ) <= min(first[1] + first[3], second[1] + second[3])


def _center(box: list) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _distance(first, second) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _valid_box(box) -> bool:
    return (
        isinstance(box, list)
        and len(box) == 4
        and all(
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
            for value in box
        )
        and 0 <= box[0] <= 1
        and 0 <= box[1] <= 1
        and box[2] > 0
        and box[3] > 0
        and box[0] + box[2] <= 1.01
        and box[1] + box[3] <= 1.01
    )


def _spoken_text(response: dict) -> str:
    pieces = [str(response.get("response") or "")]
    for beat in response.get("visual_beats") or []:
        pieces.extend(
            str(element.get("text") or "")
            for element in beat.get("scratch") or []
            if element.get("kind") == "text"
        )
    return "\n".join(pieces)


def _expected_text_boxes(case: dict, text: str) -> list[list]:
    exact = [
        region["box"] for region in case["oracle"]["regions"] if text and text == region["text"]
    ]
    if exact or not text:
        return exact
    boxes = []
    for region in case["oracle"]["regions"]:
        offsets = region.get("char_x") or []
        if len(offsets) != len(region["text"]) + 1:
            continue
        start = region["text"].find(text)
        while start >= 0:
            boxes.append(
                [
                    offsets[start],
                    region["box"][1],
                    offsets[start + len(text)] - offsets[start],
                    region["box"][3],
                ]
            )
            start = region["text"].find(text, start + len(text))
    return boxes


def _leaks_answer(text: str, answer: str, unit: str) -> bool:
    chinese = {"4": "四", "22": "二十二", "75": "七十五"}.get(answer)
    escaped = "(?:" + "|".join(re.escape(value) for value in (answer, chinese) if value) + ")"
    if re.search(
        r"(?:答案|结果|所以|还剩|每份|一份|周长|等于|得)[^。？?\n]{0,10}" + escaped + r"(?!\d)",
        text,
    ):
        return True
    return bool(
        re.search(r"[=＝]\s*" + escaped + r"(?!\d)", text)
        or (unit and re.search(r"(?<!\d)" + escaped + r"\s*" + re.escape(unit), text))
    )


def _mark_checks(case: dict, response: dict) -> list[dict]:
    checks = []
    marks = [mark for beat in response.get("visual_beats") or [] for mark in beat.get("page") or []]
    points = list(case.get("geometry", {}).get("points", {}).values())
    for index, mark in enumerate(marks):
        kind = mark.get("kind")
        name = f"paper_mark_{index + 1}"
        if kind in {"line", "arrow"} and all(key in mark for key in ("x1", "y1", "x2", "y2")):
            if not all(
                isinstance(mark[key], int | float)
                and not isinstance(mark[key], bool)
                and math.isfinite(mark[key])
                and 0 <= mark[key] <= 1
                for key in ("x1", "y1", "x2", "y2")
            ):
                checks.append(_check(name, "fail", "端点坐标格式无效。"))
                continue
            if not points:
                checks.append(_check(name, "needs_review", "本题未定义可判定的几何端点真值。"))
                continue
            distances = [
                min(_distance(point, expected) for expected in points)
                for point in ((mark["x1"], mark["y1"]), (mark["x2"], mark["y2"]))
            ]
            status = (
                "fail"
                if max(distances) > 0.12
                else "pass"
                if max(distances) <= 0.065
                else "needs_review"
            )
            checks.append(
                _check(name, status, f"端点距合成真值最大偏移 {max(distances):.3f}（整页归一化）。")
            )
        elif mark.get("box"):
            if not _valid_box(mark["box"]):
                checks.append(_check(name, "fail", "标记框坐标格式无效。"))
                continue
            text = str(mark.get("text") or "").strip()
            if kind == "label" and text in case.get("geometry", {}).get("points", {}):
                expected = case["geometry"]["points"][text]
                distance = _distance(_center(mark["box"]), expected)
                status = (
                    "fail" if distance > 0.12 else "pass" if distance <= 0.065 else "needs_review"
                )
                checks.append(_check(name, status, f"标签距真实点偏移 {distance:.3f}。"))
                continue
            candidates = _expected_text_boxes(case, text)
            if not candidates:
                checks.append(_check(name, "needs_review", "标记未提供可与合成词框匹配的原文。"))
                continue
            distance = min(_distance(_center(mark["box"]), _center(box)) for box in candidates)
            overlap = any(_overlap(mark["box"], box) for box in candidates)
            status = (
                "pass"
                if overlap or distance <= 0.065
                else "fail"
                if distance > 0.12
                else "needs_review"
            )
            checks.append(_check(name, status, f"词框最近中心偏移 {distance:.3f}。"))
    if case["oracle"].get("expects_marks") and not marks:
        checks.append(
            _check(
                "requested_paper_marks", "needs_review", "孩子明确请求圈线，但本轮没有原图笔迹。"
            )
        )
    return checks


def evaluate(case: dict, response: dict, *, error: str | None = None) -> dict:
    checks = []
    if error:
        return {
            "status": "fail",
            "checks": [_check("completed", "fail", error)],
            "limitation": "调用失败，未评定教学内容。",
        }
    oracle = case["oracle"]
    if case["mode"] == "correction":
        judgments = response.get("checks") or []
        if not judgments:
            checks.append(_check("grading_present", "fail", "没有返回本题批改项。"))
        else:
            for index, judgment in enumerate(judgments):
                verdict = judgment.get("status")
                if verdict == "unclear":
                    status = "needs_review"
                else:
                    status = "pass" if verdict in oracle["allowed_statuses"] else "fail"
                checks.append(
                    _check(
                        f"grading_{index + 1}",
                        status,
                        f"期望 {oracle['allowed_statuses']}，实际 {verdict}。",
                    )
                )
                expected_answer = oracle.get("correct_answer")
                reference = str(judgment.get("reference_answer") or "")
                if expected_answer and reference:
                    numbers = re.findall(r"-?\d+(?:\.\d+)?", reference)
                    reference_status = (
                        "pass"
                        if numbers and numbers[-1] == expected_answer
                        else "fail"
                        if numbers
                        else "needs_review"
                    )
                    checks.append(
                        _check(
                            f"reference_answer_{index + 1}",
                            reference_status,
                            "独立计算核对参考答案，不能仅凭批改状态正确就放过错误解答。",
                        )
                    )
                expected_written = str(oracle.get("written_answer") or "")
                observed = str(judgment.get("answer") or "")
                expected_numbers = re.findall(r"-?\d+(?:\.\d+)?", expected_written)
                observed_numbers = re.findall(r"-?\d+(?:\.\d+)?", observed)
                if expected_numbers and observed_numbers:
                    checks.append(
                        _check(
                            f"read_written_answer_{index + 1}",
                            "pass" if expected_numbers == observed_numbers else "fail",
                            "独立核对合成纸上实际写出的数字。",
                        )
                    )
                if judgment.get("box") and oracle.get("answer_box"):
                    box, expected = judgment["box"], oracle["answer_box"]
                    if not _valid_box(box):
                        checks.append(
                            _check(f"grading_location_{index + 1}", "fail", "答案框格式无效。")
                        )
                        continue
                    distance = _distance(_center(box), _center(expected))
                    status = "fail" if distance > 0.2 and not _overlap(box, expected) else "pass"
                    checks.append(
                        _check(
                            f"grading_location_{index + 1}",
                            status,
                            f"答案框中心偏移 {distance:.3f}；检测大幅错位。",
                        )
                    )
        if oracle.get("open_ended"):
            checks.append(
                _check(
                    "open_expression_quality",
                    "needs_review",
                    "规则可排除误判为错误；具体表达建议是否合适仍须人工复核。",
                )
            )
    else:
        text = _spoken_text(response)
        checks.append(
            _check(
                "target_located",
                "pass" if response.get("located") else "needs_review",
                "核对是否找到这张清晰合成纸的第1题。",
            )
        )
        if not text.strip():
            checks.append(_check("response_present", "fail", "没有可供孩子使用的提示。"))
        answer = oracle.get("withhold_answer")
        if answer:
            leaked = _leaks_answer(text, answer, oracle.get("answer_unit", ""))
            checks.append(
                _check(
                    "withheld_final_answer",
                    "fail" if leaked else "pass",
                    "检测明确的最终答案泄露；非标准写法仍需人工检查。",
                )
            )
        if oracle.get("incorrect_thought"):
            affirmed = re.search(
                r"完全正确|回答正确|答案正确|答对了|(?:这个|你的|这条)思路(?:是)?正确", text
            )
            checks.append(
                _check(
                    "no_false_strong_confirmation",
                    "fail" if affirmed else "pass",
                    "孩子给定思路错误，不能作整体正确确认。",
                )
            )
        for index, alternatives in enumerate(oracle.get("concept_groups", [])):
            matched = any(word in text for word in alternatives)
            checks.append(
                _check(
                    f"responds_to_concept_{index + 1}",
                    "pass" if matched else "needs_review",
                    f"是否回应卡点：{oracle.get('concept', '')}；词义规则只作筛查。",
                )
            )
        checks.extend(_mark_checks(case, response))
    status = (
        "fail"
        if any(item["status"] == "fail" for item in checks)
        else "needs_review"
        if any(item["status"] == "needs_review" for item in checks)
        else "pass"
    )
    return {
        "status": status,
        "checks": checks,
        "limitation": "独立规则只判断明确事实与硬门槛，不代表真人教师水平或学习效果；复杂语义须人工复核。",
    }
