"""Freeze the comparison before opening the independent holdout suite."""
from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.question_bank.corpus import dump, read


def source_hash(directory: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(directory.rglob("*.py")):
        value.update(path.relative_to(directory).as_posix().encode())
        value.update(path.read_bytes())
    return value.hexdigest()


def freeze(output: Path, current: Path):
    path = output / "private" / "holdout" / "comparison-seal.json"
    if path.exists():
        raise ValueError("The final comparison is already sealed")
    value = {"baseline": source_hash(output / "private" / "baseline-src" / "k12_server"),
             "candidate": source_hash(current), "evaluator": source_hash(Path(__file__).parent),
             "tags": {"holdout-baseline": "baseline", "holdout-candidate": "candidate"}}
    dump(path, value)
    return value


def verify(output: Path, tag: str, current: Path, actual_source: str | None = None):
    value = read(output / "private" / "holdout" / "comparison-seal.json")
    if source_hash(current) != value["candidate"]:
        raise ValueError("Backend changed after final holdout comparison was sealed")
    if source_hash(Path(__file__).parent) != value["evaluator"]:
        raise ValueError("Evaluator changed after final holdout comparison was sealed")
    side = value["tags"].get(tag)
    if not side or actual_source is not None and actual_source != value[side]:
        raise ValueError("Holdout tag or actual server source does not match the sealed comparison")
    return value
