"""Homework timer summaries and adjustable goal suggestions."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from statistics import median

from .db import connect

KINDS = ("focus", "assisted", "grading_wait")
TRIAL_MINUTES = {"kindergarten": 10, "primary": 20, "junior": 25}
MAX_MINUTES = {"kindergarten": 30, "primary": 90, "junior": 120}


def task_counts(tasks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for task in tasks:
        if task["status"] != "confirmed":
            counts[(task.get("subject") or "").strip()[:60]] += 1
    return dict(counts) or {"": 1}


def suggestion(path: str, family: dict, tasks: list[dict], now: int,
               offset_minutes: int = 480) -> dict:
    base = TRIAL_MINUTES.get(family["age_band"], 20)
    counts = task_counts(tasks)
    history: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    with connect(path) as db:
        rows = db.execute(
            "SELECT e.session_id,e.subject_label,s.timer_task_counts,s.ended_at,"
            "SUM(e.duration_ms) AS duration_ms FROM timer_events e "
            "JOIN sessions s ON s.id=e.session_id WHERE s.family_id=? "
            "AND s.ended_at IS NOT NULL AND s.ended_at>=? "
            "AND e.kind IN ('focus','assisted') GROUP BY e.session_id,e.subject_label",
            (family["id"], now - 7 * 86400),
        ).fetchall()
    for row in rows:
        subject = row["subject_label"] or ""
        previous_counts = json.loads(row["timer_task_counts"] or "{}")
        count = max(1, int(previous_counts.get(subject, 1)))
        day = (row["ended_at"] + offset_minutes * 60) // 86400
        history[subject][day].append(row["duration_ms"] / 60000 / count)
    per_subject = []
    total_minutes = 0
    for subject, count in counts.items():
        days = history.get(subject, {})
        if len(days) >= 3:
            daily_rates = [median(values) for values in days.values()]
            rate = min(base * 1.5, max(base * 0.5, median(daily_rates)))
            source = "history"
        else:
            rate, source = base, "trial"
        minutes = max(5, round(rate * count / 5) * 5)
        total_minutes += minutes
        per_subject.append({"subject": subject or None, "goal_seconds": minutes * 60,
                            "source": source})
    total_minutes = min(MAX_MINUTES.get(family["age_band"], 90), total_minutes)
    return {"goal_seconds": total_minutes * 60, "by_subject": per_subject,
            "source": "history" if any(item["source"] == "history" for item in per_subject)
            else "trial"}


def snapshot(path: str, session: dict) -> dict:
    with connect(path) as db:
        rows = db.execute(
            "SELECT kind,subject_label,SUM(duration_ms) AS duration_ms "
            "FROM timer_events WHERE session_id=? GROUP BY kind,subject_label",
            (session["id"],),
        ).fetchall()
        latest = db.execute("SELECT subject_label FROM timer_events WHERE session_id=? "
                            "ORDER BY seq DESC LIMIT 1", (session["id"],)).fetchone()
        paused = db.execute("SELECT COALESCE(SUM(COALESCE(ended_at,?)-started_at),0) "
                            "AS seconds FROM session_pauses WHERE session_id=?",
                            (int(time.time()), session["id"])).fetchone()
    kinds = {kind: 0 for kind in KINDS}
    subjects: dict[str, int] = defaultdict(int)
    for row in rows:
        kinds[row["kind"]] += row["duration_ms"]
        if row["kind"] in ("focus", "assisted"):
            subjects[row["subject_label"] or ""] += row["duration_ms"]
    focus_seconds = round(kinds["focus"] / 1000)
    assisted_seconds = round(kinds["assisted"] / 1000)
    return {
        "learning_seconds": focus_seconds + assisted_seconds,
        "focus_seconds": focus_seconds,
        "assisted_seconds": assisted_seconds,
        "grading_wait_seconds": round(kinds["grading_wait"] / 1000),
        "paused_seconds": paused["seconds"],
        "by_subject": [{"subject": label or None, "seconds": round(value / 1000)}
                       for label, value in sorted(subjects.items())],
        "goal_seconds": session.get("timer_goal_seconds"),
        "goal_source": session.get("timer_goal_source"),
        "current_subject": latest["subject_label"] if latest else None,
        "last_seq": session.get("timer_last_seq") or 0,
    }


def daily_summary(path: str, family_id: str, since: int, until: int,
                  now: int | None = None) -> dict:
    effective_end = min(until, now if now is not None else int(time.time()))
    with connect(path) as db:
        rows = db.execute(
            "SELECT e.kind,e.subject_label,e.duration_ms,e.ended_at FROM timer_events e "
            "JOIN sessions s ON s.id=e.session_id WHERE s.family_id=? "
            "AND e.ended_at>=? AND e.ended_at-e.duration_ms/1000.0<?",
            (family_id, since, until),
        ).fetchall()
        legacy = db.execute(
            "SELECT started_at,ended_at FROM sessions s WHERE s.family_id=? "
            "AND s.started_at<? AND COALESCE(s.ended_at,?)>=? "
            "AND s.timer_task_counts IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM timer_events e WHERE e.session_id=s.id)",
            (family_id, until, effective_end, since),
        ).fetchall()
        pauses = db.execute(
            "SELECT p.started_at,p.ended_at FROM session_pauses p JOIN sessions s "
            "ON s.id=p.session_id WHERE s.family_id=? AND p.started_at<? "
            "AND COALESCE(p.ended_at,?)>=?",
            (family_id, until, effective_end, since),
        ).fetchall()
    kinds = {kind: 0.0 for kind in KINDS}
    subjects: dict[str, float] = defaultdict(float)
    for row in rows:
        seconds = row["duration_ms"] / 1000
        overlap = max(0.0, min(row["ended_at"], effective_end) -
                      max(row["ended_at"] - seconds, since))
        milliseconds = overlap * 1000
        kinds[row["kind"]] += milliseconds
        if row["kind"] in ("focus", "assisted"):
            subjects[row["subject_label"] or ""] += milliseconds
    focus_seconds = round(kinds["focus"] / 1000)
    assisted_seconds = round(kinds["assisted"] / 1000)
    return {
        "duration_seconds": focus_seconds + assisted_seconds,
        "focus_seconds": focus_seconds,
        "assisted_seconds": assisted_seconds,
        "grading_wait_seconds": round(kinds["grading_wait"] / 1000),
        "paused_seconds": sum(max(0, min(row["ended_at"] or effective_end, until) -
                                  max(row["started_at"], since)) for row in pauses),
        "by_subject": [{"subject": label or None, "seconds": round(value / 1000)}
                       for label, value in sorted(subjects.items())],
        "legacy_duration_seconds": sum(max(0, min(row["ended_at"] or effective_end, until) -
                                           max(row["started_at"], since)) for row in legacy),
    }
