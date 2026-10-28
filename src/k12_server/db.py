"""SQLite storage for a small pilot. All write operations are transactional."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

RETENTION_SECONDS = 60 * 86400

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS parents (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS families (
  id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES parents(id), child_name TEXT NOT NULL,
  age_band TEXT NOT NULL, grade TEXT, textbook TEXT, created_at INTEGER NOT NULL,
  proactive_help_mode TEXT NOT NULL DEFAULT 'standard',
  audio_transcription_allowed INTEGER NOT NULL DEFAULT 0,
  audio_transcription_provider TEXT,
  premium_voice_enabled INTEGER NOT NULL DEFAULT 0,
  premium_voice_used_chars INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, pairing_code_hash TEXT,
  pairing_expires_at INTEGER, pairing_attempts INTEGER NOT NULL DEFAULT 0,
  family_id TEXT REFERENCES families(id), created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id), title TEXT NOT NULL,
  subject TEXT, due_date TEXT, status TEXT NOT NULL DEFAULT 'todo', version INTEGER NOT NULL DEFAULT 1,
  child_checked_at INTEGER, parent_confirmed_at INTEGER, created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL, due_at INTEGER, first_submitted_at INTEGER,
  rejection_reason TEXT, child_feedback TEXT, latest_submission_id TEXT, batch_request_id TEXT,
  cancel_reason TEXT, batch_index INTEGER
);
CREATE TABLE IF NOT EXISTS task_submissions (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), family_id TEXT NOT NULL REFERENCES families(id),
  session_id TEXT REFERENCES sessions(id), attempt INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending', ai_status TEXT NOT NULL DEFAULT 'pending',
  ai_summary TEXT NOT NULL DEFAULT '', ai_details TEXT NOT NULL DEFAULT '',
  submitted_at INTEGER NOT NULL, reviewed_at INTEGER, review_reason TEXT, child_feedback TEXT,
  request_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
  UNIQUE(task_id,attempt), UNIQUE(task_id,request_id)
);
CREATE TABLE IF NOT EXISTS submission_pages (
  id TEXT PRIMARY KEY, submission_id TEXT NOT NULL REFERENCES task_submissions(id),
  page_index INTEGER NOT NULL, image_jpeg BLOB NOT NULL, image_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
  grade_status TEXT NOT NULL DEFAULT 'not_requested',
  grade_summary TEXT NOT NULL DEFAULT '', grade_checks_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE(submission_id,page_index)
);
CREATE TABLE IF NOT EXISTS task_reminder_state (
  task_id TEXT NOT NULL REFERENCES tasks(id), device_id TEXT NOT NULL,
  event_key TEXT NOT NULL, snoozed_until INTEGER NOT NULL DEFAULT 0,
  spoken_at INTEGER, PRIMARY KEY(task_id,device_id,event_key)
);
CREATE TABLE IF NOT EXISTS task_archive_counts (
  family_id TEXT NOT NULL REFERENCES families(id), day TEXT NOT NULL,
  unconfirmed_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(family_id,day)
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id), device_id TEXT NOT NULL REFERENCES devices(id),
  status TEXT NOT NULL, started_at INTEGER NOT NULL, ended_at INTEGER, expires_at INTEGER NOT NULL,
  correction_requested_at INTEGER, correction_authorized_until INTEGER,
  timer_goal_seconds INTEGER, timer_goal_source TEXT, timer_task_counts TEXT, timer_last_seq INTEGER NOT NULL DEFAULT 0,
  proactive_help_enabled INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON sessions(family_id) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS timer_events (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), seq INTEGER NOT NULL,
  kind TEXT NOT NULL, subject_label TEXT, duration_ms INTEGER NOT NULL,
  ended_at INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
  UNIQUE(session_id,seq)
);
CREATE INDEX IF NOT EXISTS timer_events_session ON timer_events(session_id);
CREATE TABLE IF NOT EXISTS session_pauses (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
  started_at INTEGER NOT NULL, ended_at INTEGER, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS session_pauses_session ON session_pauses(session_id);
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), task_id TEXT REFERENCES tasks(id),
  kind TEXT NOT NULL, label TEXT NOT NULL, evidence TEXT, confidence REAL,
  progress TEXT, writing TEXT, visible_steps TEXT,
  review_status TEXT NOT NULL DEFAULT 'unreviewed', created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS hints (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), task_id TEXT REFERENCES tasks(id),
  question TEXT NOT NULL, response TEXT NOT NULL, source TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, teaching_json TEXT
);
CREATE TABLE IF NOT EXISTS help_candidates (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), label TEXT NOT NULL,
  evidence TEXT NOT NULL, box_json TEXT NOT NULL, confidence REAL NOT NULL,
  effort TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS help_candidates_session ON help_candidates(session_id);
CREATE TABLE IF NOT EXISTS family_ai_preferences (
  family_id TEXT PRIMARY KEY REFERENCES families(id), solver_model TEXT NOT NULL DEFAULT 'gpt-6.1-sol',
  grading_model TEXT NOT NULL DEFAULT 'gpt-6-astra',
  locator_model TEXT NOT NULL DEFAULT 'gpt-6-sol', tutor_model TEXT NOT NULL DEFAULT 'gpt-6-luna',
  precompute_enabled INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS page_preparations (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
  fingerprint TEXT NOT NULL, status TEXT NOT NULL, model_version INTEGER NOT NULL,
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS page_preparations_session ON page_preparations(session_id, created_at);
CREATE TABLE IF NOT EXISTS prepared_questions (
  id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES page_preparations(id),
  ordinal INTEGER NOT NULL, path_json TEXT NOT NULL, label TEXT NOT NULL,
  evidence TEXT NOT NULL, box_json TEXT NOT NULL, progress TEXT NOT NULL,
  read_confidence REAL NOT NULL, solution_json TEXT, solve_confidence REAL,
  verification_status TEXT NOT NULL DEFAULT 'pending',
  UNIQUE(page_id,ordinal)
);
CREATE INDEX IF NOT EXISTS prepared_questions_page ON prepared_questions(page_id,ordinal);
CREATE TABLE IF NOT EXISTS family_page_cache (
  family_id TEXT NOT NULL REFERENCES families(id), page_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL, inventory_json TEXT NOT NULL,
  model_version INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
  PRIMARY KEY(family_id,page_key,model_version)
);
CREATE INDEX IF NOT EXISTS family_page_cache_expiry ON family_page_cache(expires_at);
CREATE TABLE IF NOT EXISTS family_question_cache (
  family_id TEXT NOT NULL REFERENCES families(id), page_key TEXT NOT NULL,
  question_key TEXT NOT NULL, model_version INTEGER NOT NULL, solver_model TEXT NOT NULL,
  solution_json TEXT NOT NULL, solve_confidence REAL NOT NULL,
  verification_status TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
  PRIMARY KEY(family_id,page_key,question_key,model_version,solver_model)
);
CREATE INDEX IF NOT EXISTS family_question_cache_expiry ON family_question_cache(expires_at);
CREATE TABLE IF NOT EXISTS ai_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, family_id TEXT NOT NULL REFERENCES families(id),
  model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL, reported INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_usage_family_time ON ai_usage(family_id, created_at);
CREATE TABLE IF NOT EXISTS ai_usage_totals (
  family_id TEXT PRIMARY KEY REFERENCES families(id), calls INTEGER NOT NULL DEFAULT 0,
  prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0, unreported_calls INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS speech_requests (
  id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id),
  request_key TEXT, characters INTEGER NOT NULL, status TEXT NOT NULL,
  created_at INTEGER NOT NULL, UNIQUE(family_id, request_key)
);
CREATE INDEX IF NOT EXISTS speech_requests_family_status ON speech_requests(family_id,status);
CREATE TABLE IF NOT EXISTS corrections (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), task_id TEXT REFERENCES tasks(id),
  summary TEXT NOT NULL, checks_json TEXT NOT NULL, confidence REAL NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed', image_jpeg BLOB,
  subject_label TEXT, page_label TEXT, batch_id TEXT, page_index INTEGER, batch_total INTEGER,
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reflections (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), task_id TEXT REFERENCES tasks(id),
  text TEXT NOT NULL, source TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS live_sessions (
  id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id), session_id TEXT NOT NULL REFERENCES sessions(id),
  status TEXT NOT NULL, created_at INTEGER NOT NULL, ended_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_session ON live_sessions(family_id) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS rate_limits (
  key TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start INTEGER NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def initialize(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        ai_preference_columns = {row["name"] for row in connection.execute(
            "PRAGMA table_info(family_ai_preferences)")}
        if "grading_model" not in ai_preference_columns:
            connection.execute("ALTER TABLE family_ai_preferences ADD COLUMN grading_model "
                               "TEXT NOT NULL DEFAULT 'gpt-6-astra'")
        family_columns = {row["name"] for row in connection.execute("PRAGMA table_info(families)")}
        if "proactive_help_mode" not in family_columns:
            connection.execute("ALTER TABLE families ADD COLUMN proactive_help_mode TEXT NOT NULL DEFAULT 'standard'")
        if "audio_transcription_allowed" not in family_columns:
            connection.execute("ALTER TABLE families ADD COLUMN audio_transcription_allowed INTEGER NOT NULL DEFAULT 0")
        if "audio_transcription_provider" not in family_columns:
            connection.execute("ALTER TABLE families ADD COLUMN audio_transcription_provider TEXT")
        if "premium_voice_enabled" not in family_columns:
            connection.execute("ALTER TABLE families ADD COLUMN premium_voice_enabled INTEGER NOT NULL DEFAULT 0")
        if "premium_voice_used_chars" not in family_columns:
            connection.execute("ALTER TABLE families ADD COLUMN premium_voice_used_chars INTEGER NOT NULL DEFAULT 0")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(observations)")}
        for field in ("progress", "writing", "visible_steps"):
            if field not in columns:
                connection.execute(f"ALTER TABLE observations ADD COLUMN {field} TEXT")
        hint_columns = {row["name"] for row in connection.execute("PRAGMA table_info(hints)")}
        if "level" not in hint_columns:
            connection.execute("ALTER TABLE hints ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
        if "teaching_json" not in hint_columns:
            connection.execute("ALTER TABLE hints ADD COLUMN teaching_json TEXT")
        session_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}
        for field in ("correction_requested_at", "correction_authorized_until",
                      "timer_goal_seconds", "timer_goal_source", "timer_task_counts"):
            if field not in session_columns:
                kind = "TEXT" if field in ("timer_goal_source", "timer_task_counts") else "INTEGER"
                connection.execute(f"ALTER TABLE sessions ADD COLUMN {field} {kind}")
        if "timer_last_seq" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN timer_last_seq INTEGER NOT NULL DEFAULT 0")
        if "proactive_help_enabled" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN proactive_help_enabled INTEGER NOT NULL DEFAULT 1")
        correction_columns = {row["name"] for row in connection.execute("PRAGMA table_info(corrections)")}
        for field, kind in (("image_jpeg", "BLOB"), ("subject_label", "TEXT"),
                            ("page_label", "TEXT"), ("batch_id", "TEXT"),
                            ("page_index", "INTEGER"), ("batch_total", "INTEGER")):
            if field not in correction_columns:
                connection.execute(f"ALTER TABLE corrections ADD COLUMN {field} {kind}")
        task_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
        for field, kind in (("due_at", "INTEGER"), ("first_submitted_at", "INTEGER"),
                            ("rejection_reason", "TEXT"), ("child_feedback", "TEXT"),
                            ("latest_submission_id", "TEXT"),
                            ("batch_request_id", "TEXT"), ("cancel_reason", "TEXT"),
                            ("batch_index", "INTEGER")):
            if field not in task_columns:
                connection.execute(f"ALTER TABLE tasks ADD COLUMN {field} {kind}")
        connection.execute("CREATE INDEX IF NOT EXISTS tasks_family_batch ON tasks(family_id,batch_request_id)")
        submission_columns = {row["name"] for row in connection.execute("PRAGMA table_info(task_submissions)")}
        if "child_feedback" not in submission_columns:
            connection.execute("ALTER TABLE task_submissions ADD COLUMN child_feedback TEXT")
        page_columns = {row["name"] for row in connection.execute("PRAGMA table_info(submission_pages)")}
        for field, kind in (("grade_status", "TEXT NOT NULL DEFAULT 'not_requested'"),
                            ("grade_summary", "TEXT NOT NULL DEFAULT ''"),
                            ("grade_checks_json", "TEXT NOT NULL DEFAULT '[]'")):
            if field not in page_columns:
                connection.execute(f"ALTER TABLE submission_pages ADD COLUMN {field} {kind}")
        # Extend only records that have not already been purged. The original
        # creation timestamp remains the absolute 60-day privacy boundary.
        for table, created in (("tasks", "created_at"), ("sessions", "started_at"),
                               ("observations", "created_at"), ("hints", "created_at"),
                               ("help_candidates", "created_at"),
                               ("corrections", "created_at"), ("reflections", "created_at"),
                               ("timer_events", "created_at"),
                               ("session_pauses", "started_at")):
            connection.execute(
                f"UPDATE {table} SET expires_at={created}+? WHERE expires_at<{created}+?",
                (RETENTION_SECONDS, RETENTION_SECONDS),
            )


@contextmanager
def transaction(path: str) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def purge_expired(path: str, now: int | None = None) -> None:
    current = now or int(time.time())
    with transaction(path) as connection:
        connection.execute("DELETE FROM family_page_cache WHERE expires_at<=?", (current,))
        connection.execute("DELETE FROM family_question_cache WHERE expires_at<=?", (current,))
        connection.execute("DELETE FROM prepared_questions WHERE page_id IN "
                           "(SELECT id FROM page_preparations WHERE expires_at<=? OR session_id IN "
                           "(SELECT id FROM sessions WHERE status='finished'))", (current,))
        connection.execute("DELETE FROM page_preparations WHERE expires_at<=? OR session_id IN "
                           "(SELECT id FROM sessions WHERE status='finished')", (current,))
        stale_usage = connection.execute(
            "SELECT family_id,COUNT(*) AS calls,COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,"
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens,"
            "COALESCE(SUM(total_tokens),0) AS total_tokens,"
            "COALESCE(SUM(CASE WHEN reported=0 THEN 1 ELSE 0 END),0) AS unreported_calls "
            "FROM ai_usage WHERE created_at<=? GROUP BY family_id",
            (current - RETENTION_SECONDS,),
        ).fetchall()
        for row in stale_usage:
            connection.execute(
                "INSERT INTO ai_usage_totals VALUES(?,?,?,?,?,?) ON CONFLICT(family_id) DO UPDATE SET "
                "calls=calls+excluded.calls,prompt_tokens=prompt_tokens+excluded.prompt_tokens,"
                "completion_tokens=completion_tokens+excluded.completion_tokens,"
                "total_tokens=total_tokens+excluded.total_tokens,"
                "unreported_calls=unreported_calls+excluded.unreported_calls",
                (row["family_id"], row["calls"], row["prompt_tokens"],
                 row["completion_tokens"], row["total_tokens"], row["unreported_calls"]),
            )
        connection.execute("DELETE FROM ai_usage WHERE created_at<=?", (current - RETENTION_SECONDS,))
        expired = connection.execute(
            "SELECT family_id, date(created_at,'unixepoch') AS day,COUNT(*) AS count "
            "FROM tasks WHERE expires_at<=? AND status NOT IN ('confirmed','canceled') "
            "GROUP BY family_id,date(created_at,'unixepoch')", (current,)
        ).fetchall()
        for row in expired:
            connection.execute(
                "INSERT INTO task_archive_counts(family_id,day,unconfirmed_count) VALUES(?,?,?) "
                "ON CONFLICT(family_id,day) DO UPDATE SET unconfirmed_count=unconfirmed_count+excluded.unconfirmed_count",
                (row["family_id"], row["day"], row["count"]),
            )
        connection.execute("DELETE FROM task_reminder_state WHERE task_id IN (SELECT id FROM tasks WHERE expires_at<=?)", (current,))
        connection.execute("DELETE FROM submission_pages WHERE expires_at<=? OR submission_id IN "
                           "(SELECT id FROM task_submissions WHERE task_id IN "
                           "(SELECT id FROM tasks WHERE expires_at<=?))", (current, current))
        connection.execute("DELETE FROM task_submissions WHERE expires_at<=? OR task_id IN "
                           "(SELECT id FROM tasks WHERE expires_at<=?)", (current, current))
        connection.execute("DELETE FROM session_pauses WHERE expires_at <= ? OR session_id IN (SELECT id FROM sessions WHERE expires_at <= ?)", (current, current))
        connection.execute("DELETE FROM timer_events WHERE expires_at <= ? OR session_id IN (SELECT id FROM sessions WHERE expires_at <= ?)", (current, current))
        connection.execute("DELETE FROM corrections WHERE expires_at <= ? OR session_id IN (SELECT id FROM sessions WHERE expires_at <= ?)", (current, current))
        connection.execute("DELETE FROM hints WHERE expires_at <= ? OR session_id IN (SELECT id FROM sessions WHERE expires_at <= ?)", (current, current))
        connection.execute("DELETE FROM help_candidates WHERE expires_at <= ? OR session_id IN (SELECT id FROM sessions WHERE expires_at <= ?)", (current, current))
        connection.execute("DELETE FROM reflections WHERE expires_at <= ? OR session_id IN (SELECT id FROM sessions WHERE expires_at <= ?)", (current, current))
        connection.execute("DELETE FROM observations WHERE expires_at <= ? OR session_id IN (SELECT id FROM sessions WHERE expires_at <= ?)", (current, current))
        connection.execute("DELETE FROM live_sessions WHERE session_id IN (SELECT id FROM sessions WHERE expires_at <= ?)", (current,))
        connection.execute("UPDATE task_submissions SET session_id=NULL WHERE session_id IN "
                           "(SELECT id FROM sessions WHERE expires_at<=?)", (current,))
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (current,))
        connection.execute("UPDATE observations SET task_id=NULL WHERE task_id IN (SELECT id FROM tasks WHERE expires_at <= ?)", (current,))
        connection.execute("UPDATE hints SET task_id=NULL WHERE task_id IN (SELECT id FROM tasks WHERE expires_at <= ?)", (current,))
        connection.execute("UPDATE corrections SET task_id=NULL WHERE task_id IN (SELECT id FROM tasks WHERE expires_at <= ?)", (current,))
        connection.execute("UPDATE reflections SET task_id=NULL WHERE task_id IN (SELECT id FROM tasks WHERE expires_at <= ?)", (current,))
        connection.execute("DELETE FROM tasks WHERE expires_at <= ?", (current,))
        connection.execute(
            "UPDATE devices SET pairing_code_hash = NULL, pairing_expires_at = NULL "
            "WHERE pairing_expires_at <= ?",
            (current,),
        )
        connection.execute("DELETE FROM rate_limits WHERE window_start <= ?", (current - 600,))
