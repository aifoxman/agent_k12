"""The pilot's family boundary and core homework flow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import io
import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw


def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    module = importlib.import_module("k12_server.app")
    module = importlib.reload(module)
    monkeypatch.setattr(module, "read_teaching_anchors", lambda *_args: [])
    return TestClient(module.app), module


def parent(client, email="parent@example.com"):
    response = client.post("/v1/auth/register", json={
        "email": email, "password": "long-test-password", "child_name": "小雨",
        "age_band": "primary", "grade": "三年级",
    })
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def pair(client, headers):
    claim = client.post("/v1/devices/claim", json={}).json()
    assert client.post("/v1/families/pair", json={"code": claim["pairing_code"]}, headers=headers).status_code == 200
    status = client.post("/v1/devices/status", json={"device_id": claim["device_id"],
                         "device_secret": claim["device_secret"]}).json()
    return {"Authorization": "Bearer " + status["token"]}


def test_paired_pad_transfers_only_after_new_parent_enters_device_code(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    old_parent = parent(client, "old@example.com")
    new_parent = parent(client, "new@example.com")
    claim = client.post("/v1/devices/claim", json={}).json()
    assert client.post("/v1/families/pair", json={"code": claim["pairing_code"]},
                       headers=old_parent).status_code == 200
    credentials = {"device_id": claim["device_id"], "device_secret": claim["device_secret"]}
    old_status = client.post("/v1/devices/status", json=credentials).json()
    child = {"Authorization": "Bearer " + old_status["token"]}
    with module.connect(module.settings.database_path) as db:
        db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,expires_at) "
                   "VALUES(?,?,?,?,?,?)", ("old-work", old_status["family_id"],
                                            claim["device_id"], "active", int(time.time()),
                                            int(time.time()) + 3600))
        db.commit()

    assert client.post("/v1/devices/transfer-claim", json=credentials).status_code == 401
    assert client.post("/v1/devices/transfer-claim", json={**credentials,
        "device_secret": "wrong"}, headers=child).status_code == 403
    transfer = client.post("/v1/devices/transfer-claim", json=credentials,
                           headers=child)
    assert transfer.status_code == 200
    assert len(transfer.json()["pairing_code"]) == 8
    assert client.post("/v1/devices/status", json=credentials).json()["family_id"] == old_status["family_id"]
    assert client.get("/v1/families/dashboard", headers=old_parent).json()["session"]["id"] == "old-work"

    paired = client.post("/v1/families/pair", json={"code": transfer.json()["pairing_code"]},
                         headers=new_parent)
    assert paired.status_code == 200
    assert client.post("/v1/families/pair", json={"code": transfer.json()["pairing_code"]},
                       headers=old_parent).status_code == 404
    new_status = client.post("/v1/devices/status", json=credentials).json()
    assert new_status["family_id"] != old_status["family_id"]
    assert client.get("/v1/families/dashboard", headers=child).json()["family"]["id"] == new_status["family_id"]
    assert client.get("/v1/families/dashboard", headers=old_parent).json()["session"] is None
    with module.connect(module.settings.database_path) as db:
        old_session = db.execute("SELECT family_id,status FROM sessions WHERE id='old-work'").fetchone()
    assert old_session["family_id"] == old_status["family_id"]
    assert old_session["status"] == "finished"


def test_existing_model_preferences_gain_independent_grading_default(tmp_path):
    from k12_server.db import connect, initialize
    from k12_server.page_prep import preferences

    path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE family_ai_preferences (family_id TEXT PRIMARY KEY, "
                   "solver_model TEXT, locator_model TEXT, tutor_model TEXT, "
                   "precompute_enabled INTEGER, version INTEGER, updated_at INTEGER)")
        db.execute("INSERT INTO family_ai_preferences VALUES(?,?,?,?,?,?,?)",
                   ("family", "gpt-6-sol", "gpt-6-sol", "gpt-6-luna", 1, 2, 1))
    initialize(path)
    with connect(path) as db:
        assert "grading_model" in {column["name"] for column in db.execute(
            "PRAGMA table_info(family_ai_preferences)")}
    selected = preferences(path, "family")
    assert selected["solver_model"] == "gpt-6-sol"
    assert selected["grading_model"] == "gpt-6-astra"


def test_learning_context_is_scoped_to_session_and_selected_task(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    headers = parent(client)
    pair(client, headers)
    now = int(time.time())
    with module.connect(module.settings.database_path) as db:
        family_id = db.execute("SELECT id FROM families LIMIT 1").fetchone()["id"]
        device_id = db.execute("SELECT id FROM devices WHERE family_id=?", (family_id,)).fetchone()["id"]
        for session_id, status in (("current", "active"), ("older", "paused")):
            db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,expires_at) "
                       "VALUES(?,?,?,?,?,?)", (session_id, family_id, device_id, status,
                                                now - 100, now + 3600))
        for task_id in ("math", "language"):
            db.execute("INSERT INTO tasks(id,family_id,title,created_at,expires_at) "
                       "VALUES(?,?,?,?,?)", (task_id, family_id, task_id, now, now + 3600))
        for hint_id, session_id, task_id, question in (
            ("one", "current", "math", "当前数学题"),
            ("two", "current", "language", "同会话语文题"),
            ("three", "older", "math", "旧会话数学题"),
        ):
            db.execute("INSERT INTO hints(id,session_id,task_id,question,response,source,level,"
                       "created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (hint_id, session_id, task_id, question, "先自己想", "ai", 1,
                        now, now + 3600))
    context = module.learning_context("current", "math")
    assert "当前数学题" in context
    assert "同会话语文题" not in context
    assert "旧会话数学题" not in context


def test_hint_attempt_checks_latest_hint_and_records_only_observation(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    now = int(time.time())
    with module.connect(module.settings.database_path) as db:
        family_id = db.execute("SELECT id FROM families LIMIT 1").fetchone()["id"]
        device_id = db.execute("SELECT id FROM devices WHERE family_id=?", (family_id,)).fetchone()["id"]
        db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,expires_at) "
                   "VALUES(?,?,?,?,?,?)", ("practice", family_id, device_id, "active",
                                          now - 60, now + 3600))
        db.execute("INSERT INTO hints(id,session_id,question,response,source,level,created_at,expires_at) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   ("latest", "practice", "第三题", "先找题目条件", "ai", 1, now, now + 3600))
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    jpeg = output.getvalue()
    seen = []
    mode = {"status": "new_work"}

    def assess(_settings, before, after, question, hint, box):
        seen.append((before, after, question, hint, box))
        return {"status": mode["status"], "confidence": .9, "evidence": "目标处新写了一个步骤",
                "response": "我看到新写了一步。你能说说为什么这样做吗？"}

    monkeypatch.setattr(module, "assess_hint_attempt", assess)

    def submit(hint_id="latest", headers=c, box="[0.1,0.2,0.3,0.4]", background=False):
        return client.post(f"/v1/hints/{hint_id}/attempt",
                           data={"session_id": "practice", "target_box": box,
                                 "background": str(background).lower()},
                           files={"before_image": ("before.jpg", jpeg, "image/jpeg"),
                                  "image": ("now.jpg", jpeg, "image/jpeg")}, headers=headers)

    assert submit(headers=p).status_code == 403
    assert submit(hint_id="older").status_code == 409
    assert submit(box="[2,0,1,1]").status_code == 422
    result = submit()
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "new_work"
    assert seen == [(jpeg, jpeg, "第三题", "先找题目条件", [.1, .2, .3, .4])]
    with module.connect(module.settings.database_path) as db:
        observation = db.execute("SELECT kind,progress FROM observations WHERE session_id='practice'").fetchone()
        columns = db.execute("PRAGMA table_info(observations)").fetchall()
    assert observation["kind"] == "hint_attempt"
    assert "新写" in module.learning_context("practice", None)
    assert "image_jpeg" not in {item["name"] for item in columns}
    mode["status"] = "no_visible_change"
    assert submit(background=True).json()["id"] is None
    with module.connect(module.settings.database_path) as db:
        count = db.execute("SELECT COUNT(*) AS count FROM observations WHERE session_id='practice'").fetchone()
    assert count["count"] == 1


def test_task_submission_review_and_revisions(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    other = parent(client, "review-outsider@example.com")
    due = int(time.time()) + 3600
    published = client.post("/v1/tasks/batch", json={
        "request_id": "publish-once-123", "due_at": due,
        "items": [{"title": "语文试卷", "subject": "语文"},
                  {"title": "数学练习", "subject": "数学", "due_at": due + 1800}],
    }, headers=p)
    assert published.status_code == 200, published.text
    tasks = published.json()["tasks"]
    assert client.post("/v1/tasks/batch", json={"request_id": "publish-once-123",
                                                   "due_at": due, "items": [{"title": "语文试卷"}]},
                       headers=p).json()["tasks"] == tasks
    task_id = tasks[0]["id"]
    image = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(image, format="JPEG")
    jpeg = image.getvalue()

    def submit(version, request_id):
        return client.post(f"/v1/tasks/{task_id}/submissions",
                           data={"version": str(version), "request_id": request_id},
                           files=[("images", ("one.jpg", io.BytesIO(jpeg), "image/jpeg")),
                                  ("images", ("two.jpg", io.BytesIO(jpeg), "image/jpeg"))],
                           headers=c)

    first = submit(1, "submit-first-123")
    assert first.status_code == 200, first.text
    assert first.json()["page_count"] == 2
    assert submit(1, "submit-first-123").json()["id"] == first.json()["id"]
    assert submit(1, "submit-stale-123").status_code == 409
    dashboard = client.get("/v1/families/dashboard", headers=p).json()
    task = next(item for item in dashboard["tasks"] if item["id"] == task_id)
    assert task["status"] == "awaiting_parent" and task["first_submitted_at"]
    attempts = client.get(f"/v1/tasks/{task_id}/submissions", headers=p).json()["submissions"]
    assert len(attempts[0]["pages"]) == 2
    image_url = f"/v1/submission-pages/{attempts[0]['pages'][0]['id']}/image"
    assert client.get(image_url, headers=p).content == jpeg
    assert client.get(image_url, headers=other).status_code == 404
    review = {"submission_id": first.json()["id"], "version": 2,
              "decision": "rejected", "reason": ""}
    assert client.post(f"/v1/tasks/{task_id}/reviews", json=review, headers=p).status_code == 422
    review["reason"] = "请补写第三题的步骤"
    draft_request = {"submission_id": first.json()["id"], "reason": review["reason"]}
    assert client.post(f"/v1/tasks/{task_id}/feedback-draft",
                       json=draft_request, headers=c).status_code == 403
    monkeypatch.setattr(module, "draft_child_feedback",
                        lambda _settings, title, reason: f"请先补写{title}中第三题的步骤。")
    draft = client.post(f"/v1/tasks/{task_id}/feedback-draft",
                        json=draft_request, headers=p)
    assert draft.status_code == 200
    review["child_feedback"] = draft.json()["draft"]
    assert client.post(f"/v1/tasks/{task_id}/reviews", json=review, headers=p).json()["status"] == "needs_revision"
    assert client.get("/v1/families/dashboard", headers=c).json()["tasks"][0]["rejection_reason"] == review["child_feedback"]
    assert client.get("/v1/families/dashboard", headers=p).json()["tasks"][0]["rejection_reason"] == review["reason"]
    assert client.get(f"/v1/tasks/{task_id}/submissions", headers=c).json()["submissions"][0]["review_reason"] == review["child_feedback"]
    second = submit(3, "submit-second-123")
    assert second.status_code == 200, second.text
    assert second.json()["attempt"] == 2
    assert client.post(f"/v1/tasks/{task_id}/feedback-draft",
                       json=draft_request, headers=p).status_code == 409
    assert client.get("/v1/families/dashboard", headers=c).json()["tasks"][0]["rejection_reason"] is None
    final = client.post(f"/v1/tasks/{task_id}/reviews", json={
        "submission_id": second.json()["id"], "version": 4,
        "decision": "accepted", "reason": ""}, headers=p)
    assert final.json()["status"] == "confirmed"
    assert client.post(f"/v1/tasks/{task_id}/reviews", json=review, headers=p).status_code == 409
    with module.connect(module.settings.database_path) as db:
        row = db.execute("SELECT expires_at-created_at AS duration FROM tasks WHERE id=?",
                         (task_id,)).fetchone()
    assert row["duration"] == 60 * 86400


def test_task_batch_review_is_atomic_and_unresolved_expiry_is_not_completion(
        monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    due = int(time.time()) + 3600
    assert client.post("/v1/tasks/batch", json={
        "request_id": "too-far-away-123", "due_at": due + 61 * 86400,
        "items": [{"title": "远期作业"}]}, headers=p).status_code == 422
    tasks = client.post("/v1/tasks/batch", json={
        "request_id": "two-items-123", "due_at": due,
        "items": [{"title": "语文"}, {"title": "数学"}]}, headers=p).json()["tasks"]
    submitted = []
    for index, task in enumerate(tasks):
        result = client.post(f"/v1/tasks/{task['id']}/submissions",
                             data={"version": "1", "request_id": f"manual-submit-{index}",
                                   "photo_issue": "相机无法启动，请家长现场核对"}, headers=c)
        assert result.status_code == 200, result.text
        submitted.append(result.json())
    items = [{"task_id": task["id"], "submission_id": attempt["id"], "version": 2}
             for task, attempt in zip(tasks, submitted, strict=True)]
    stale = [items[0], {**items[1], "version": 1}]
    assert client.post("/v1/tasks/reviews/batch", json={"items": stale}, headers=p).status_code == 409
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks WHERE status='confirmed'").fetchone()[0] == 0
    assert len(client.post("/v1/tasks/reviews/batch", json={"items": items}, headers=p).json()["confirmed"]) == 2
    another = client.post("/v1/tasks/batch", json={
        "request_id": "expiring-open-123", "due_at": due,
        "items": [{"title": "未完成的作业"}]}, headers=p).json()["tasks"][0]
    with module.connect(module.settings.database_path) as db:
        created = db.execute("SELECT created_at FROM tasks WHERE id=?", (another["id"],)).fetchone()[0]
    module.purge_expired(module.settings.database_path, created + 60 * 86400 + 1)
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert db.execute("SELECT SUM(unconfirmed_count) FROM task_archive_counts").fetchone()[0] == 1


def test_parent_requests_full_grade_from_submitted_photo(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    other = parent(client, "grading-outsider@example.com")
    task = client.post("/v1/tasks/batch", json={
        "request_id": "grade-publish-123", "due_at": int(time.time()) + 3600,
        "items": [{"title": "通用试卷", "subject": "数学"}]}, headers=p).json()["tasks"][0]
    photo = io.BytesIO()
    Image.new("RGB", (640, 400), "white").save(photo, format="JPEG")
    submission = client.post(f"/v1/tasks/{task['id']}/submissions",
                             data={"version": "1", "request_id": "grade-submit-123"},
                             files={"images": ("page.jpg", io.BytesIO(photo.getvalue()), "image/jpeg")},
                             headers=c).json()
    path = f"/v1/tasks/{task['id']}/submissions/{submission['id']}/grade"
    before = client.get(f"/v1/tasks/{task['id']}/submissions", headers=p).json()["submissions"][0]["pages"][0]
    assert before["grade_status"] == "not_requested"
    assert client.post(path, headers=other).status_code == 404
    module.settings = replace(module.settings, ai_api_url="http://model.example/v1/chat/completions",
                              ai_api_key="test-key", ai_model="test-model")

    def fake_analyze(*_args, **_kwargs):
        assert _args[0].ai_model == "gpt-6-astra"
        return {"confidence": .9, "visible_answer_count": 1, "checks": [{
            "question": "第 1 题", "status": "check_again", "answer": "3",
            "reason": "请复算", "evidence": "纸上写 3", "explanation": "按题意重新计算",
            "reference_answer": "4", "solution_idea": "先核对两个加数",
            "solution_steps": ["2+2=4"], "comparison": "孩子写 3，少算 1。",
            "box": [.2, .2, .3, .2], "confidence": .9, "steps": []}]}

    monkeypatch.setattr(module, "analyze", fake_analyze)
    assert client.post(path, headers=p).json()["queued_pages"] == 1
    after = client.get(f"/v1/tasks/{task['id']}/submissions", headers=p).json()["submissions"][0]["pages"][0]
    assert after["grade_status"] == "ready"
    assert after["grade_checks"][0]["question"] == "第 1 题"
    assert after["grade_checks"][0]["reference_answer"] == "4"
    assert after["grade_checks"][0]["solution_steps"] == ["2+2=4"]
    child_page = client.get(f"/v1/tasks/{task['id']}/submissions", headers=c).json()["submissions"][0]["pages"][0]
    assert child_page["grade_checks"] == [] and child_page["grade_summary"] == ""
    review_path = f"/v1/submission-pages/{after['id']}/checks/0/parent-review"
    assert client.patch(review_path, json={"status": "looks_correct", "answer": "3",
                                           "note": "核对原题后确认", "box": [.2, .2, .3, .2],
                                           "revision": 0}, headers=other).status_code == 404
    reviewed = client.patch(review_path, json={"status": "looks_correct", "answer": "3",
                                               "note": "核对原题后确认", "box": [.2, .2, .3, .2],
                                               "revision": 0}, headers=p)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["check"]["status"] == "looks_correct"
    assert reviewed.json()["check"]["original_review"]["status"] == "check_again"
    assert reviewed.json()["check"]["reference_answer"] == ""
    assert reviewed.json()["check"]["original_review"]["reference_answer"] == "4"
    assert client.patch(review_path, json={"status": "unclear", "answer": "3",
                                           "note": "再次核对", "revision": 0},
                        headers=p).status_code == 409
    assert client.post(path, headers=p).json()["queued_pages"] == 0


def test_audio_transcription_requires_parent_opt_in_and_forwards_current_clip(
        monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_API_URL", "http://localhost:8899/v1/audio/transcriptions")
    monkeypatch.setenv("ASR_MODEL", "test-transcriber")
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    clip = b"webm-test-audio"

    def send(headers):
        return client.post("/v1/voice/transcribe",
                           files={"audio": ("voice.webm", io.BytesIO(clip), "audio/webm")},
                           headers=headers)

    assert send(c).status_code == 403
    assert client.patch("/v1/families/audio-transcription", json={"allowed": True},
                        headers=c).status_code == 403
    assert client.patch("/v1/families/audio-transcription", json={"allowed": True},
                        headers=p).status_code == 200
    assert client.get("/v1/families/dashboard", headers=c).json()["family"][
        "audio_transcription_allowed"] == 1

    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))

            class Result:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"text": "我想一下"}

            return Result()

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    response = send(c)
    assert response.status_code == 200 and response.json() == {"text": "我想一下"}
    assert calls[0][1]["files"]["file"][1] == clip
    assert calls[0][1]["data"]["model"] == "test-transcriber"
    module.settings = replace(module.settings, asr_api_url="http://localhost:9999/v1/audio/transcriptions")
    assert send(c).status_code == 403
    assert client.get("/v1/families/dashboard", headers=p).json()["family"][
        "audio_transcription_allowed"] == 0
    assert client.patch("/v1/families/audio-transcription", json={"allowed": True},
                        headers=p).status_code == 200
    assert send(c).status_code == 200
    assert calls[-1][0].startswith("http://localhost:9999")
    assert client.patch("/v1/families/audio-transcription", json={"allowed": False},
                        headers=p).status_code == 200
    assert send(c).status_code == 403


def test_local_audio_transcription_stays_on_family_server_and_requires_parent_opt_in(
        monkeypatch, tmp_path):
    monkeypatch.delenv("ASR_API_URL", raising=False)
    monkeypatch.delenv("ASR_MODEL", raising=False)
    monkeypatch.setenv("ASR_LOCAL_MODEL_PATH", str(tmp_path / "local-model"))
    client, module = make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(module.local_asr, "ready", lambda path: True)
    heard = []
    monkeypatch.setattr(module.local_asr, "transcribe",
                        lambda path, audio: heard.append((path, audio)) or "第三大題的第三小題")
    p = parent(client)
    c = pair(client, p)

    def send():
        return client.post("/v1/voice/transcribe",
                           files={"audio": ("voice.webm", io.BytesIO(b"full-clip"), "audio/webm")},
                           headers=c)

    assert send().status_code == 403
    dashboard = client.get("/v1/families/dashboard", headers=p).json()
    assert dashboard["asr_available"] is True
    assert dashboard["asr_provider"] == "家庭服务端本地模型"
    assert client.patch("/v1/families/audio-transcription", json={"allowed": True},
                        headers=p).status_code == 200
    assert send().json() == {"text": "第三大题的第三小题"}
    assert heard == [(str(tmp_path / "local-model"), b"full-clip")]
    assert client.patch("/v1/families/audio-transcription", json={"allowed": False},
                        headers=p).status_code == 200
    assert send().status_code == 403


def test_automatic_help_respects_parent_and_child_controls_without_blocking_manual_help(
        monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    page = io.BytesIO()
    Image.new("RGB", (1400, 900), "white").save(page, format="JPEG")
    image = page.getvalue()
    calls = []
    monkeypatch.setattr(module, "detect_help_candidate", lambda *args: calls.append(args) or {
        "found": True, "label": "可见的题号", "evidence": "题干可见", "box": [.2, .3, .3, .2],
        "confidence": .92, "effort": "short",
    })

    def candidate():
        return client.post(f"/v1/sessions/{sid}/help-candidates",
                           data={"activity_box": "[0.2,0.3,0.3,0.2]"},
                           files={"image": ("current.jpg", io.BytesIO(image), "image/jpeg"),
                                  "reference_image": ("earlier.jpg", io.BytesIO(image),
                                                      "image/jpeg")}, headers=c)

    monkeypatch.setattr(module, "assess_idle_checkin", lambda *args: {
        "offer": True, "progress": False, "page_changed": False})

    def checkin(headers=c):
        return client.post(f"/v1/sessions/{sid}/idle-checkin",
                           files={"image": ("current.jpg", io.BytesIO(image), "image/jpeg"),
                                  "reference_image": ("earlier.jpg", io.BytesIO(image),
                                                      "image/jpeg")}, headers=headers)

    assert candidate().status_code == 200
    assert checkin().json()["offer"] is True
    assert checkin(p).status_code == 403
    assert len(calls) == 1
    assert client.patch("/v1/families/help-preferences", json={"mode": "off"},
                        headers=c).status_code == 403
    assert client.patch("/v1/families/help-preferences", json={"mode": "off"},
                        headers=p).status_code == 200
    assert candidate().status_code == 409
    assert checkin().status_code == 409
    assert len(calls) == 1
    assert client.patch(f"/v1/sessions/{sid}/help-preferences", json={"enabled": True},
                        headers=c).status_code == 409
    assert client.patch("/v1/families/help-preferences", json={"mode": "gentle"},
                        headers=p).status_code == 200
    assert client.patch(f"/v1/sessions/{sid}/help-preferences", json={"enabled": False},
                        headers=c).status_code == 200
    assert candidate().status_code == 409
    assert checkin().status_code == 409
    assert len(calls) == 1
    monkeypatch.setattr(module, "locate_question", lambda *_: {
        "found": True, "box": [.2, .3, .3, .2], "label": "可见的题号", "evidence": "题干可见"})
    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs: {
        "target_mismatch": False, "response": "先读题干。", "needs_parent": False})
    manual = client.post("/v1/hints", data={"session_id": sid, "question": "请给我一步提示",
                                             "target": "可见的题号"},
                         files={"image": ("page.jpg", io.BytesIO(image), "image/jpeg")},
                         headers=c)
    assert manual.status_code == 200 and manual.json()["located"] is True


def test_automatic_help_rechecks_candidate_before_teaching(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    page = io.BytesIO()
    Image.new("RGB", (1400, 900), "white").save(page, format="JPEG")
    image = page.getvalue()
    with module.transaction(module.settings.database_path) as db:
        db.execute("INSERT INTO help_candidates(id,session_id,label,evidence,box_json,confidence,effort,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("candidate", sid, "第7题", "可见题干", "[0.2, 0.3, 0.3, 0.2]", .95,
                    "short", module.current(), module.current() + 120))
    teaching = []
    location = {"found": True, "box": [.7, .3, .2, .2],
                "label": "第7题", "evidence": "可见题干"}
    monkeypatch.setattr(module, "locate_question", lambda *_: dict(location))
    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs:
                        teaching.append(args) or {"target_mismatch": False,
                                                  "response": "读一读题干", "needs_parent": False})

    def accept():
        return client.post("/v1/hints", data={"session_id": sid, "question": "要一步提示",
                                               "candidate_id": "candidate"},
                           files={"image": ("page.jpg", io.BytesIO(image), "image/jpeg")},
                           headers=c)

    assert accept().json()["located"] is False
    assert not teaching
    location["box"] = [.2, .3, .3, .2]
    accepted = accept()
    assert accepted.status_code == 200 and accepted.json()["located"] is True
    assert module.one("SELECT source FROM hints WHERE id=?", (accepted.json()["id"],))["source"] == "proactive_accepted"
    assert accept().status_code == 410


def test_parent_model_token_usage_tracks_provider_counts_and_local_periods(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    family_id = client.get("/v1/families/dashboard", headers=p).json()["family"]["id"]
    ai = importlib.import_module("k12_server.ai")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8,
                              "total_tokens": 20}}

    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: FakeResponse())
    ai.set_usage_family(family_id)
    ai.post_model(module.settings, {"model": "gpt-6-sol"})
    today, _ = module.day_bounds(480)
    with module.transaction(module.settings.database_path) as db:
        db.execute("INSERT INTO ai_usage(family_id,model,prompt_tokens,completion_tokens,total_tokens,reported,created_at) VALUES(?,?,?,?,?,?,?)",
                   (family_id, "gpt-6-sol", 50, 10, 60, 1, today - 3600))
        db.execute("INSERT INTO ai_usage(family_id,model,prompt_tokens,completion_tokens,total_tokens,reported,created_at) VALUES(?,?,?,?,?,?,?)",
                   (family_id, "gpt-6-sol", 0, 0, 0, 0, today + 1))
    usage = client.get("/v1/families/dashboard?offset_minutes=480", headers=p).json()["ai_usage"]
    assert usage["today"]["total_tokens"] == 20
    assert usage["today"]["unreported_calls"] == 1
    assert usage["all_time"]["total_tokens"] == 80
    assert usage["all_time"]["calls"] == 3
    local_day = datetime.fromtimestamp(today + 480 * 60, UTC).day
    assert usage["month"]["total_tokens"] == (20 if local_day == 1 else 80)
    assert client.post("/v1/data/delete", headers=p).status_code == 200
    assert client.get("/v1/families/dashboard", headers=p).json()["ai_usage"]["all_time"]["calls"] == 0
    ai.set_usage_family("")


def test_spoken_target_hint_uses_located_crop_and_rejects_content_mismatch(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    # This route fixture supplies OCR coordinates on a blank image; pixel grounding
    # is tested separately with actual ink in the teaching alignment tests.
    monkeypatch.setattr(module, "verify_teaching_anchors", lambda _image, anchors, *_args: anchors)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    image = io.BytesIO()
    Image.new("RGB", (1400, 900), "white").save(image, format="JPEG")
    original = image.getvalue()
    location = {"found": False}
    analysis_calls = []
    monkeypatch.setattr(module, "locate_question", lambda *_: location)

    def fake_analysis(*args, **kwargs):
        analysis_calls.append((args, kwargs))
        return {"target_mismatch": False, "response": "先圈出题目中的条件",
                "needs_parent": False, "visual_cue": {
                    "kind": "circle", "box": [.4, .4, .1, .05], "label": "题目条件", "anchor_id": "a1"}}

    monkeypatch.setattr(module, "analyze", fake_analysis)

    def ask(thought="", earlier=""):
        return client.post("/v1/hints", data={"session_id": sid, "student_thought": thought,
                           "previous_hint": earlier,
                           "question": "第三大题的第三小题怎么做", "target": "第3大题第3小题"},
                           files={"image": ("page.jpg", io.BytesIO(original), "image/jpeg")}, headers=c)

    missing = ask().json()
    assert missing["located"] is False and not analysis_calls
    assert module.one("SELECT COUNT(*) AS count FROM hints")["count"] == 0
    location.update({"found": True, "box": [.2, .3, .3, .2],
                     "label": "三、（3）", "evidence": "可见题干中的条件",
                     "anchors": [{"id": "a1", "text": "条件", "box": [.3, .35, .05, .02],
                                  "confidence": .96}]})
    found = ask().json()
    assert found["located"] is True and found["target_box"] == [.2, .3, .3, .2]
    assert found["response"] == "先圈出题目中的条件"
    assert found["visual_cue"]["kind"] == "circle"
    assert .2 <= found["visual_cue"]["box"][0] <= .5
    assert analysis_calls[0][1]["context_image"] == original
    assert analysis_calls[0][0][1] != original
    assert analysis_calls[0][0][6] == "hint_located"
    assert module.one("SELECT COUNT(*) AS count FROM hints")["count"] == 1
    followup = ask("我想先数一数条件", "先圈出题目中的条件").json()
    assert followup["located"] is True
    assert "孩子刚刚口述的想法：我想先数一数条件" in analysis_calls[1][0][2]
    assert "上一轮老师给过的提示：先圈出题目中的条件" in analysis_calls[1][0][2]
    assert module.one("SELECT COUNT(*) AS count FROM reflections")["count"] == 1
    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs: {
        "target_mismatch": True, "response": "不确定，但我猜这样做", "needs_parent": False})
    monkeypatch.setattr(module, "teach_from_evidence", lambda *args: "先读题目要求，再找出已知条件。")
    uncertain = ask().json()
    assert uncertain["located"] is True and "我猜" not in uncertain["response"]
    assert "先读题目要求" in uncertain["response"]
    assert module.one("SELECT COUNT(*) AS count FROM hints")["count"] == 3
    location["evidence"] = "题目问“林”的结构"
    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs: {
        "target_mismatch": False, "response": "先写“木”，说说理由。", "needs_parent": False})
    monkeypatch.setattr(module, "teach_from_evidence", lambda *args: "先读题，看看“林”由哪两部分组成？")
    confused = ask().json()
    assert confused["located"] is True and "“木”" not in confused["response"]
    assert module.one("SELECT COUNT(*) AS count FROM hints")["count"] == 4

    def unavailable(*args):
        raise module.HTTPException(502, "模型暂不可用")

    monkeypatch.setattr(module, "teach_from_evidence", unavailable)
    still_confused = ask().json()
    assert still_confused["located"] is False and still_confused["target_box"] == location["box"]
    assert module.one("SELECT COUNT(*) AS count FROM hints")["count"] == 4


def test_hint_rechecks_full_page_progress_after_mismatched_nested_number(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    picture = io.BytesIO()
    Image.new("RGB", (1400, 900), "white").save(picture, format="JPEG")
    initial = {"found": True, "box": [.1, .2, .3, .2],
               "label": "二、1、（2）", "evidence": "已做过的第一小题"}
    current = {"found": True, "box": [.5, .6, .3, .2],
               "label": "二、3、（1）", "evidence": "未做的第三小题第（1）题完整题干"}
    locate_calls = []
    fallback_evidence = []

    def locate(*args):
        locate_calls.append(args)
        return current if len(args) > 4 and args[4] is True else initial

    monkeypatch.setattr(module, "locate_question", locate)
    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs: {
        "target_mismatch": True, "response": "第一小题可以这样算", "needs_parent": False})

    def grounded(*args):
        fallback_evidence.append(args[1])
        return "先读第三小题第（1）题，找出它要你求什么？"

    monkeypatch.setattr(module, "teach_from_evidence", grounded)
    response = client.post("/v1/hints", data={
        "session_id": sid,
        "question": "第二大题的第一小题的第二小题怎么做",
        "target": "第2大题第1小题第2小题",
        "spoken_reference": "true",
    }, files={"image": ("page.jpg", io.BytesIO(picture.getvalue()), "image/jpeg")},
        headers=c).json()
    assert response["located"] is True
    assert response["target_label"] == "二、3、（1）"
    assert response["target_box"] == current["box"]
    assert "第三小题第（1）题" in response["response"]
    assert "定位到的题干" not in response["response"]
    assert locate_calls[1][4] is True
    assert fallback_evidence == [current["evidence"]]
    analysis_prompts = []

    def consistent_analysis(*args, **kwargs):
        analysis_prompts.append(args[2])
        return {"target_mismatch": False, "response": "先看题目要你写什么？",
                "needs_parent": False}

    monkeypatch.setattr(module, "analyze", consistent_analysis)
    locate_calls.clear()
    spoken = client.post("/v1/hints", data={
        "session_id": sid,
        "question": "第二大题的第一小题的第二小题怎么做",
        "target": "第2大题第1小题第2小题",
        "spoken_reference": "true",
    }, files={"image": ("page.jpg", io.BytesIO(picture.getvalue()), "image/jpeg")},
        headers=c).json()
    assert spoken["located"] is True and spoken["target_label"] == "二、3、（1）"
    assert spoken["response"].startswith("我先按二、3、（1）来理解")
    assert len(locate_calls) == 1 and locate_calls[0][4] is True
    assert "语音识别出的题号只作求助线索" in analysis_prompts[-1]

    locate_calls.clear()
    reviewed = client.post("/v1/hints", data={
        "session_id": sid,
        "question": "复习第二大题的第一小题的第二小题",
        "target": "第2大题第1小题第2小题",
        "spoken_reference": "true",
    }, files={"image": ("page.jpg", io.BytesIO(picture.getvalue()), "image/jpeg")},
        headers=c).json()
    assert reviewed["located"] is True and reviewed["target_label"] == initial["label"]
    assert len(locate_calls) == 1 and len(locate_calls[0]) == 3

    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs: {
        "target_mismatch": True, "response": "错题提示", "needs_parent": False})
    locate_calls.clear()
    marked = client.post("/v1/hints", data={
        "session_id": sid, "question": "请看我划线的题",
        "selected_strokes": json.dumps([[[.1, .4], [.5, .4]]]),
    }, files={"image": ("page.jpg", io.BytesIO(picture.getvalue()), "image/jpeg")},
        headers=c).json()
    assert marked["located"] is True and marked["target_label"] == initial["label"]
    assert len(locate_calls) == 2 and all(call[4] is False for call in locate_calls)
    assert all(call[5] == [[[.1, .4], [.5, .4]]] for call in locate_calls)

    current["found"] = False
    unresolved = client.post("/v1/hints", data={
        "session_id": sid, "question": "这题怎么做", "target": "第2大题第1小题第2小题",
        "spoken_reference": "true",
    }, files={"image": ("page.jpg", io.BytesIO(picture.getvalue()), "image/jpeg")},
        headers=c).json()
    assert unresolved["located"] is False
    assert "题干" not in unresolved["response"]
    assert "读出这题开头几个字" in unresolved["response"]
    assert module.one("SELECT COUNT(*) AS count FROM hints")["count"] == 4


def test_vague_hint_uses_recent_dialogue_and_keeps_single_question_grounded(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    image = io.BytesIO()
    Image.new("RGB", (1400, 900), "white").save(image, format="JPEG")
    page = image.getvalue()
    locations = []

    def locate(*args):
        locations.append(args)
        return {"found": True, "box": [.2, .3, .3, .2],
                "label": "二、（4）", "evidence": "纸面可见题干"}

    monkeypatch.setattr(module, "locate_question", locate)
    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs: {
        "target_mismatch": False, "response": "先读出题目要求。", "needs_parent": False,
    })

    def ask(question, target=None, selected_region=False):
        data = {"session_id": sid, "question": question,
                "selected_region": str(selected_region).lower()}
        if target:
            data["target"] = target
        return client.post("/v1/hints", data=data,
                           files={"image": ("page.jpg", io.BytesIO(page), "image/jpeg")},
                           headers=c).json()

    assert ask("第2大题第4小题", "第2大题第4小题")["located"] is True
    vague = ask("这题我还是看不懂")
    assert vague["located"] is True and vague["response"].startswith("我先按二、（4）来理解")
    assert locations[1][2] == "这题我还是看不懂" and locations[1][4] is True
    assert "先读出题目要求" in locations[1][3]
    assert module.one("SELECT response FROM hints WHERE question=?", ("这题我还是看不懂",))["response"].startswith("我先按")
    assert ask("请帮我看这道题", selected_region=True)["target_box"] is None
    assert len(locations) == 2


def test_family_flow_and_boundaries(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    other = parent(client, "other@example.com")
    tasks = client.post("/v1/tasks/import", json={"text": "语文：朗读第三课\n数学：练习册第 5 页"}, headers=p)
    assert tasks.status_code == 200
    task = tasks.json()["tasks"][0]
    assert len(tasks.json()["tasks"]) == 2
    child_dashboard = client.get("/v1/families/dashboard", headers=c).json()
    assert abs(child_dashboard["server_now"] - time.time()) < 5
    assert child_dashboard["tasks"][0]["id"] in [t["id"] for t in tasks.json()["tasks"]]
    assert client.patch(f"/v1/tasks/{task['id']}", json={"status": "confirmed", "version": 1}, headers=p).status_code == 409
    assert client.patch(f"/v1/tasks/{task['id']}", json={"status": "awaiting_parent", "version": 1}, headers=c).status_code == 422
    submission = client.post(f"/v1/tasks/{task['id']}/submissions",
                             data={"version": "1", "request_id": "oral-reading-123",
                                   "photo_issue": "朗读任务无纸面结果，请家长听读后确认"},
                             headers=c)
    assert submission.status_code == 200
    assert client.patch(f"/v1/tasks/{task['id']}", json={"status": "confirmed", "version": 2}, headers=other).status_code == 404
    assert client.post(f"/v1/tasks/{task['id']}/reviews", json={
        "submission_id": submission.json()["id"], "version": 2,
        "decision": "accepted"}, headers=p).status_code == 200
    assert client.post("/v1/live-sessions", headers=p).status_code == 409
    assert client.post("/v1/sessions", json={"setup_ticket": "invalid"}, headers=c).status_code == 403
    child_id = module.read_token(module.settings.app_secret, c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    session = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c)
    assert session.status_code == 200
    sid = session.json()["id"]
    assert client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).status_code == 409
    assert client.post("/v1/live-sessions", headers=other).status_code == 409
    assert client.post("/v1/live-sessions", headers=p).status_code == 200
    assert client.patch(f"/v1/sessions/{sid}", json={"status": "paused"}, headers=c).status_code == 200
    assert client.post("/v1/live-sessions", headers=p).status_code == 409
    assert client.patch(f"/v1/sessions/{sid}", json={"status": "active"}, headers=c).status_code == 403
    assert client.patch(f"/v1/sessions/{sid}", json={"status": "active", "setup_ticket": proof}, headers=c).status_code == 200
    assert client.post(f"/v1/sessions/{sid}/finish", headers=c).status_code == 200
    assert client.get("/v1/reports/daily", headers=p).json()["tasks"]["confirmed"] == 1
    assert client.post("/v1/data/delete", headers=p).json()["deleted"]
    assert not client.get("/v1/families/dashboard", headers=p).json()["tasks"]
    assert module.one("SELECT COUNT(*) AS count FROM sessions")["count"] == 0


def test_timer_goal_events_subjects_and_separate_wait(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    client.post("/v1/tasks/import", json={"text": "读一页地理资料", "subject": "地理"}, headers=p)
    suggestion = client.get("/v1/families/dashboard", headers=c).json()["timer_suggestion"]
    assert suggestion["goal_seconds"] == 1200
    assert suggestion["by_subject"][0]["subject"] == "地理"
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof,
                                           "timer_goal_seconds": 1800}, headers=c).json()["id"]
    now = module.current()
    monkeypatch.setattr(module, "current", lambda: now + 10)
    url = f"/v1/sessions/{sid}/timer/events"
    events = [
        {"id": f"{sid}:{seq}", "seq": seq, "kind": kind, "subject": subject,
         "duration_ms": duration, "ended_at": now + seq * 2}
        for seq, kind, subject, duration in (
            (1, "focus", "地理", 5000),
            (2, "grading_wait", "地理", 3000),
            (3, "assisted", "语文", 2000),
        )
    ]
    assert client.post(url, json={"events": events}, headers=p).status_code == 403
    saved = client.post(url, json={"events": events}, headers=c)
    assert saved.status_code == 200, saved.text
    assert saved.json()["learning_seconds"] == 7
    assert saved.json()["grading_wait_seconds"] == 3
    assert {item["subject"]: item["seconds"] for item in saved.json()["by_subject"]} == {
        "地理": 5, "语文": 2,
    }
    assert client.post(url, json={"events": events}, headers=c).json()["last_seq"] == 3
    changed = {**events[0], "duration_ms": 7000}
    assert client.post(url, json={"events": [changed]}, headers=c).status_code == 409
    assert client.post(url, json={"events": [{**events[2], "seq": 5}]}, headers=c).status_code == 409
    assert client.patch(f"/v1/sessions/{sid}/timer", json={"goal_seconds": 2400},
                        headers=p).status_code == 200
    dashboard = client.get("/v1/families/dashboard", headers=c).json()
    assert dashboard["timer"]["goal_seconds"] == 2400
    assert client.patch(f"/v1/sessions/{sid}", json={"status": "paused"}, headers=c).status_code == 200
    monkeypatch.setattr(module, "current", lambda: now + 18)
    assert client.post(f"/v1/sessions/{sid}/finish", headers=c).json()["duration_seconds"] == 7
    offset = 720 - ((now % 86400) // 60)
    report = client.get(f"/v1/reports/daily?offset_minutes={offset}", headers=p).json()
    assert report["duration_seconds"] == 7
    assert report["grading_wait_seconds"] == 3
    assert report["paused_seconds"] == 8
    assert report["legacy_duration_seconds"] == 0
    assert client.post("/v1/data/delete", headers=p).status_code == 200
    assert module.one("SELECT COUNT(*) AS count FROM timer_events")["count"] == 0


def test_page_scan_requires_active_paired_child_and_keeps_each_crop_separate(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    image = b"\xff\xd8paper\xff\xd9"
    seen = []

    def fake_scan(_settings, data):
        seen.append(data)
        return {"pages": [{"box": [.05, .05, .9, .9], "subject": "语文",
                           "printed_page": "第 3 页"}], "reason": "清晰"}

    monkeypatch.setattr(module, "scan_pages", fake_scan)
    url = f"/v1/sessions/{sid}/pages/scan"
    files = {"image": ("paper.jpg", io.BytesIO(image), "image/jpeg")}
    assert client.post(url, files=files, headers=p).status_code == 403
    files = {"image": ("paper.jpg", io.BytesIO(image), "image/jpeg")}
    assert client.post(url, files=files, headers=c).json()["pages"][0]["subject"] == "语文"
    assert seen == [image]
    client.patch(f"/v1/sessions/{sid}", json={"status": "paused"}, headers=c)
    files = {"image": ("paper.jpg", io.BytesIO(image), "image/jpeg")}
    assert client.post(url, files=files, headers=c).status_code == 409


def test_single_question_recheck_updates_current_status_and_keeps_original(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    other = parent(client, "other-recheck@example.com")
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    client.patch(f"/v1/sessions/{sid}/correction-access", json={"allowed": True}, headers=p)
    image = io.BytesIO()
    Image.new("RGB", (1200, 800), "white").save(image, format="JPEG")
    def fake_analyze(*args, **kwargs):
        assert args[0].ai_model == "gpt-6-astra"
        return {
        "response": "已检查", "confidence": .9,
        "checks": [{"question": "第 1 题", "status": "looks_correct", "answer": "7",
                    "evidence": "题干与答案可见", "reason": "初次判断相符",
                    "reference_answer": "7", "solution_idea": "独立列式",
                    "solution_steps": ["3+4=7"], "comparison": "孩子答案一致。",
                    "confidence": .9, "box": [.3, .3, .1, .1]}],
        }

    monkeypatch.setattr(module, "analyze", fake_analyze)
    correction = client.post(f"/v1/sessions/{sid}/corrections",
                             files={"image": ("page.jpg", io.BytesIO(image.getvalue()), "image/jpeg")},
                             headers=c).json()
    url = f"/v1/corrections/{correction['id']}/checks/0/recheck"
    focused = []

    def fake_recheck(_settings, whole, crop, question, *args):
        assert _settings.ai_model == "gpt-6-astra"
        focused.append((whole, crop, question))
        return {"status": "check_again", "answer": "7", "evidence": "复核时读到不同条件",
                "reason": "与题目要求不符", "explanation": "请按印刷要求重算", "confidence": .9,
                "reference_answer": "8", "solution_idea": "先核对条件",
                "solution_steps": ["3+5=8"], "comparison": "孩子写 7，少算 1。", "diagram": None}

    monkeypatch.setattr(module, "recheck_single", fake_recheck)
    assert client.post(url, headers=other).status_code == 404
    assert client.post(url.replace("/0/", "/4/"), headers=p).status_code == 404
    client.patch(f"/v1/sessions/{sid}/correction-access", json={"allowed": False}, headers=p)
    assert client.post(url, headers=c).status_code == 403
    first = client.post(url, headers=p)
    assert first.status_code == 200
    assert first.json()["recheck"]["disagrees"] is True
    assert focused[0][0] == image.getvalue()
    assert focused[0][1] != image.getvalue()
    saved = client.get("/v1/families/dashboard", headers=p).json()["corrections"][0]["checks"][0]
    assert saved["status"] == "check_again"
    assert saved["original_review"]["status"] == "looks_correct"
    assert saved["original_review"]["reason"] == "初次判断相符"
    assert saved["original_review"]["reference_answer"] == "7"
    assert saved["reason"] == "与题目要求不符"
    assert saved["reference_answer"] == "8"
    assert saved["solution_steps"] == ["3+5=8"]
    assert saved["recheck"]["status"] == "check_again"
    assert "复检后当前结果" in client.get(
        "/v1/families/dashboard", headers=p).json()["corrections"][0]["summary"]
    client.patch(f"/v1/sessions/{sid}/correction-access", json={"allowed": True}, headers=p)
    assert client.post(url, headers=c).status_code == 200
    assert client.post(url, headers=p).status_code == 200
    for _ in range(3):
        assert client.post(url, headers=p).status_code == 200
    saved = client.get("/v1/families/dashboard", headers=p).json()["corrections"][0]["checks"][0]
    assert saved["original_review"]["status"] == "looks_correct"
    assert saved["recheck"]["source_status"] == "looks_correct"
    assert client.post(url, headers=p).status_code == 429


def test_parent_can_correct_one_result_and_position_without_losing_ai_record(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    other = parent(client, "other-parent-review@example.com")
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    client.patch(f"/v1/sessions/{sid}/correction-access", json={"allowed": True}, headers=p)
    image = io.BytesIO()
    Image.new("RGB", (1200, 800), "white").save(image, format="JPEG")
    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs: {
        "response": "已检查", "confidence": .9,
        "checks": [{"question": "第 1 题", "status": "check_again", "answer": "7",
                    "evidence": "原图答案可见", "reason": "模型判断有误",
                    "explanation": "原模型的解题说明", "confidence": .9,
                    "box": [.3, .3, .1, .1]}],
    })
    correction = client.post(f"/v1/sessions/{sid}/corrections",
                             files={"image": ("page.jpg", io.BytesIO(image.getvalue()), "image/jpeg")},
                             headers=c).json()
    url = f"/v1/corrections/{correction['id']}/checks/0/parent-review"
    body = {"status": "looks_correct", "answer": "7", "note": "与纸面要求相符",
            "box": [.4, .25, .12, .08], "revision": 0}
    assert client.patch(url, json=body, headers=c).status_code == 403
    assert client.patch(url, json=body, headers=other).status_code == 404
    assert client.patch(url, json={**body, "note": ""}, headers=p).status_code == 422
    assert client.patch(url, json={**body, "box": [.98, .25, .12, .08]}, headers=p).status_code == 422
    saved = client.patch(url, json=body, headers=p)
    assert saved.status_code == 200, saved.text
    assert saved.json()["check"]["status"] == "looks_correct"
    assert saved.json()["check"]["parent_review"]["revision"] == 1
    assert client.patch(url, json=body, headers=p).status_code == 409
    child_check = client.get("/v1/families/dashboard", headers=c).json()["corrections"][0]["checks"][0]
    assert child_check["status"] == "looks_correct"
    assert child_check["box"] == [.4, .25, .12, .08]
    assert child_check["original_review"]["status"] == "check_again"
    assert child_check["ai_review"]["reason"] == "模型判断有误"
    assert child_check["parent_review_history"][0]["previous"]["status"] == "check_again"
    assert client.get("/v1/families/dashboard", headers=p).json()["corrections"][0]["review_status"] == "unreviewed"
    followup = {"status": "needs_discussion", "answer": "7", "note": "开放题可有不同答案",
                "box": None, "revision": 1}
    assert client.patch(url, json=followup, headers=p).status_code == 200
    latest = client.get("/v1/families/dashboard", headers=p).json()["corrections"][0]["checks"][0]
    assert latest["status"] == "needs_discussion"
    assert latest["box"] is None
    assert latest["parent_revision"] == 2
    assert len(latest["parent_review_history"]) == 2
    monkeypatch.setattr(module, "recheck_single", lambda *args, **kwargs: {
        "status": "check_again", "answer": "7", "evidence": "模型另有判断",
        "reason": "需核对", "explanation": "参考解法", "confidence": .9,
    })
    recheck_url = f"/v1/corrections/{correction['id']}/checks/0/recheck"
    assert client.post(recheck_url, headers=p).status_code == 200
    latest = client.get("/v1/families/dashboard", headers=p).json()["corrections"][0]["checks"][0]
    assert latest["status"] == "needs_discussion"
    assert latest["parent_review"]["status"] == "needs_discussion"
    assert latest["ai_review"]["status"] == "check_again"


def test_ephemeral_image_and_ai_failure(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    image = b"\xff\xd8test-jpeg-content\xff\xd9"
    setup = client.post("/v1/setup/check", files={"image": ("paper.jpg", io.BytesIO(image), "image/jpeg")}, headers=c)
    assert setup.status_code == 503
    images_seen = []

    def fake_analysis(*args, **kwargs):
        images_seen.append(args[-1] if len(args) > 7 else None)
        return {"ready": True, "confidence": .9, "label": "清晰", "evidence": "完整纸面",
                "response": "先读题", "needs_parent": False, "progress": "第一题有新笔迹",
                "writing": "文字清楚", "visible_steps": "写出算式",
                "checks": [{"question": "第 1 题", "status": "check_again", "answer": "12",
                            "evidence": "纸上写了 12", "reason": "算式需要复核", "next_step": "检查加法",
                            "confidence": .9}]}

    monkeypatch.setattr(module, "analyze", fake_analysis)
    setup = client.post("/v1/setup/check", files={"image": ("paper.jpg", io.BytesIO(image), "image/jpeg")}, headers=c)
    assert setup.json()["ready"] is True
    assert setup.json()["issue_box"] is None
    sid = client.post("/v1/sessions", json={"setup_ticket": setup.json()["setup_ticket"]}, headers=c).json()["id"]
    reflection = client.post(f"/v1/sessions/{sid}/reflections", json={"text": "我先把 12 分成三组"}, headers=c)
    assert reflection.status_code == 200
    assert client.get("/v1/families/dashboard", headers=p).json()["reflections"][0]["text"] == "我先把 12 分成三组"
    observation = client.post(f"/v1/sessions/{sid}/observations",
                              files={"image": ("crop.jpg", io.BytesIO(image), "image/jpeg")}, headers=c)
    assert observation.status_code == 200
    second = client.post(f"/v1/sessions/{sid}/observations", files=[
        ("image", ("now.jpg", io.BytesIO(image), "image/jpeg")),
        ("previous_image", ("before.jpg", io.BytesIO(image), "image/jpeg")),
    ], headers=c)
    assert second.status_code == 200
    assert images_seen[-1] == image
    assert client.get("/v1/families/dashboard", headers=p).json()["observations"][0]["visible_steps"] == "写出算式"
    hint = client.post("/v1/hints", data={"session_id": sid, "question": "这题怎么做", "level": 1,
                                          "selected_region": "true"},
                       files={"image": ("crop.jpg", io.BytesIO(image), "image/jpeg")}, headers=c)
    assert hint.json()["response"] == "先读题"
    assert hint.json()["level"] == 1
    assert client.post(f"/v1/sessions/{sid}/corrections",
                       files={"image": ("page.jpg", io.BytesIO(image), "image/jpeg")}, headers=c).status_code == 403
    request = client.post(f"/v1/sessions/{sid}/correction-request", headers=c)
    assert request.status_code == 200 and request.json()["allowed"] is False
    assert client.get("/v1/families/dashboard", headers=p).json()["session"]["correction_requested_at"]
    other = parent(client, "other-correction@example.com")
    assert client.patch(f"/v1/sessions/{sid}/correction-access",
                        json={"allowed": True}, headers=other).status_code == 404
    assert client.patch(f"/v1/sessions/{sid}/correction-access",
                        json={"allowed": True}, headers=c).status_code == 403
    approved = client.patch(f"/v1/sessions/{sid}/correction-access",
                            json={"allowed": True}, headers=p)
    assert approved.status_code == 200 and approved.json()["authorized_until"] > int(time.time())
    correction = client.post(f"/v1/sessions/{sid}/corrections",
                             files={"image": ("page.jpg", io.BytesIO(image), "image/jpeg")}, headers=c)
    assert correction.status_code == 200
    assert correction.json()["checks"][0]["status"] == "check_again"
    assert client.get("/v1/families/dashboard", headers=p).json()["corrections"][0]["checks"][0]["answer"] == "12"
    assert client.get(f"/v1/corrections/{correction.json()['id']}/image", headers=p).content == image
    assert client.get(f"/v1/corrections/{correction.json()['id']}/image", headers=c).content == image
    assert client.post(f"/v1/sessions/{sid}/corrections",
                       files={"image": ("page.jpg", io.BytesIO(image), "image/jpeg")}, headers=p).status_code == 403
    assert client.patch(f"/v1/corrections/{correction.json()['id']}",
                        json={"review_status": "accepted"}, headers=c).status_code == 403
    assert client.get("/v1/families/dashboard", headers=other).json()["corrections"] == []
    assert client.get(f"/v1/corrections/{correction.json()['id']}/image", headers=other).status_code == 404
    assert client.patch(f"/v1/corrections/{correction.json()['id']}",
                        json={"review_status": "accepted"}, headers=other).status_code == 404
    assert client.patch(f"/v1/corrections/{correction.json()['id']}",
                        json={"review_status": "accepted"}, headers=p).status_code == 200
    assert client.patch(f"/v1/sessions/{sid}/correction-access",
                        json={"allowed": False}, headers=p).status_code == 200
    assert client.get("/v1/families/dashboard", headers=c).json()["corrections"] == []
    assert client.get(f"/v1/corrections/{correction.json()['id']}/image", headers=c).status_code == 403
    assert client.post(f"/v1/sessions/{sid}/corrections",
                       files={"image": ("page.jpg", io.BytesIO(image), "image/jpeg")}, headers=c).status_code == 403
    assert client.patch(f"/v1/sessions/{sid}/correction-access",
                        json={"allowed": True}, headers=p).status_code == 200
    assert client.patch(f"/v1/sessions/{sid}", json={"status": "paused"}, headers=c).status_code == 200
    assert client.get("/v1/families/dashboard", headers=p).json()["session"]["correction_authorized_until"] is None
    assert client.post(f"/v1/sessions/{sid}/correction-request", headers=c).status_code == 409
    assert client.patch(f"/v1/sessions/{sid}", json={"status": "active"}, headers=c).status_code == 403
    fresh = client.post("/v1/setup/check", files={"image": ("paper.jpg", io.BytesIO(image), "image/jpeg")}, headers=c)
    assert client.patch(f"/v1/sessions/{sid}",
                        json={"status": "active", "setup_ticket": fresh.json()["setup_ticket"]},
                        headers=c).status_code == 200
    assert client.patch(f"/v1/observations/{observation.json()['id']}",
                        json={"review_status": "rejected"}, headers=p).status_code == 200
    assert not list(tmp_path.glob("*.jpg"))
    assert client.post("/v1/hints", data={"session_id": sid, "question": "x"},
                       files={"image": ("crop.png", io.BytesIO(image), "image/png")}, headers=c).status_code == 415
    with module.transaction(module.settings.database_path) as db:
        db.execute("UPDATE sessions SET expires_at=? WHERE id=?", (int(time.time()) + 86400, sid))
    module.purge_expired(module.settings.database_path, now=int(time.time()) + 2 * 86400)
    assert module.one("SELECT COUNT(*) AS count FROM hints")["count"] == 0
    assert module.one("SELECT COUNT(*) AS count FROM corrections")["count"] == 0
    assert module.one("SELECT COUNT(*) AS count FROM reflections")["count"] == 0
    assert module.one("SELECT COUNT(*) AS count FROM sessions")["count"] == 0


def test_auth_and_realtime_ticket(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    p = parent(client)
    assert client.get("/v1/families/dashboard").status_code == 401
    assert client.post("/v1/auth/login", json={"email": "parent@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/v1/realtime/ticket", headers=p).status_code == 200
    assert client.get("/v1/ice", headers=p).json()["turn_configured"] is False
    for _ in range(4):
        assert client.post("/v1/auth/login", json={"email": "parent@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "parent@example.com", "password": "wrong"}).status_code == 429


def test_batch_pages_keep_matching_photo_and_subject(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    tasks = client.post("/v1/tasks/import", json={"text": "数学：第 43 页\n语文：第 8 页"}, headers=p).json()["tasks"]
    child_id = module.read_token(module.settings.app_secret, c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    client.patch(f"/v1/sessions/{sid}/correction-access", json={"allowed": True}, headers=p)
    monkeypatch.setattr(module, "analyze", lambda *args, **kwargs: {
        "response": "已检查这张图", "confidence": .9,
        "checks": [{"question": "第 1 题", "status": "unclear", "answer": "",
                    "evidence": "字迹太浅", "reason": "无法判断", "next_step": "重拍", "confidence": .6}],
    })
    pages = [
        (b"\xff\xd8math-photo\xff\xd9", "数学", "第 43 页", tasks[0]["id"]),
        (b"\xff\xd8chinese-photo\xff\xd9", "语文", "第 8 页", tasks[1]["id"]),
    ]
    batch_id = "batch-test-2026"
    for index, (image, subject, label, task_id) in enumerate(pages, 1):
        response = client.post(f"/v1/sessions/{sid}/corrections", data={
            "task_id": task_id, "subject_label": subject, "page_label": label,
            "batch_id": batch_id, "page_index": index, "batch_total": 2,
        }, files={"image": (f"page-{index}.jpg", io.BytesIO(image), "image/jpeg")}, headers=c)
        assert response.status_code == 200, response.text
        assert response.json()["page_index"] == index
        assert client.get(f"/v1/corrections/{response.json()['id']}/image", headers=p).content == image
    records = client.get("/v1/families/dashboard", headers=p).json()["corrections"]
    assert all(item["image_available"] and "image_jpeg" not in item for item in records)
    assert [(item["subject_label"], item["page_label"], item["batch_id"]) for item in reversed(records)] == [
        ("数学", "第 43 页", batch_id), ("语文", "第 8 页", batch_id),
    ]
    assert client.post("/v1/data/delete", headers=p).status_code == 200
    assert client.get(f"/v1/corrections/{records[0]['id']}/image", headers=p).status_code == 404


def test_underline_hint_and_correction_locate_question_above_mark(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    photo = io.BytesIO()
    Image.new("RGB", (1200, 800), "white").save(photo, format="JPEG")
    page = photo.getvalue()
    marks = [[[.2, .53], [.7, .53]]]
    seen = []
    location = {"found": True, "box": [.18, .3, .62, .2],
                "label": "第 2 题", "evidence": "下划线上方第 2 题的完整题干"}

    def locate(*args):
        seen.append(args)
        return dict(location)

    def analyze(*args, **kwargs):
        if "教师式辅导" in args[2]:
            return {"target_mismatch": False, "response": "先读第 2 题题干。",
                    "needs_parent": False}
        return {"response": "已检查", "confidence": .9, "checks": [{
            "question": "第 2 题", "status": "check_again", "box": None,
            "answer": "7", "evidence": "答案 7", "reason": "需重算",
            "next_step": "重算", "confidence": .9,
        }]}

    monkeypatch.setattr(module, "locate_question", locate)
    monkeypatch.setattr(module, "analyze", analyze)

    def hint(data):
        return client.post("/v1/hints", data={"session_id": sid, "question": "给我提示", **data},
                           files={"image": ("page.jpg", io.BytesIO(page), "image/jpeg")}, headers=c)

    response = hint({"selected_strokes": json.dumps(marks)})
    assert response.status_code == 200, response.text
    assert response.json()["target_box"] == location["box"]
    assert seen[0][1] == page and seen[0][5] == marks
    assert hint({"selected_strokes": "[[[0.2,0.5],[2,0.5]]]"}).status_code == 422
    location["found"] = False
    ambiguous = hint({"selected_strokes": json.dumps(marks)}).json()
    assert ambiguous["located"] is False
    location["found"] = True

    client.patch(f"/v1/sessions/{sid}/correction-access", json={"allowed": True}, headers=p)

    def correct():
        return client.post(f"/v1/sessions/{sid}/corrections",
                           data={"selected_strokes": json.dumps(marks)},
                           files={"image": ("page.jpg", io.BytesIO(page), "image/jpeg")}, headers=c)

    response = correct()
    assert response.status_code == 200, response.text
    assert response.json()["checks"][0]["question"] == "第 2 题"
    assert seen[-1][5] == marks
    location["found"] = False
    assert correct().status_code == 422


def test_selected_question_correction_keeps_full_photo_and_only_target_marks(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    photo = io.BytesIO()
    Image.new("RGB", (1200, 800), "white").save(photo, format="JPEG")
    original = photo.getvalue()
    seen = []

    def fake_analyze(*args, **kwargs):
        seen.append((args, kwargs))
        return {"response": "已检查", "confidence": .85, "checks": [
            {"question": "框内题", "status": "check_again", "box": [.38, .4, .1, .1],
             "answer": "7", "evidence": "纸上有答案", "reason": "需要重算", "next_step": "重算", "confidence": .8},
            {"question": "相邻题", "status": "looks_correct", "box": [.88, .8, .08, .08],
             "answer": "8", "evidence": "纸上有答案", "reason": "正确", "next_step": "下一题", "confidence": .8},
        ]}

    monkeypatch.setattr(module, "analyze", fake_analyze)
    payload = {"selected_box": "[0.15, 0.2, 0.2, 0.2]", "page_label": "框选题目"}

    def submit(data):
        return client.post(f"/v1/sessions/{sid}/corrections", data=data,
                           files={"image": ("paper.jpg", io.BytesIO(original), "image/jpeg")}, headers=c)

    assert submit(payload).status_code == 403
    client.patch(f"/v1/sessions/{sid}/correction-access", json={"allowed": True}, headers=p)
    assert submit({**payload, "selected_box": "[2, 0, 1, 1]"}).status_code == 422
    response = submit(payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [check["question"] for check in result["checks"]] == ["框内题"]
    assert result["checks"][0]["box"] is not None
    assert seen[0][1]["context_image"] == original
    assert "只批改框选的这一题" in seen[0][0][2]
    assert client.get(f"/v1/corrections/{result['id']}/image", headers=p).content == original


def test_realtime_annotation_reaches_only_paired_child(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    with client:
        p = parent(client)
        c = pair(client, p)
        child_id = module.read_token(module.settings.app_secret, c["Authorization"].removeprefix("Bearer "))["sub"]
        proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
        assert client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).status_code == 200
        assert client.post("/v1/live-sessions", headers=p).status_code == 200
        pt = client.post("/v1/realtime/ticket", headers=p).json()["ticket"]
        ct = client.post("/v1/realtime/ticket", headers=c).json()["ticket"]
        with client.websocket_connect("/v1/realtime", subprotocols=["k12", pt]) as parent_ws:
            with client.websocket_connect("/v1/realtime", subprotocols=["k12", ct]) as child_ws:
                assert parent_ws.receive_json() == {"type": "peer_ready", "role": "child"}
                parent_ws.send_json({"type": "annotation", "points": [[0.1, 0.2], [0.3, 0.4]]})
                assert child_ws.receive_json() == {"type": "annotation", "points": [[0.1, 0.2], [0.3, 0.4]]}


def test_turn_credentials_match_shared_secret(monkeypatch, tmp_path):
    secret = "turn-shared-secret-abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setenv("TURN_HOST", "turn.example.com")
    monkeypatch.setenv("TURN_SHARED_SECRET", secret)
    client, _ = make_client(monkeypatch, tmp_path)
    headers = parent(client)
    ice = client.get("/v1/ice", headers=headers).json()
    assert ice["turn_configured"] is True
    turn = ice["iceServers"][1]
    assert turn["urls"][0].startswith("turn:turn.example.com:3478")
    expected = base64.b64encode(hmac.new(secret.encode(), turn["username"].encode(), hashlib.sha1).digest()).decode()
    assert turn["credential"] == expected


def test_parent_model_choices_and_verified_sentence_stream(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    assert client.get("/v1/families/ai-preferences", headers=c).status_code == 403
    default = client.get("/v1/families/ai-preferences", headers=p).json()
    assert default["solver_model"] == "gpt-6.1-sol"
    assert default["grading_model"] == "gpt-6-astra"
    assert "gpt-6.1-sol" in default["available_models"]
    assert client.patch("/v1/families/ai-preferences", headers=p, json={
        "solver_model": "unlisted", "locator_model": "gpt-6-sol",
        "tutor_model": "gpt-6-luna", "precompute_enabled": True,
    }).status_code == 422
    assert client.patch("/v1/families/ai-preferences", headers=p, json={
        "solver_model": "gpt-6.1-sol", "grading_model": "unlisted",
        "locator_model": "gpt-6-sol", "tutor_model": "gpt-6-luna",
        "precompute_enabled": True,
    }).status_code == 422
    saved = client.patch("/v1/families/ai-preferences", headers=p, json={
        "solver_model": "gpt-6-astra", "grading_model": "gpt-6.1-sol",
        "locator_model": "gpt-6-sol",
        "tutor_model": "gpt-6-luna", "precompute_enabled": True,
    })
    assert saved.status_code == 200 and saved.json()["version"] == 1
    assert saved.json()["grading_model"] == "gpt-6.1-sol"
    with module.connect(module.settings.database_path) as db:
        family_id = db.execute("SELECT id FROM families LIMIT 1").fetchone()[0]
    assert module.grading_settings(family_id).ai_model == "gpt-6.1-sol"

    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    image = output.getvalue()

    class Prepared:
        def match(self, *_args, **_kwargs):
            return {"id": "q", "label": "二、3、(1)", "box": [.2, .3, .4, .2],
                    "evidence": "求两个数的和", "read_confidence": .95,
                    "solution": {"answer": "42", "steps": "先找两个已知量，再相加得42"},
                    "verification_status": "verified"}

    monkeypatch.setattr(module, "page_preparation", lambda: Prepared())
    monkeypatch.setattr(module, "stream_model", lambda *_args: iter([
        "先找", "到两个已知量。你愿意", "先列出它们吗？",
    ]))
    response = client.post("/v1/hints/stream", data={
        "session_id": sid, "question": "第二大题第三小题第一问怎么做", "level": 1,
    }, files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert response.status_code == 200
    assert response.text.count("event: beat") == 2
    assert "event: done" in response.text
    beats = [json.loads(block.split("data: ", 1)[1])
             for block in response.text.split("\n\n") if block.startswith("event: beat")]
    sentences = [beat["speech"] for beat in beats]
    assert all("42" not in sentence for sentence in sentences)
    assert all(beat["scratch"] == [] for beat in beats)
    with module.connect(module.settings.database_path) as db:
        saved_hint = db.execute("SELECT response FROM hints WHERE session_id=?", (sid,)).fetchone()
    assert saved_hint and "先找到两个已知量" in saved_hint["response"]
    monkeypatch.setattr(module, "page_preparation", lambda: type("NoCache", (), {
        "match": lambda self, *_args, **_kwargs: None,
    })())
    monkeypatch.setattr(module, "locate_question", lambda *_args, **_kwargs: {
        "found": True, "label": "二、3、(1)", "box": [.2, .3, .4, .2],
        "evidence": "求两个数的和", "confidence": .9,
    })
    uncached = client.post("/v1/hints/stream", data={"session_id": sid,
        "question": "这道题怎么做"}, files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert uncached.status_code == 200 and uncached.text.count("event: beat") == 2


def test_stream_hint_fallback_preserves_jpeg_upload(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    monkeypatch.setattr(module, "page_preparation", lambda: type("NoCache", (), {
        "match": lambda self, *_args, **_kwargs: None,
    })())
    monkeypatch.setattr(module, "locate_question", lambda *_args, **_kwargs: {"found": False})

    response = client.post("/v1/hints/stream", data={
        "session_id": sid, "question": "这道题怎么做",
    }, files={"image": ("page.jpg", output.getvalue(), "image/jpeg")}, headers=c)

    assert response.status_code == 200
    assert "event: done" in response.text
    assert '"located": false' in response.text
    assert "JPEG image required" not in response.text
    assert "event: error" not in response.text


def test_open_ended_drawing_uses_complete_draft_in_one_hint(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")

    class Prepared:
        def match(self, *_args, **_kwargs):
            return {"label": "4", "box": [.5, .2, .3, .2], "evidence": "把四片叶片拼成图形",
                    "read_confidence": .78, "verification_status": "uncertain",
                    "solution": {"answer": "可拼成正方形，也可拼成长方形。",
                                 "steps": "先比较四片叶片的边，再把等长边贴合。",
                                 "confidence": .94, "ambiguous": True}}

    monkeypatch.setattr(module, "page_preparation", lambda: Prepared())
    monkeypatch.setattr(module, "solve_question", lambda *_args: (_ for _ in ()).throw(
        AssertionError("A grounded drawing approach should use the prepared solution")))
    beats = [
        {"speech": "先看四片叶片的形状。", "page": [], "scratch": [
            {"kind": "text", "x": 10, "y": 20, "text": "四片叶片"}]},
        {"speech": "再把相同长度的边贴合，最后的轮廓留给你画。", "page": [], "scratch": [
            {"kind": "text", "x": 10, "y": 40, "text": "等长边 → 贴合 → ____"}]},
    ]
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_args: iter(
        ("beat", beat) for beat in beats))

    response = client.post("/v1/hints/stream", data={
        "session_id": sid, "question": "这道题怎么做"},
        files={"image": ("page.jpg", output.getvalue(), "image/jpeg")}, headers=c)
    assert response.status_code == 200
    assert response.text.count("event: beat") == 2
    assert "event: error" not in response.text
    assert "等长边 → 贴合 → ____" in response.text
    assert "可拼成正方形" not in response.text


def test_streamed_teaching_beats_and_idle_offer_share_the_located_paper(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    image = output.getvalue()
    target_box = [.2, .3, .4, .2]

    class Prepared:
        def match(self, *_args, **_kwargs):
            return {"label": "二、3、(1)", "box": target_box,
                    "evidence": "求两个数的和", "read_confidence": .95,
                    "solution": {"answer": "42", "steps": "先列出两个量再相加",
                                 "confidence": .96, "ambiguous": False},
                    "verification_status": "verified"}

    monkeypatch.setattr(module, "page_preparation", lambda: Prepared())
    beats = [
        {"speech": "先圈出两个已知量。", "page": [{"kind": "circle", "box": target_box}],
         "scratch": []},
        {"speech": "在草稿上写出它们的关系。", "page": [],
         "scratch": [{"kind": "text", "x": 20, "y": 25, "text": "关系＝____"}]},
    ]
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_args: iter(
        ("beat", beat) for beat in beats))
    response = client.post("/v1/hints/stream", data={"session_id": sid,
        "question": "第二大题第三小题第一问"},
        files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert response.status_code == 200
    assert response.text.count("event: beat") == 2
    assert "event: sentence" not in response.text
    assert '"visual_beats":' in response.text

    monkeypatch.setattr(module, "assess_idle_checkin", lambda *_args: {
        "offer": True, "progress": False, "page_changed": False})
    monkeypatch.setattr(module, "locate_question", lambda *_args, **_kwargs: {
        "found": True, "label": "二、3、(1)", "box": target_box,
        "evidence": "求两个数的和", "confidence": .92})
    idle = client.post(f"/v1/sessions/{sid}/idle-checkin",
                       files={"image": ("current.jpg", image, "image/jpeg"),
                              "reference_image": ("earlier.jpg", image, "image/jpeg")},
                       headers=c)
    assert idle.status_code == 200
    assert idle.json()["target_box"] == target_box
    assert idle.json()["candidate_id"]
    with module.connect(module.settings.database_path) as db:
        saved = db.execute("SELECT box_json FROM help_candidates WHERE id=?",
                           (idle.json()["candidate_id"],)).fetchone()
    assert json.loads(saved["box_json"]) == target_box
    monkeypatch.setattr(module, "solve_question", lambda *_args: {
        "answer": "42", "steps": "先列出两个量再相加",
        "confidence": .96, "ambiguous": False})
    accepted = client.post("/v1/hints/stream", data={"session_id": sid,
        "question": "请帮我看看", "candidate_id": idle.json()["candidate_id"]},
        files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert accepted.status_code == 200
    assert accepted.text.count("event: beat") == 2
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT id FROM help_candidates WHERE id=?",
                          (idle.json()["candidate_id"],)).fetchone() is None
        accepted_hints = db.execute("SELECT COUNT(*) FROM hints WHERE session_id=? "
                                    "AND source='proactive_accepted'", (sid,)).fetchone()[0]
    assert accepted_hints == 1
    marked = client.post("/v1/hints/stream", data={"session_id": sid,
        "question": "请看我画线的题", "selected_strokes": json.dumps([
            [[.22, .43], [.29, .43], [.38, .43]]])},
        files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert marked.status_code == 200
    assert marked.text.count("event: beat") == 2


def test_spoken_answer_confirmation_uses_checked_solution_in_both_hint_paths(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    image = output.getvalue()

    class Prepared:
        def match(self, *_args, **_kwargs):
            return {"id": "q", "label": "二、3、(1)", "box": [.2, .3, .4, .2],
                    "evidence": "一小时的一半是多少分钟？", "read_confidence": .96,
                    "solution": {"answer": "30分钟", "steps": "60除以2得30",
                                 "confidence": .97, "ambiguous": False},
                    "verification_status": "verified"}

    monkeypatch.setattr(module, "page_preparation", lambda: Prepared())
    monkeypatch.setattr(module, "assess_spoken_answer", lambda *_args: "对，回答正确。半小时就是三十分钟。")
    monkeypatch.setattr(module, "stream_model", lambda *_args: (_ for _ in ()).throw(
        AssertionError("A correct answer should not request another hint")))
    data = {"session_id": sid, "question": "第二大题第三小题第一问", "level": 1,
            "student_thought": "半小时，也就是三十分钟"}
    streamed = client.post("/v1/hints/stream", data=data,
                           files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert streamed.status_code == 200
    assert "event: visual" in streamed.text
    assert 'event: sentence\ndata: {"text": "对，回答正确。半小时就是三十分钟。"}' in streamed.text
    assert '"answer_assessment": "correct"' in streamed.text
    assert '"reply_mode": "none"' in streamed.text
    assert "event: error" not in streamed.text

    monkeypatch.setattr(module, "analyze", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("A correct answer should not trigger general analysis")))
    normal = client.post("/v1/hints", data=data,
                         files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert normal.status_code == 200
    assert normal.json()["response"] == "对，回答正确。半小时就是三十分钟。"
    assert normal.json()["reply_mode"] == "none"
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM hints WHERE session_id=?", (sid,)).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM reflections WHERE session_id=?",
                          (sid,)).fetchone()[0] == 2

    monkeypatch.setattr(module, "assess_spoken_answer", lambda *_args: None)
    monkeypatch.setattr(module, "stream_model", lambda *_args: iter([
        "先想一小时有多少分钟。",
    ]))
    incomplete = client.post("/v1/hints/stream", data={**data, "student_thought": "30"},
                             files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert incomplete.status_code == 200
    assert "回答正确" not in incomplete.text
    assert '"answer_assessment": "correct"' not in incomplete.text
    assert "先想一小时有多少分钟" in incomplete.text

    monkeypatch.setattr(module, "page_preparation", lambda: type("NoCache", (), {
        "match": lambda self, *_args, **_kwargs: None,
    })())
    monkeypatch.setattr(module, "locate_question", lambda *_args, **_kwargs: {
        "found": True, "label": "二、3、(1)", "box": [.2, .3, .4, .2],
        "evidence": "一小时的一半是多少分钟？", "confidence": .96,
    })
    solved = []
    def solve(solver_settings, *_args):
        solved.append(solver_settings.ai_model)
        return {"answer": "30分钟", "steps": "60除以2得30",
                "confidence": .97, "ambiguous": False}

    monkeypatch.setattr(module, "solve_question", solve)
    monkeypatch.setattr(module, "assess_spoken_answer", lambda *_args: "对，回答正确。半小时就是三十分钟。")
    uncached = client.post("/v1/hints/stream", data=data,
                           files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert uncached.status_code == 200
    assert '"answer_assessment": "correct"' in uncached.text
    assert solved == ["gpt-6.1-sol"]


def test_page_preparation_prioritizes_current_then_invalidates_on_parent_change(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    image = output.getvalue()
    import k12_server.page_prep as prep

    inventory_calls, solved = [], []
    def inventory(_settings, _image, previous=None):
        inventory_calls.append(1)
        if previous is not None:
            assert len(previous) == 4 and "progress" not in previous[0]
        return [{"path": ["一", str(i)], "label": f"一、{i}", "evidence": f"可读题干{i}",
                 "box": [.1, .1 * i, .4, .08],
                 "progress": "done" if i == 1 else "blank", "confidence": .95}
                for i in range(1, 5)]

    def solve(settings, _image, question, independent=False):
        assert settings.ai_model == "gpt-6.1-sol" and not independent
        solved.append(question["label"])
        return {"answer": "结果", "steps": "先找条件", "confidence": .96, "ambiguous": False}

    monkeypatch.setattr(prep, "inventory_page", inventory)
    monkeypatch.setattr(prep, "solve_question", solve)
    service = prep.PagePreparation(module.settings)
    monkeypatch.setattr(module, "page_preparation", lambda: service)
    try:
        url = f"/v1/sessions/{sid}/page-prep"
        assert client.post(url, files={"image": ("page.jpg", image, "image/jpeg")},
                           headers=p).status_code == 403
        first = client.post(url, files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
        assert first.status_code == 200 and first.json()["status"] == "preparing"
        deadline = time.monotonic() + 3
        while service.status(sid)["status"] != "ready" and time.monotonic() < deadline:
            time.sleep(.02)
        assert service.status(sid) == {"status": "ready", "total": 4, "prepared": 4,
                                       "inventory_ready": True}
        assert solved[0] == "一、2" and set(solved) == {"一、1", "一、2", "一、3", "一、4"}
        assert service.match(sid, image, "第一题", spoken=True)["label"] == "一、2"
        assert service.match_strokes(sid, image, [[[.16, .29], [.46, .29]]])["label"] == "一、2"
        assert service.match_strokes(sid, image, [
            [[.16, .29], [.46, .29]], [[.16, .39], [.46, .39]],
        ]) is None
        again = client.post(url, files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
        assert again.json()["page_id"] == first.json()["page_id"]
        assert len(inventory_calls) == 1
        changed = Image.new("RGB", (900, 1200), "white")
        draw = ImageDraw.Draw(changed)
        for x in range(0, 900, 100):
            draw.rectangle((x, 0, x + 50, 1199), fill="black")
        output = io.BytesIO()
        changed.save(output, format="JPEG")
        changed_image = output.getvalue()
        assert not prep.same_paper(prep.image_fingerprint(image),
                                   prep.image_fingerprint(changed_image))
        with module.connect(module.settings.database_path) as db:
            family_id = db.execute("SELECT family_id FROM sessions WHERE id=?",
                                   (sid,)).fetchone()["family_id"]
        submit = service.executor.submit
        service.executor.submit = lambda *_args: None
        try:
            pending = service.schedule(sid, family_id, changed_image)
            changed_again = Image.new("RGB", (900, 1200), "white")
            draw = ImageDraw.Draw(changed_again)
            for x in range(0, 900, 150):
                draw.rectangle((x, 0, x + 70, 1199), fill="black")
            output = io.BytesIO()
            changed_again.save(output, format="JPEG")
            other_image = output.getvalue()
            assert not prep.same_paper(prep.image_fingerprint(changed_image),
                                       prep.image_fingerprint(other_image))
            next_pending = service.schedule(sid, family_id, other_image)
        finally:
            service.executor.submit = submit
        assert pending["status"] == "preparing"
        assert next_pending["status"] == "preparing"
        with module.connect(module.settings.database_path) as db:
            assert db.execute("SELECT status FROM page_preparations WHERE id=?",
                              (first.json()["page_id"],)).fetchone()["status"] == "ready"
            assert db.execute("SELECT status FROM page_preparations WHERE id=?",
                              (pending["page_id"],)).fetchone()["status"] == "preparing"
        assert service.match(sid, image, "第一题", spoken=True)["label"] == "一、2"
        assert client.post(f"/v1/sessions/{sid}/finish", headers=c).status_code == 200
        next_proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
        next_sid = client.post("/v1/sessions", json={"setup_ticket": next_proof},
                               headers=c).json()["id"]
        assert client.post(f"/v1/sessions/{next_sid}/page-prep",
                           files={"image": ("page.jpg", image, "image/jpeg")},
                           headers=c).status_code == 200
        deadline = time.monotonic() + 3
        while service.status(next_sid)["status"] != "ready" and time.monotonic() < deadline:
            time.sleep(.02)
        assert service.status(next_sid)["prepared"] == 4
        assert len(inventory_calls) == 2
        assert len(solved) == 4
        updated = client.patch("/v1/families/ai-preferences", headers=p, json={
            "solver_model": "gpt-6-astra", "locator_model": "gpt-6-sol",
            "tutor_model": "gpt-6-luna", "precompute_enabled": False,
        })
        assert updated.status_code == 200
        assert service.match(next_sid, image, "第一题", spoken=True) is None
        assert client.post(f"/v1/sessions/{next_sid}/page-prep",
                           files={"image": ("page.jpg", image, "image/jpeg")},
                           headers=c).json()["status"] == "disabled"
        assert client.post("/v1/data/delete", headers=p).status_code == 200
        with module.connect(module.settings.database_path) as db:
            assert db.execute("SELECT COUNT(*) FROM family_question_cache").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM family_page_cache").fetchone()[0] == 0
    finally:
        service.close()


def test_marked_hint_uses_prepared_question_without_relocating_or_resolving(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    child_id = module.read_token(module.settings.app_secret,
                                 c["Authorization"].removeprefix("Bearer "))["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    image = output.getvalue()

    class Prepared:
        def match_strokes(self, *_args):
            return {"id": "prepared-2", "label": "二、3、(1)", "box": [.2, .3, .4, .2],
                    "evidence": "求两个数的和", "read_confidence": .96, "progress": "partial",
                    "solution": {"answer": "42", "steps": "先列出两个量再相加",
                                 "hint_seed": "先圈出两个已知量", "confidence": .96,
                                 "ambiguous": False}, "verification_status": "verified"}

    def unexpected(*_args, **_kwargs):
        raise AssertionError("prepared target should avoid a second locate or solve")

    monkeypatch.setattr(module, "page_preparation", lambda: Prepared())
    monkeypatch.setattr(module, "locate_question", unexpected)
    monkeypatch.setattr(module, "solve_question", unexpected)
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_args: iter([(
        "beat", {"speech": "在草稿上写出它们的关系。", "page": [], "scratch": []})]))
    response = client.post("/v1/hints/stream", data={
        "session_id": sid, "question": "画线这题给我提示",
        "selected_strokes": json.dumps([[[.25, .52], [.55, .52]]]),
    }, files={"image": ("page.jpg", image, "image/jpeg")}, headers=c)
    assert response.status_code == 200
    assert '"question_id": "prepared-2"' in response.text
    assert response.text.count("event: beat") == 1
