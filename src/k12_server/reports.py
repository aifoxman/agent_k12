"""Evidence-based homework reports assembled from existing family records."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .db import connect

RECORD_LIMIT = 100


def daily_report_details(path: str, family_id: str, since: int, until: int,
                         offset_minutes: int, now: int, role: str = "parent", *,
                         correction_require_parent_approval: bool = True) -> dict:
    """Keep daily events separate from current family-wide pending work."""
    with connect(path) as db:
        submitted = db.execute(
            "SELECT COUNT(*) FROM tasks t WHERE t.family_id=? AND t.expires_at>? AND ("
            "(t.first_submitted_at>=? AND t.first_submitted_at<?) OR "
            "(t.child_checked_at>=? AND t.child_checked_at<?) OR EXISTS ("
            "SELECT 1 FROM task_submissions x WHERE x.task_id=t.id AND x.family_id=? "
            "AND x.expires_at>? AND x.submitted_at>=? AND x.submitted_at<?))",
            (family_id, now, since, until, since, until, family_id, now, since, until),
        ).fetchone()[0]
        confirmed = db.execute(
            "SELECT COUNT(*) FROM tasks t WHERE t.family_id=? AND t.expires_at>? AND ("
            "(t.parent_confirmed_at>=? AND t.parent_confirmed_at<?) OR EXISTS ("
            "SELECT 1 FROM task_submissions x WHERE x.task_id=t.id AND x.family_id=? "
            "AND x.expires_at>? AND x.status='accepted' AND x.reviewed_at>=? "
            "AND x.reviewed_at<?))",
            (family_id, now, since, until, family_id, now, since, until),
        ).fetchone()[0]
        returned = db.execute(
            "SELECT COUNT(DISTINCT x.task_id) FROM task_submissions x "
            "JOIN tasks t ON t.id=x.task_id WHERE x.family_id=? AND t.family_id=? "
            "AND x.expires_at>? AND t.expires_at>? AND x.status='rejected' "
            "AND x.reviewed_at>=? AND x.reviewed_at<?",
            (family_id, family_id, now, now, since, until),
        ).fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE family_id=? AND status='awaiting_parent' "
            "AND expires_at>?", (family_id, now),
        ).fetchone()[0]

        def records(table: str, columns: str, extra: str = "", args: tuple = ()) -> dict:
            scope = (" FROM " + table + " r JOIN sessions s ON s.id=r.session_id "
                     "LEFT JOIN tasks t ON t.id=r.task_id WHERE s.family_id=? "
                     "AND s.expires_at>? AND r.expires_at>? "
                     "AND r.created_at>=? AND r.created_at<?" + extra)
            params = (family_id, now, now, since, until, *args)
            total = db.execute("SELECT COUNT(*)" + scope, params).fetchone()[0]
            items = [dict(row) for row in db.execute(
                "SELECT " + columns + scope + " ORDER BY r.created_at DESC,r.rowid DESC LIMIT ?",
                (*params, RECORD_LIMIT),
            )]
            return {"items": items, "total": total, "truncated": total > len(items)}

        correction_access = ""
        correction_args = ()
        if role == "child":
            if correction_require_parent_approval:
                correction_access = (" AND (s.status='finished' OR (s.status='active' "
                                     "AND s.correction_authorized_until>?))")
                correction_args = (now,)
            else:
                correction_access = " AND s.status IN ('active','finished')"
        corrections = records(
            "corrections",
            "r.id,r.session_id,r.task_id,t.title AS task_title,t.subject,r.subject_label,"
            "r.page_label,r.batch_id,r.page_index,r.batch_total,r.summary,r.checks_json,"
            "r.confidence,r.review_status,r.created_at,r.image_jpeg IS NOT NULL AS image_available",
            correction_access, correction_args,
        )
        for item in corrections["items"]:
            item["checks"] = _checks(item.pop("checks_json"))
        observations = records(
            "observations",
            "r.id,r.session_id,r.task_id,r.kind,r.label,r.evidence,r.confidence,"
            "r.progress,r.writing,r.visible_steps,r.review_status,r.created_at",
        )
        reflections = records(
            "reflections", "r.id,r.session_id,r.task_id,r.text,r.source,r.created_at",
        )
    return {
        "period": {"date": datetime.fromtimestamp(since + offset_minutes * 60, UTC)
                   .date().isoformat(), "since": since, "until": until,
                   "offset_minutes": offset_minutes},
        "task_activity": {"submitted": submitted, "confirmed": confirmed, "returned": returned},
        "pending_review_count": pending,
        "daily_records": {"corrections": corrections, "observations": observations,
                          "reflections": reflections},
    }


def _checks(value: str) -> list[dict]:
    try:
        parsed = json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _source(text: str, source_type: str, source_id: str | None) -> dict:
    return {"text": text, "source_type": source_type, "source_id": source_id}


def session_review(path: str, family_id: str, session_id: str, now: int) -> dict:
    """Describe this session's effort without inferring understanding or mastery."""
    fallback = _source("可以用自己的话说说这次采用的方法。", "suggestion", None)
    with connect(path) as db:
        session = db.execute(
            "SELECT id FROM sessions WHERE id=? AND family_id=? AND expires_at>?",
            (session_id, family_id, now),
        ).fetchone()
        if not session:
            return {"hint_count": 0, "submitted_task_count": 0,
                    "task_counts": {"awaiting_parent": 0, "needs_revision": 0, "confirmed": 0},
                    "record": None, "next_step": fallback}
        hint_count = db.execute(
            "SELECT COUNT(*) FROM hints WHERE session_id=? AND expires_at>?",
            (session_id, now),
        ).fetchone()[0]
        tasks = [dict(row) for row in db.execute(
            "SELECT t.id,t.title,t.status,t.child_feedback FROM tasks t "
            "WHERE t.family_id=? AND t.expires_at>? AND EXISTS (SELECT 1 "
            "FROM task_submissions x WHERE x.task_id=t.id AND x.family_id=? "
            "AND x.session_id=? AND x.expires_at>?) ORDER BY t.created_at DESC,t.rowid DESC",
            (family_id, now, family_id, session_id, now),
        )]
        counts = {status: sum(task["status"] == status for task in tasks)
                  for status in ("awaiting_parent", "needs_revision", "confirmed")}
        reflection = db.execute(
            "SELECT id,text FROM reflections WHERE session_id=? AND expires_at>? "
            "AND trim(text)!='' ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (session_id, now),
        ).fetchone()
        record = None
        if reflection:
            record = _source(f"你记录的想法：{reflection['text'].strip()}",
                             "reflections", reflection["id"])
        else:
            observation = db.execute(
                "SELECT id,evidence,progress,label FROM observations WHERE session_id=? "
                "AND expires_at>? AND review_status='accepted' "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1", (session_id, now),
            ).fetchone()
            if observation:
                detail = (observation["evidence"] or observation["progress"]
                          or observation["label"] or "").strip()
                if detail:
                    record = _source(f"已核对的纸面记录：{detail}",
                                     "observations", observation["id"])

        next_step = None
        for task in tasks:
            if task["status"] != "needs_revision":
                continue
            feedback = (task["child_feedback"] or "").strip()
            if not feedback:
                feedback = "请和家长核对需要补做的内容，再试一步。"
            next_step = _source(f"家长的补做建议：{feedback}", "tasks", task["id"])
            break
        if next_step is None:
            corrections = db.execute(
                "SELECT id,checks_json,created_at FROM corrections WHERE session_id=? AND expires_at>? "
                "AND review_status!='rejected' ORDER BY created_at DESC,rowid DESC",
                (session_id, now),
            ).fetchall()
            # A later AI page must not push a parent's unresolved guidance aside.
            for parent_pass in (True, False):
                candidates = []
                for correction in corrections:
                    for check in _checks(correction["checks_json"]):
                        parent = check.get("parent_review")
                        parent = parent if isinstance(parent, dict) and parent else None
                        if bool(parent) != parent_pass:
                            continue
                        status = parent.get("status") if parent else check.get("status")
                        if status not in {"check_again", "needs_discussion", "unclear"}:
                            continue
                        question = str(check.get("question") or "这道题").strip()
                        if parent:
                            detail = str(parent.get("note") or "请和家长一起核对这道题。").strip()
                            text = f"家长校对建议（{question}）：{detail}"
                        else:
                            detail = str(check.get("next_step") or "请和家长一起核对原题。").strip()
                            text = f"AI 建议，待核对（{question}）：{detail}"
                        updated = parent.get("updated_at") if parent else None
                        timestamp = updated if isinstance(updated, (int, float)) else correction["created_at"]
                        candidates.append((timestamp, _source(text, "corrections", correction["id"])))
                if candidates:
                    next_step = max(candidates, key=lambda candidate: candidate[0])[1]
                    break
    return {"hint_count": hint_count, "submitted_task_count": len(tasks), "task_counts": counts,
            "record": record, "next_step": next_step or fallback}
