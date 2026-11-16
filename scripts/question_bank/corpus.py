"""Read source documents without modifying them or mixing answers into requests."""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

SUBJECTS = {"数学": "math", "语文": "chinese", "英语": "english", "科学": "science"}
GRADES = {f"{number}年级": i for i, number in enumerate("一二三四五六", 1)}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ANSWER = re.compile(r"(?:参考答案|答案[与及和]?解析|试题答案|答案要点|参考解答|^\s*答案\s*[:：]?\s*$)")


def inferred_subject(filename: str, content: dict) -> str:
    explicit = next((v for k, v in SUBJECTS.items() if k in filename), None)
    if explicit:
        return explicit
    if re.search(r"(?<![A-Za-z])PEP(?![A-Za-z])|English", filename, re.I):
        return "english"
    heading = " ".join(p for p in content.get("paragraphs", [])[:8] if p.strip())[:350]
    named = {v for k, v in SUBJECTS.items() if k in heading}
    if len(named) == 1:
        return named.pop()
    text = content.get("stem_text", "")
    if len(re.findall(r"\b[A-Za-z]{2,}\b", text)) >= 30 and re.search(r"listen|read|writing|English", text, re.I):
        return "english"
    return "unknown"


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def docx_content(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > 32_000_000:
            raise ValueError("oversized document XML")
        root = ET.fromstring(archive.read(info))
        paragraphs = ["".join(n.text or "" for n in p.iter(W + "t"))
                      for p in root.iter(W + "p")]
        boundaries = [i for i, text in enumerate(paragraphs) if ANSWER.search(text)]
        # A contents/title mentioning answers is not an answer boundary.
        boundary = next((i for i in boundaries if i >= 10), None)
        stem = paragraphs[:boundary] if boundary is not None else paragraphs
        answers = paragraphs[boundary:] if boundary is not None else []
        canonical = re.sub(r"\s+", "", "\n".join(stem))
        # Remove publisher/header years for source-family deduplication only.
        canonical = re.sub(r"20\d{2}(?:[-—至]20\d{2})?", "YEAR", canonical)
        return {"paragraphs": paragraphs, "stem_text": "\n".join(stem),
                "answer_text": "\n".join(answers), "answer_boundary": boundary,
                "text_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                "media_count": sum(n.startswith("word/media/") for n in archive.namelist())}


def inventory(root: Path, output: Path) -> dict:
    root = root.resolve(strict=True)
    records, errors, hashes = [], [], {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".docx", ".pdf", ".jpg", ".png", ".zip", ".rar"}:
            continue
        relative = path.relative_to(root)
        grade = next((GRADES[p] for p in relative.parts if p in GRADES), None)
        if not grade:
            continue
        record = {"path": relative.as_posix(), "grade": grade, "extension": path.suffix.lower(),
                  "bytes": path.stat().st_size, "sha256": digest(path),
                  "subject": next((v for k, v in SUBJECTS.items() if k in path.name), "unknown")}
        try:
            if path.suffix.lower() == ".docx":
                content = docx_content(path)
                if record["subject"] == "unknown":
                    record["subject"] = inferred_subject(path.name, content)
                record.update({k: v for k, v in content.items() if k not in {"paragraphs", "stem_text", "answer_text"}})
                dump(output / "private" / "documents" / f"{record['sha256']}.json", content)
            family = record.get("text_sha256") or record["sha256"]
            record["duplicate_of"] = hashes.get(family)
            hashes.setdefault(family, record["sha256"])
            record["state"] = "archive_requires_review" if path.suffix.lower() in {".zip", ".rar"} else "indexed"
        except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
            record["state"] = "unreadable"
            errors.append({"path": record["path"], "error": type(exc).__name__})
        records.append(record)
    value = {"version": 1, "source_root": str(root), "records": records,
             "counts": dict(Counter(r["extension"] for r in records)),
             "duplicates": sum(bool(r.get("duplicate_of")) for r in records), "errors": errors}
    dump(output / "private" / "inventory.json", value)
    return value


def quotas():
    quotas = {("math", g): 5 for g in range(1, 7)}
    quotas.update({("chinese", g): 2 for g in range(1, 7)})
    quotas.update({("english", g): 3 for g in range(3, 7)})
    quotas.update({("science", g): (2 if g == 6 else 1) for g in range(2, 7)})
    return quotas


def choose_documents(index: dict) -> list[dict]:
    """60 source groups: math 30, Chinese 12, English 12, science 6."""
    chosen = []
    for (subject, grade), count in quotas().items():
        candidates = [r for r in index["records"] if r["subject"] == subject and r["grade"] == grade
                      and r["state"] == "indexed" and not r.get("duplicate_of")
                      and r["extension"] == ".docx" and r.get("answer_boundary") is not None]
        # Stable, diverse years/publishers rather than directory enumeration order.
        candidates.sort(key=lambda r: hashlib.sha256(("k12-corpus-v1:" + r["sha256"]).encode()).hexdigest())
        chosen.extend(candidates[:count])
    return chosen


def backfill(output: Path):
    if (output / "manifest.json").exists():
        raise ValueError("Cannot change a sealed corpus")
    index = read(output / "private" / "inventory.json")
    selected = read(output / "private" / "selected-documents.json")
    used = {r["sha256"] for r in selected}
    specs = [read(p) for p in (output / "private" / "specs").glob("*.json")]
    accepted = Counter((s.get("subject"), s.get("grade")) for s in specs
                       if s.get("accepted") and s.get("independent_reference_check") is True)
    added = []
    for key, desired in quotas().items():
        missing = max(0, desired - accepted[key])
        candidates = [r for r in index["records"] if (r["subject"], r["grade"]) == key
                      and r["sha256"] not in used and r["state"] == "indexed"
                      and r["extension"] == ".docx" and r.get("answer_boundary") is not None
                      and not r.get("duplicate_of")]
        candidates.sort(key=lambda r: hashlib.sha256(("k12-corpus-v1:" + r["sha256"]).encode()).hexdigest())
        added.extend(candidates[:min(12, missing * 2)])
    dump(output / "private" / "selected-documents.json", selected + added)
    return {"added": len(added), "selected_total": len(selected) + len(added)}


def classify_cached(output: Path):
    index_path = output / "private" / "inventory.json"
    index = read(index_path)
    changed = Counter()
    for record in index["records"]:
        path = output / "private" / "documents" / f"{record['sha256']}.json"
        if record["subject"] == "unknown" and path.exists():
            record["subject"] = inferred_subject(Path(record["path"]).name, read(path))
            if record["subject"] != "unknown":
                changed[record["subject"]] += 1
    dump(index_path, index)
    return dict(changed)
