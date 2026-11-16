"""Preserve original PDF layout, then select reference-grounded question crops."""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

from scripts.question_bank.budget import Budget, BudgetExceeded
from scripts.question_bank.corpus import dump, read
from scripts.question_bank.simulation import student_paper
from scripts.question_bank.teacher import BLIND_ORACLE, ORACLE, REFERENCE_CHECK, call_teacher, normalize, validate_oracle


def quote_regions(page, quote):
    """Map whitespace-normalized PDF text back to actual glyph rectangles."""
    import pymupdf
    chars = [char for block in page.get_text("rawdict")["blocks"] if "lines" in block
             for line in block["lines"] for span in line["spans"] for char in span["chars"]
             if not char["c"].isspace()]
    text = "".join(c["c"] for c in chars)
    query = normalize(quote)
    start, result = text.find(query), []
    while start >= 0:
        rect = pymupdf.Rect(chars[start]["bbox"])
        for char in chars[start + 1:start + len(query)]:
            rect |= pymupdf.Rect(char["bbox"])
        result.append(rect)
        start = text.find(query, start + 1)
    return result


def prepare_document(record, output: Path, settings, round_id=1):
    import pymupdf
    identifier = record["sha256"][:20]
    spec_path = output / "private" / "specs" / f"{identifier}.json"
    cached_spec = read(spec_path) if spec_path.exists() else None
    if cached_spec and cached_spec.get("validation_version") == 3:
        return cached_spec
    content = read(output / "private" / "documents" / f"{record['sha256']}.json")
    pdf = output / "private" / "pdf" / f"{record['sha256']}.pdf"
    if not pdf.exists():
        return {"case_id": identifier, "accepted": False, "validation_errors": ["pdf_missing"]}
    with pymupdf.open(pdf) as document:
        pages = list(document)[:min(4, len(document))]
        # English first pages frequently contain only listening; select visible written work.
        if record["subject"] == "english":
            pages.sort(key=lambda page: sum(bool(re.search(pattern, page.get_text(), re.I))
                                           for pattern in [r"笔试|读写", r"Read", r"选择|单项", r"Writing"]), reverse=True)
        page = pages[0]
        text = page.get_text()
        if re.search(r"参考答案|答案解析", text) and len(text) < 300:
            return {"case_id": identifier, "accepted": False, "validation_errors": ["answer_page"]}
        image_path = output / "private" / "pages" / f"{identifier}.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        page.get_pixmap(dpi=160).pil_save(str(image_path), format="JPEG", quality=94)
        spec = cached_spec or call_teacher(settings, Budget(output / "budget.sqlite3"), round_id, f"oracle:{identifier}", ORACLE,
                            {"grade": record["grade"], "subject": record["subject"],
                             "page_text": text[:14000], "source_answers": content["answer_text"][:35000]}, image_path,
                            model=settings.ai_fast_vision_model or settings.ai_model)
        spec = validate_oracle(spec, content, text)
        spec.update(case_id=identifier, grade=record["grade"], subject=record["subject"],
                    source_sha256=record["sha256"], source_path=record["path"], pdf_page=page.number + 1)
        if spec["accepted"]:
            x, y, w, h = spec["box"]
            page_rect = page.rect
            rect = pymupdf.Rect(x * page_rect.width, y * page_rect.height,
                               (x + w) * page_rect.width, (y + h) * page_rect.height)
            quote_rects = quote_regions(page, spec["question_quote"])
            # Recover text positions from the PDF, rather than asking vision to be a typesetter.
            full_rects = quote_regions(page, spec["question_text"])
            if full_rects:
                text_rect = min(full_rects, key=lambda r: abs(r.y0 - rect.y0))
                if (rect & text_rect).get_area() > 0:
                    rect |= text_rect
                    rect = (rect + (-3, -3, 3, 3)) & page_rect
            if not quote_rects or not any(rect.contains(r) for r in quote_rects):
                spec["accepted"] = False
                spec["validation_errors"].append("question_quote_outside_crop")
            else:
                crop_path = output / "private" / "crops" / f"{identifier}.jpg"
                crop_path.parent.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(dpi=200, clip=rect).pil_save(str(crop_path), format="JPEG", quality=96)
                spec["crop"] = str(crop_path.relative_to(output).as_posix())
                blind = call_teacher(settings, Budget(output / "budget.sqlite3"), round_id, f"blind:{identifier}",
                    BLIND_ORACLE, {"grade": record["grade"], "subject": record["subject"]}, crop_path,
                    model=settings.ai_fast_vision_model or settings.ai_model)
                spec["independent_solution"] = blind
                def canonical(value):
                    return re.sub(r"[\s。，、（）()：:；;]", "", str(value)).lower()
                matched = canonical(blind.get("final_answer")) == canonical(spec["final_answer"])
                complete = all(blind.get(k) is True for k in ("complete", "no_printed_answer", "single_question"))
                adjudication = call_teacher(settings, Budget(output / "budget.sqlite3"), round_id, f"reference:{identifier}",
                    REFERENCE_CHECK, {"question_label": spec["label"], "source_reference": spec["reference_quote"],
                        "reference_answer": spec["answer"], "reference_final": spec["final_answer"],
                        "independent_solution": blind, "wrong_answer": spec["wrong_answer"]}, crop_path,
                    model=settings.ai_fast_vision_model or settings.ai_model)
                agreed = all(adjudication.get(k) is True for k in
                             ("reference_corresponds", "solutions_equivalent", "wrong_answer_invalid", "crop_complete", "no_printed_answer"))
                spec["reference_adjudication"] = adjudication
                if not complete or not agreed or float(blind.get("confidence") or 0) < .8:
                    spec["accepted"] = False
                    spec["validation_errors"].append("independent_crop_or_answer_disagreement")
                spec["literal_answers_match"] = matched
                spec["independent_reference_check"] = bool(complete and agreed and float(blind.get("confidence") or 0) >= .8)
                spec["keyword_boxes"] = []
                for keyword in spec.get("keywords", []):
                    for match in page.search_for(keyword):
                        if rect.contains(match):
                            spec["keyword_boxes"].append({"text": keyword,
                                "box": [(match.x0 - rect.x0) / rect.width, (match.y0 - rect.y0) / rect.height,
                                        match.width / rect.width, match.height / rect.height]})
        spec.setdefault("independent_reference_check", False)
        spec["validation_version"] = 3
        dump(spec_path, spec)
        return spec


def prepare_all(output: Path, settings, *, limit=60):
    records = read(output / "private" / "selected-documents.json")[:limit]
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(prepare_document, record, output, settings): record for record in records}
        for future in as_completed(futures):
            record = futures[future]
            try:
                result = future.result()
            except BudgetExceeded:
                raise
            except Exception as exc:
                result = {"case_id": record["sha256"][:20], "accepted": False,
                          "validation_errors": [type(exc).__name__]}
                dump(output / "private" / "preparation-errors" / f"{result['case_id']}.json", result)
            results.append(result)
            print({"prepared": len(results), "accepted": sum(r.get("accepted", False) for r in results),
                   "case_id": result["case_id"], "errors": result.get("validation_errors")}, flush=True)
    dump(output / "private" / "preparation.json", [{k: r.get(k) for k in
         ("case_id", "accepted", "validation_errors", "grade", "subject")} for r in results])
    return results


def group_cases(specs):
    """Connected near-duplicate question groups, never split photo variants."""
    groups = []
    for spec in sorted(specs, key=lambda s: s["case_id"]):
        text = re.sub(r"[\s\d一二三四五六七八九十、.．，。()（）]", "", spec["question_text"])
        matching = [g for g in groups if any(s["source_sha256"] == spec["source_sha256"] or
                    SequenceMatcher(None, text, s["_canonical"]).ratio() >= .9 for s in g)]
        spec = {**spec, "_canonical": text}
        if matching:
            merged = [spec]
            for group in matching:
                merged.extend(group)
                groups.remove(group)
            groups.append(merged)
        else:
            groups.append([spec])
    return groups


def make_manifest(output: Path, font: str):
    if (output / "manifest.json").exists():
        raise ValueError("Manifest is immutable; choose a new run rather than changing its split")
    specs = [read(p) for p in sorted((output / "private" / "specs").glob("*.json"))]
    groups = group_cases([s for s in specs if s.get("accepted")])
    # Round-robin strata before deterministic group selection preserves grade/subject breadth.
    strata = {}
    for group in groups:
        key = (group[0]["subject"], group[0]["grade"])
        strata.setdefault(key, []).append(group)
    ordered = []
    while any(strata.values()):
        for key in sorted(strata):
            if strata[key]:
                ordered.append(strata[key].pop(0))
    total = sum(len(g) for g in ordered)
    desired = min(18, max(1, round(total * .3)))
    holdout, development = [], []
    seen_path = output / "private" / "seen-development.json"
    seen = set(read(seen_path)) if seen_path.exists() else set()
    for group in ordered:
        if not any(s["case_id"] in seen for s in group) and len(holdout) < desired and len(group) <= desired - len(holdout):
            holdout.extend(group)
        else:
            development.extend(group)
    manifests = {}
    for split, selected in (("development", development), ("holdout", holdout)):
        base = output if split == "development" else output / "private" / "holdout"
        cases = []
        for spec in selected:
            identifier = spec["case_id"]
            directory = base / "papers" / identifier
            directory.mkdir(parents=True, exist_ok=True)
            crop = Image.open(output / spec["crop"])
            images = {}
            for mode, answer in (("hint", ""), ("correct", spec["answer"]),
                                 ("wrong", spec["wrong_answer"]), ("blank", "")):
                path = directory / f"{mode}.jpg"
                student_paper(crop, answer, font).save(path, quality=94)
                images[mode] = path.relative_to(output).as_posix()
            for variant in ("rotation", "perspective", "shadow", "blur", "jpeg", "movement"):
                path = directory / f"hint-{variant}.jpg"
                student_paper(crop, "", font, variant=variant, seed=identifier).save(path, quality=90)
                images[variant] = path.relative_to(output).as_posix()
            oracle = {k: v for k, v in spec.items() if not k.startswith("_")}
            dump(base / "private" / "oracles" / f"{identifier}.json", oracle)
            cases.append({"id": identifier, "grade": spec["grade"], "subject": spec["subject"],
                          "type": spec.get("type"), "split": split,
                          "source_sha256": spec["source_sha256"], "images": images})
        manifest = {"version": 1, "run_id": output.name, "simulated_camera": True,
                    "real_handwriting": False, "split": split, "cases": cases,
                    "oracle_boundary": "Reference answers are evaluator-only; never sent to tested backend."}
        dump(base / "manifest.json", manifest)
        manifests[split] = {"count": len(cases)}
    # Remove temporary mixed specs once split is sealed; keep only in their protected split.
    for directory in (output / "private" / "specs", output / "private" / "crops"):
        for path in directory.glob("*"):
            if path.is_file():
                path.unlink()
    dump(output / "coverage.json", {"requested": 60, "accepted": total,
                                    "unavailable": 60 - total, "splits": manifests})
    return manifests
