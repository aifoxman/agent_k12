"""Daily event attribution and grounded end-of-session feedback."""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from k12_server.db import connect, initialize
from k12_server.reports import daily_report_details, session_review
from k12_server.security import make_token

NOW = 200000
SINCE = 144000
UNTIL = SINCE + 86400
EXPIRY = NOW + 86400


@pytest.fixture
def report_db(tmp_path):
    path = str(tmp_path / "reports.sqlite3")
    initialize(path)
    with connect(path) as db:
        for family in ("family", "other"):
            db.execute("INSERT INTO parents VALUES(?,?,?,?)",
                       (family, f"{family}@example.com", "hash", 0))
            db.execute("INSERT INTO families(id,parent_id,child_name,age_band,created_at) "
                       "VALUES(?,?,?,'primary',0)", (family, family, "孩子"))
            db.execute("INSERT INTO devices(id,secret_hash,family_id,created_at) VALUES(?,?,?,0)",
                       (family, "hash", family))
        for session, family, status in (("current", "family", "active"),
                                        ("old", "family", "finished"),
                                        ("outsider", "other", "active")):
            db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,expires_at) "
                       "VALUES(?,?,?,?,?,?)", (session, family, family, status, SINCE - 100, EXPIRY))
    return path


def task(db, task_id, status="todo", family="family", **timestamps):
    db.execute("INSERT INTO tasks(id,family_id,title,status,created_at,expires_at) "
               "VALUES(?,?,?,?,?,?)", (task_id, family, task_id, status, SINCE - 100, EXPIRY))
    for column, value in timestamps.items():
        db.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task_id))


def submission(db, submission_id, task_id, attempt=1, session="current", family="family",
               submitted_at=NOW, status="pending", reviewed_at=None):
    db.execute("INSERT INTO task_submissions(id,task_id,family_id,session_id,attempt,status,"
               "submitted_at,reviewed_at,request_id,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (submission_id, task_id, family, session, attempt, status, submitted_at,
                reviewed_at, submission_id, EXPIRY))


def correction(db, correction_id, checks, session="current", status="unreviewed", created_at=NOW):
    db.execute("INSERT INTO corrections(id,session_id,summary,checks_json,confidence,review_status,"
               "created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
               (correction_id, session, "待核对", json.dumps(checks, ensure_ascii=False),
                .9, status, created_at, EXPIRY))


def test_daily_activity_uses_event_dates_and_deduplicates_tasks(report_db):
    with connect(report_db) as db:
        task(db, "revised", "confirmed", first_submitted_at=NOW, child_checked_at=NOW,
             parent_confirmed_at=NOW)
        submission(db, "first", "revised", status="rejected", reviewed_at=NOW - 5)
        submission(db, "second", "revised", attempt=2, status="accepted", reviewed_at=NOW)
        task(db, "previous", "confirmed", parent_confirmed_at=SINCE - 1)
        submission(db, "yesterday", "previous", submitted_at=SINCE - 1,
                   status="accepted", reviewed_at=SINCE - 1)
        task(db, "tomorrow", "confirmed", parent_confirmed_at=UNTIL)
        submission(db, "future", "tomorrow", submitted_at=UNTIL, status="accepted",
                   reviewed_at=UNTIL)
        task(db, "family-pending", "awaiting_parent")
        task(db, "foreign", "confirmed", family="other", parent_confirmed_at=NOW)
        submission(db, "foreign-submit", "foreign", family="other", session="outsider")
    report = daily_report_details(report_db, "family", SINCE, UNTIL, 480, NOW)
    assert report["task_activity"] == {"submitted": 1, "confirmed": 1, "returned": 1}
    assert report["pending_review_count"] == 1
    assert report["period"] == {"date": "1970-01-03", "since": SINCE, "until": UNTIL,
                                "offset_minutes": 480}


def test_daily_activity_keeps_known_legacy_times_without_guessing_missing_events(report_db):
    with connect(report_db) as db:
        task(db, "known", "confirmed", parent_confirmed_at=NOW, child_checked_at=NOW)
        task(db, "unknown", "confirmed", created_at=NOW)
        task(db, "expired", "confirmed", parent_confirmed_at=NOW, expires_at=NOW)
    report = daily_report_details(report_db, "family", SINCE, UNTIL, 480, NOW)
    assert report["task_activity"] == {"submitted": 1, "confirmed": 1, "returned": 0}


def test_daily_records_filter_before_limiting_and_exclude_other_families(report_db):
    with connect(report_db) as db:
        for index in range(105):
            db.execute("INSERT INTO reflections VALUES(?,?,?,?,?,?,?)",
                       (f"thought-{index}", "current", None, f"想法{index}", "child_voice_or_text",
                        NOW - index, EXPIRY))
        for rid, session, created in (("previous", "current", SINCE - 1),
                                      ("next", "current", UNTIL),
                                      ("foreign", "outsider", NOW)):
            db.execute("INSERT INTO reflections VALUES(?,?,?,?,?,?,?)",
                       (rid, session, None, "不在本日报", "child_voice_or_text", created, EXPIRY))
        correction(db, "page", [{"question": "第一题", "status": "unclear"}])
    report = daily_report_details(report_db, "family", SINCE, UNTIL, 480, NOW)
    thoughts = report["daily_records"]["reflections"]
    assert thoughts["total"] == 105 and len(thoughts["items"]) == 100 and thoughts["truncated"]
    assert thoughts["items"][0]["id"] == "thought-0"
    assert thoughts["items"][-1]["id"] == "thought-99"
    pages = report["daily_records"]["corrections"]
    assert pages["total"] == 1 and not pages["truncated"]
    assert pages["items"][0]["checks"][0]["status"] == "unclear"
    assert "checks_json" not in pages["items"][0]
    assert "image_available" in pages["items"][0]


def test_daily_records_preserve_child_correction_authorization(report_db):
    with connect(report_db) as db:
        correction(db, "active-page", [])
        correction(db, "finished-page", [], session="old")
    report = daily_report_details(report_db, "family", SINCE, UNTIL, 480, NOW, "child")
    assert [item["id"] for item in report["daily_records"]["corrections"]["items"]] == [
        "finished-page"]
    with connect(report_db) as db:
        db.execute("UPDATE sessions SET correction_authorized_until=? WHERE id='current'",
                   (NOW + 60,))
    assert daily_report_details(report_db, "family", SINCE, UNTIL, 480, NOW,
                                "child")["daily_records"]["corrections"]["total"] == 2


def test_daily_records_test_bypass_preserves_session_and_history_boundaries(report_db):
    with connect(report_db) as db:
        correction(db, "active-page", [])
        correction(db, "finished-page", [], session="old")
        correction(db, "other-family-page", [], session="outsider")
        db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,expires_at) "
                   "VALUES('paused','family','family','paused',?,?)", (SINCE, EXPIRY))
        correction(db, "paused-page", [], session="paused")
        correction(db, "expired-page", [])
        db.execute("UPDATE corrections SET expires_at=? WHERE id='expired-page'", (NOW,))
        correction(db, "outside-period-page", [], created_at=SINCE - 1)

    report = daily_report_details(
        report_db, "family", SINCE, UNTIL, 480, NOW, "child",
        correction_require_parent_approval=False,
    )
    pages = report["daily_records"]["corrections"]
    assert {item["id"] for item in pages["items"]} == {"active-page", "finished-page"}
    assert pages["total"] == 2
    with connect(report_db) as db:
        session = db.execute("SELECT correction_authorized_until,correction_requested_at "
                             "FROM sessions WHERE id='current'").fetchone()
        assert session["correction_authorized_until"] is None
        assert session["correction_requested_at"] is None

    enforced = daily_report_details(report_db, "family", SINCE, UNTIL, 480, NOW, "child")
    assert [item["id"] for item in enforced["daily_records"]["corrections"]["items"]] == [
        "finished-page"]
    parent = daily_report_details(
        report_db, "family", SINCE, UNTIL, 480, NOW,
        correction_require_parent_approval=False,
    )
    assert {item["id"] for item in parent["daily_records"]["corrections"]["items"]} == {
        "active-page", "finished-page", "paused-page"}


def test_session_review_counts_all_hints_and_only_linked_tasks_with_child_feedback(report_db):
    with connect(report_db) as db:
        for index in range(40):
            db.execute("INSERT INTO hints(id,session_id,task_id,question,response,source,level,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (f"hint-{index}", "current", None, "怎么做", "先读题", "ai", 1, NOW, EXPIRY))
        db.execute("INSERT INTO hints(id,session_id,task_id,question,response,source,level,created_at,expires_at) VALUES('old-hint','old',NULL,'问题','提示','ai',1,?,?)",
                   (NOW, EXPIRY))
        task(db, "linked", "needs_revision", rejection_reason="家长内部说明",
             child_feedback="请先补写你的第一步，再和我一起核对。")
        submission(db, "linked-one", "linked", status="rejected", reviewed_at=NOW)
        submission(db, "linked-two", "linked", attempt=2)
        task(db, "unlinked", "confirmed")
        submission(db, "unlinked-one", "unlinked", session=None)
        db.execute("INSERT INTO reflections VALUES('thought','current',NULL,?,'child_voice_or_text',?,?)",
                   ("我想先画图", NOW, EXPIRY))
        correction(db, "ai-page", [{"question": "第二题", "status": "check_again",
                                    "next_step": "先核对条件。"}])
    review = session_review(report_db, "family", "current", NOW)
    assert review["hint_count"] == 40
    assert review["submitted_task_count"] == 1
    assert review["task_counts"] == {"awaiting_parent": 0, "needs_revision": 1, "confirmed": 0}
    assert review["record"] == {"text": "你记录的想法：我想先画图",
                                 "source_type": "reflections", "source_id": "thought"}
    assert review["next_step"]["source_type"] == "tasks"
    assert "请先补写你的第一步" in review["next_step"]["text"]
    assert "家长内部说明" not in review["next_step"]["text"]


def test_session_review_keeps_parent_judgment_and_only_confirmed_observation(report_db):
    with connect(report_db) as db:
        for oid, status, created in (("accepted", "accepted", NOW - 1),
                                      ("unreviewed", "unreviewed", NOW)):
            db.execute("INSERT INTO observations(id,session_id,kind,label,evidence,review_status,"
                       "created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                       (oid, "current", "hint_attempt", "有新书写", "目标处有新书写", status,
                        created, EXPIRY))
        correction(db, "page", [
            {"question": "第一题", "status": "check_again", "next_step": "旧的错误建议",
             "parent_review": {"status": "looks_correct", "note": "原判看漏步骤"}},
            {"question": "第二题", "status": "check_again", "next_step": "旧提示",
             "parent_review": {"status": "needs_discussion", "note": "请说明你选择这个条件的理由"}},
        ])
        correction(db, "rejected-page", [{"question": "不可信", "status": "check_again"}],
                   status="rejected", created_at=NOW + 1)
    review = session_review(report_db, "family", "current", NOW)
    assert review["record"]["source_id"] == "accepted"
    assert "新书写" in review["record"]["text"] and "掌握" not in review["record"]["text"]
    assert "第二题" in review["next_step"]["text"]
    assert "家长校对建议" in review["next_step"]["text"]
    assert "旧" not in review["next_step"]["text"]


def test_session_review_does_not_repeat_private_legacy_return_reason(report_db):
    with connect(report_db) as db:
        task(db, "legacy-return", "needs_revision", rejection_reason="家长内部说明")
        submission(db, "legacy-attempt", "legacy-return", status="rejected", reviewed_at=NOW)
    next_step = session_review(report_db, "family", "current", NOW)["next_step"]
    assert next_step["source_type"] == "tasks"
    assert "家长内部说明" not in next_step["text"]
    assert "和家长核对需要补做的内容" in next_step["text"]


def test_session_review_prioritizes_parent_guidance_across_correction_records(report_db):
    with connect(report_db) as db:
        correction(db, "older-parent-page", [
            {"question": "第二题", "status": "check_again", "next_step": "过时的 AI 解释",
             "parent_review": {"status": "needs_discussion", "note": "请先说明图中两条边的关系",
                               "updated_at": NOW}},
        ], created_at=NOW - 100)
        correction(db, "newer-ai-page", [
            {"question": "第三题", "status": "check_again", "next_step": "再看单位"},
        ], created_at=NOW + 1)
        correction(db, "corrected-page", [
            {"question": "第一题", "status": "check_again", "next_step": "已经失效的订正",
             "parent_review": {"status": "looks_correct", "note": "孩子步骤正确",
                               "updated_at": NOW + 2}},
        ], created_at=NOW + 2)
    next_step = session_review(report_db, "family", "current", NOW)["next_step"]
    assert next_step["source_id"] == "older-parent-page"
    assert "家长校对建议（第二题）" in next_step["text"]
    assert "请先说明图中两条边的关系" in next_step["text"]
    assert "失效" not in next_step["text"] and "过时" not in next_step["text"]


def test_session_review_has_neutral_fallback_and_family_boundary(report_db):
    with connect(report_db) as db:
        correction(db, "page", [{"question": "第一题", "status": "check_again",
                                 "parent_review": {"status": "looks_correct"}}])
    review = session_review(report_db, "family", "current", NOW)
    assert review["record"] is None
    assert review["next_step"] == {"text": "可以用自己的话说说这次采用的方法。",
                                    "source_type": "suggestion", "source_id": None}
    assert session_review(report_db, "other", "current", NOW) == review


def test_session_review_labels_ai_suggestion_without_claiming_a_learning_outcome(report_db):
    with connect(report_db) as db:
        correction(db, "ai-page", [{"question": "第三题", "status": "unclear",
                                    "next_step": "先核对题目里给出的单位。"}])
        db.execute("INSERT INTO observations(id,session_id,kind,label,evidence,review_status,"
                   "created_at,expires_at) VALUES('visual','current','hint_attempt','有新书写',"
                   "'出现新字迹','unreviewed',?,?)", (NOW, EXPIRY))
    review = session_review(report_db, "family", "current", NOW)
    assert review["record"] is None
    assert review["next_step"] == {
        "text": "AI 建议，待核对（第三题）：先核对题目里给出的单位。",
        "source_type": "corrections", "source_id": "ai-page",
    }


def test_report_api_adds_fields_without_model_calls_and_retains_timezone(monkeypatch, report_db):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("DATABASE_PATH", report_db)
    module = importlib.reload(importlib.import_module("k12_server.app"))
    monkeypatch.setattr(module, "current", lambda: NOW)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("Reports must not call the model")

    monkeypatch.setattr(module, "analyze", unexpected_call)
    monkeypatch.setattr(module, "draft_child_feedback", unexpected_call)
    client = TestClient(module.app)
    parent = {"Authorization": "Bearer " + make_token(module.settings.app_secret, "parent", "family")}
    child = {"Authorization": "Bearer " + make_token(module.settings.app_secret, "child", "family")}
    report = client.get("/v1/reports/daily?offset_minutes=480", headers=parent)
    assert report.status_code == 200
    result = report.json()
    assert result["period"]["since"] == SINCE
    assert {"tasks", "hint_count", "correction_count", "unreviewed_count", "duration_seconds"} <= result.keys()
    assert result["task_activity"] == {"submitted": 0, "confirmed": 0, "returned": 0}
    shifted = client.get("/v1/reports/daily?offset_minutes=-720", headers=parent).json()
    assert shifted["period"] == {"date": "1970-01-02", "since": 129600, "until": 216000,
                                 "offset_minutes": -720}
    end = client.post("/v1/sessions/current/finish", headers=child)
    assert end.status_code == 200, end.text
    assert {"id", "status", "duration_seconds", "timer", "review"} <= end.json().keys()
    assert end.json()["review"]["record"] is None
    assert client.get("/v1/reports/daily?offset_minutes=900", headers=parent).status_code == 422


def test_finish_retry_keeps_original_end_summary_and_device_boundary(monkeypatch, report_db):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("DATABASE_PATH", report_db)
    module = importlib.reload(importlib.import_module("k12_server.app"))
    monkeypatch.setattr(module, "current", lambda: NOW)
    broadcasts = []
    monkeypatch.setattr(module, "broadcast_sync", lambda *args: broadcasts.append(args))
    with connect(report_db) as db:
        db.execute("UPDATE sessions SET status='paused' WHERE id='current'")
        db.execute("INSERT INTO session_pauses VALUES('pause','current',?,NULL,?)",
                   (NOW - 30, EXPIRY))
        db.execute("INSERT INTO live_sessions VALUES('live','family','current','active',?,NULL)",
                   (NOW - 60,))
        db.execute("INSERT INTO timer_events VALUES('focus','current',1,'focus','数学',5000,?,?,?)",
                   (NOW - 30, NOW - 30, EXPIRY))
        db.execute("INSERT INTO hints(id,session_id,task_id,question,response,source,level,created_at,expires_at) VALUES('hint','current',NULL,'问题','提示','ai',1,?,?)",
                   (NOW - 30, EXPIRY))
        db.execute("INSERT INTO devices(id,secret_hash,family_id,created_at) "
                   "VALUES('second-device','hash','family',0)")
    client = TestClient(module.app)

    def headers(role, subject):
        return {"Authorization": "Bearer " + make_token(module.settings.app_secret, role, subject)}

    owner = headers("child", "family")
    first = client.post("/v1/sessions/current/finish", headers=owner)
    assert first.status_code == 200, first.text
    assert first.json()["ended_at"] == NOW
    assert first.json()["duration_seconds"] == 5
    assert first.json()["timer"]["paused_seconds"] == 30
    assert first.json()["review"]["hint_count"] == 1
    monkeypatch.setattr(module, "current", lambda: NOW + 60)
    retry = client.post("/v1/sessions/current/finish", headers=owner)
    assert retry.status_code == 200 and retry.json() == first.json()
    assert broadcasts == [("family", {"type": "live_ended"})]
    assert client.post("/v1/sessions/current/finish", headers=headers(
        "child", "second-device")).status_code == 409
    assert client.post("/v1/sessions/current/finish", headers=headers(
        "child", "other")).status_code == 404
    assert client.post("/v1/sessions/current/finish", headers=headers(
        "parent", "family")).status_code == 403
    with connect(report_db) as db:
        assert db.execute("SELECT ended_at FROM sessions WHERE id='current'").fetchone()[0] == NOW
        assert db.execute("SELECT ended_at FROM session_pauses WHERE id='pause'").fetchone()[0] == NOW
        assert db.execute("SELECT ended_at FROM live_sessions WHERE id='live'").fetchone()[0] == NOW
