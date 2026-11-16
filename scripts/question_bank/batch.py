"""Compose original question crops into an explicitly simulated multi-question page."""
from __future__ import annotations

import hashlib
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from scripts.question_bank.corpus import dump, read


def prepare(output: Path, font_path="C:/Windows/Fonts/msyh.ttc"):
    manifest = read(output / "manifest.json")
    cases = []
    for grade in range(1, 7):
        source = [c for c in manifest["cases"] if c["grade"] == grade and c["subject"] == "math"][:3]
        if len(source) < 3:
            continue
        pieces, rows = [], []
        font = ImageFont.truetype(font_path, 36)
        y = 100
        for index, (case, mode) in enumerate(zip(source, ("correct", "wrong", "blank"), strict=True)):
            oracle = read(output / "private" / "oracles" / f"{case['id']}.json")
            crop = Image.open(output / oracle["crop"]).convert("RGB")
            crop = crop.resize((1280, round(crop.height * 1280 / crop.width)))
            written = oracle["answer"] if mode == "correct" else oracle["wrong_answer"] if mode == "wrong" else ""
            lines = [written[i:i + 31] for i in range(0, len(written), 31)]
            height = 70 + crop.height + max(130, len(lines) * 52 + 50)
            block = Image.new("RGB", (1400, height), "white")
            draw = ImageDraw.Draw(block)
            draw.text((40, 10), f"第{index + 1}题 · 原题摘录（保留原题内容）", font=font, fill="#444444")
            block.paste(crop, (60, 65))
            answer_y = crop.height + 85
            draw.text((50, answer_y), "作答：", font=font, fill="#555555")
            for line_index, line in enumerate(lines):
                draw.text((180, answer_y + line_index * 52), line, font=font, fill="#224b98")
            draw.line((40, height - 2, 1360, height - 2), fill="#aaaaaa", width=2)
            pieces.append((y, block))
            rows.append({"ordinal": index + 1, "top": y, "bottom": y + height, "crop_height": crop.height, "mode": mode,
                         "source_case": case["id"], "reference": oracle})
            y += height + 25
        sheet = Image.new("RGB", (1400, max(2100, y + 80)), "#faf9f5")
        ImageDraw.Draw(sheet).text((50, 25), "题库拼页模拟 · 非实拍 / 非真实学生笔迹", font=font, fill="#666666")
        for top, image in pieces:
            sheet.paste(image, (0, top))
        identifier = "batch-g" + str(grade)
        path = output / "papers" / identifier / "mixed.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(path, quality=95)
        for row in rows:
            top, bottom = row.pop("top"), row.pop("bottom")
            row["box"] = [0, top / sheet.height, 1, (bottom - top) / sheet.height]
            row["keyword_regions"] = [{"text": item["text"], "box": [
                (60 + item["box"][0] * 1280) / 1400,
                (top + 65 + item["box"][1] * row["crop_height"]) / sheet.height,
                item["box"][2] * 1280 / 1400, item["box"][3] * row["crop_height"] / sheet.height]}
                for item in row["reference"].get("keyword_boxes", [])]
        source_hash = hashlib.sha256("|".join(c["source_sha256"] for c in source).encode()).hexdigest()
        dump(output / "private" / "oracles" / f"{identifier}.json", {"kind": "composite_original_crops",
             "reference_status": "verified_development_only", "rows": rows})
        cases.append({"id": identifier, "grade": grade, "subject": "math", "type": "multi-question-composite",
                      "split": "development", "source_sha256": source_hash, "images": {"hint": path.relative_to(output).as_posix(), "mixed": path.relative_to(output).as_posix()}})
    dump(output / "batch-manifest.json", {"simulated_camera": True, "composite": True, "cases": cases})
    return cases


def score(response: dict, oracle: dict):
    """Associate only unambiguous spatial evidence; never guess by nearest row."""
    rows = oracle["rows"]
    matched = {row["ordinal"]: [] for row in rows}
    unmatched = []
    for check in response.get("checks") or []:
        box = check.get("box")
        if (not isinstance(box, list) or len(box) != 4 or
                not all(type(v) in {int, float} and math.isfinite(v) and 0 <= v <= 1 for v in box) or box[3] <= 0):
            unmatched.append(check)
            continue
        y, height = box[1], box[3]
        candidates = [row for row in rows if max(0, min(y + height, row["box"][1] + row["box"][3]) - max(y, row["box"][1])) / height >= .7]
        if len(candidates) != 1:
            unmatched.append(check)
        else:
            matched[candidates[0]["ordinal"]].append(check)
    findings = []
    for row in rows:
        checks = matched[row["ordinal"]]
        if not checks:
            findings.append({"row": row["ordinal"], "status": "fail", "reason": "missing_coverage"})
            continue
        if row["mode"] != "blank" and any(c.get("answer_state") == "blank" for c in checks):
            state = "fail"
        elif row["mode"] != "blank" and not any(c.get("answer_state") == "written" and c.get("answer") for c in checks):
            state = "needs_review"
        elif row["mode"] == "correct":
            state = "fail" if any(c.get("status") == "check_again" for c in checks) else "pass" if all(c.get("status") == "looks_correct" for c in checks) else "needs_review"
        elif row["mode"] == "wrong":
            state = "pass" if any(c.get("status") == "check_again" for c in checks) else "fail" if all(c.get("status") == "looks_correct" for c in checks) else "needs_review"
        else:
            state = "pass" if all(c.get("answer_state") == "blank" and not c.get("answer") for c in checks) else "fail" if any(c.get("answer_state") == "written" for c in checks) else "needs_review"
        findings.append({"row": row["ordinal"], "status": state, "reason": "spatially_associated_state_and_verdict"})
    status = "fail" if any(f["status"] == "fail" for f in findings) else "needs_review" if unmatched or any(f["status"] == "needs_review" for f in findings) else "pass"
    return {"status": status, "rows": findings, "unmatched_checks": len(unmatched),
            "limitation": "Coverage/state gate only; reference reasoning still requires independent teacher review."}


def run(output: Path, base: str, api_run: Path, round_id: int, tag: str):
    from scripts.question_bank.runner import run_case, valid_tag
    cases = read(output / "batch-manifest.json")["cases"]
    valid_tag(tag)
    dump(output / "plans" / f"{tag}.json", {"cases": [c["id"] for c in cases], "modes": ["mixed"], "expected_tasks": len(cases)})
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(run_case, output, base, api_run, case, round_id, tag, ["mixed"]): case for case in cases}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                from scripts.question_bank.budget import BudgetExceeded
                if isinstance(exc, BudgetExceeded):
                    raise
                dump(output / "private" / "case-setup-failures" / tag / f"{futures[future]['id']}.json",
                     {"case_id": futures[future]["id"], "error": type(exc).__name__, "task_requests_not_started": True})
    return results
