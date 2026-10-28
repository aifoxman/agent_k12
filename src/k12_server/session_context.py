"""Bounded learning context for one homework session.

The current photo remains the authority for question text, written work, and grading.
"""

from __future__ import annotations

import json
import re
from typing import Any


def short(value: Any, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def format_session_context(
    hints: list[dict],
    reflections: list[dict],
    observations: list[dict],
    corrections: list[dict],
    *,
    purpose: str = "hint",
    max_chars: int = 1500,
) -> str:
    """Keep recent conversation and student effort without asserting old AI judgments."""
    events: list[tuple[int, str]] = []
    for row in hints:
        detail = f"孩子问：{short(row.get('question'), 100)}"
        if purpose != "correction":
            detail += f"；此前提示：{short(row.get('response'), 190)}"
        events.append((row["created_at"], detail))
    for row in reflections:
        events.append((row["created_at"], f"孩子口述想法：{short(row.get('text'), 180)}"))
    for row in observations:
        detail = short(row.get("progress") or row.get("label"), 170)
        if detail:
            events.append((row["created_at"], f"此前视觉观察（待当前照片核对）：{detail}"))
    if purpose != "correction":
        for row in corrections:
            label = short(
                " ".join(filter(None, (row.get("subject_label"), row.get("page_label")))), 60
            )
            detail = (
                f"此前批改建议{f'（{label}）' if label else ''}：{short(row.get('summary'), 150)}"
            )
            try:
                checks = json.loads(row.get("checks_json") or "[]")
            except (TypeError, ValueError):
                checks = []
            events.append((row["created_at"], detail))
            for check in checks:
                if not isinstance(check, dict) or not isinstance(check.get("parent_review"), dict):
                    continue
                review = check["parent_review"]
                explanation = short(review.get("note"), 100)
                label = short(check.get("question"), 40)
                status = short(review.get("status"), 30)
                event = f"家长校对{f' {label}' if label else ''}：{status}"
                if explanation:
                    event += f"；说明：{explanation}"
                events.append((review.get("updated_at") or row["created_at"], event))
    if not events:
        return ""
    recent = sorted(events, key=lambda item: item[0], reverse=True)[:8]
    chosen: list[str] = []
    remaining = max_chars
    for _, detail in recent:
        cost = len(detail) + 5
        if cost > remaining:
            if not chosen:
                chosen.append(detail[: max(0, max_chars - 5)])
            break
        chosen.append(detail)
        remaining -= cost
    return "\n".join(f"{index}. {detail}" for index, detail in enumerate(reversed(chosen), 1))
