"""Existing family records survive the child-facing feedback migration."""

import sqlite3

from k12_server.db import initialize


def test_existing_return_reason_survives_feedback_columns(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, family_id TEXT, title TEXT, "
                   "created_at INTEGER, expires_at INTEGER, rejection_reason TEXT)")
        db.execute("INSERT INTO tasks VALUES ('task', 'family', '数学', 1, 100, '请补写步骤')")
        db.execute("CREATE TABLE task_submissions (id TEXT PRIMARY KEY, task_id TEXT, "
                   "review_reason TEXT)")
        db.execute("INSERT INTO task_submissions VALUES ('attempt', 'task', '请补写步骤')")

    initialize(str(path))

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT rejection_reason,child_feedback FROM tasks "
                          "WHERE id='task'").fetchone() == ("请补写步骤", None)
        assert db.execute("SELECT review_reason,child_feedback FROM task_submissions "
                          "WHERE id='attempt'").fetchone() == ("请补写步骤", None)
