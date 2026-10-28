"""A current lesson shares an in-flight solve instead of teaching a stale snapshot."""

from __future__ import annotations

import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Thread

import pytest
from PIL import Image, ImageDraw
from test_api import make_client, pair, parent

from k12_server import page_prep
from k12_server.page_prep import PagePreparation, image_fingerprint

SOLUTION = {"answer": "17", "steps": "逐项对应题目条件，完成计算后代回检查。",
            "confidence": .97, "ambiguous": False}


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    child = pair(client, parent(client))
    claims = module.read_token(module.settings.app_secret, child["Authorization"].removeprefix("Bearer "))
    ticket = module.make_token(module.settings.app_secret, "setup", claims["sub"], 120)
    session = client.post("/v1/sessions", json={"setup_ticket": ticket}, headers=child).json()["id"]
    family = module.family_for(claims)
    picture = Image.new("RGB", (640, 960), "white")
    draw = ImageDraw.Draw(picture)
    for y in range(120, 800, 100):
        draw.rectangle((80, y, 420, y + 8), fill="black")
    data = io.BytesIO()
    picture.save(data, "JPEG")
    image = data.getvalue()
    now = int(time.time())
    with module.transaction(module.settings.database_path) as db:
        db.execute("INSERT INTO page_preparations VALUES(?,?,?,?,?,?,?)",
                   ("current-page", session, image_fingerprint(image), "preparing", 1, now, now + 600))
        db.execute("INSERT INTO prepared_questions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "current-question", "current-page", 0, '["1"]', "第1题", "比较本题两个部分并说明关系。",
            '[0.1,0.1,0.8,0.7]', "partial", .96, None, None, "pending"))
    service = PagePreparation(module.settings)
    service.inflight.add("current-page")
    service.question_jobs.add(("current-page", 0))
    monkeypatch.setattr(module, "page_preparation", lambda: service)
    monkeypatch.setattr(module, "_teaching_anchors", lambda _image, target, *_args: target)
    monkeypatch.setattr(module, "solve_question", lambda *_a, **_k: pytest.fail("duplicated shared solve"))
    yield client, module, child, session, family, image, service
    service.close()


def ask(fixture, *, stream=True, **data):
    client, _, child, session, _, image, _ = fixture
    return client.post("/v1/hints/stream" if stream else "/v1/hints",
                       data={"session_id": session, "question": "请完整讲解第1题的过程和结论，并检查。", **data},
                       files={"image": ("synthetic.jpg", image, "image/jpeg")}, headers=child)


def events(response):
    assert response.status_code == 200, response.text
    return [(part.split("\n", 1)[0].removeprefix("event: "),
             json.loads(part.split("data: ", 1)[1]))
            for part in response.text.split("\n\n") if part.startswith("event: ")]


def update(prepared, **changes):
    module = prepared[1]
    values = {"solution_json": json.dumps(SOLUTION), "solve_confidence": .97,
              "verification_status": "verified", **changes}
    with module.transaction(module.settings.database_path) as db:
        db.execute("UPDATE prepared_questions SET " + ",".join(f"{key}=?" for key in values)
                   + " WHERE id='current-question'", tuple(values.values()))


def shared_worker(prepared, monkeypatch, change):
    """A barrier makes the request capture pending before another worker commits."""
    service = prepared[-1]
    entered = Event()
    read = service.teaching_question

    def observed(*args, **kwargs):
        if kwargs.get("wait_seconds", 0):
            entered.set()
        return read(*args, **kwargs)

    monkeypatch.setattr(service, "teaching_question", observed)
    errors = []

    def run():
        if not entered.wait(3):
            errors.append("request never waited for the shared solve")
            return
        change()

    thread = Thread(target=run)
    thread.start()
    return thread, errors


@pytest.mark.parametrize("stream", [True, False])
def test_pending_snapshot_consumes_same_question_verified_by_shared_worker(prepared, monkeypatch, stream):
    module = prepared[1]
    received = []

    def teacher(*args):
        received.append((args[4], args[5], args[14]))
        assert args[5] == SOLUTION and args[14] is True
        yield "beat", {"speech": "对应条件后算得17，再代回逐项核对。", "page": [], "scratch": [],
                       "reply_mode": "none", "teaching_move": "give_clue"}

    def ordinary(*_args, **kwargs):
        received.append(kwargs.get("allow_final_answer"))
        return {"response": "对应条件后算得17，再代回逐项核对。", "visual_cue": None}

    monkeypatch.setattr(module, "stream_teaching_beats", teacher)
    monkeypatch.setattr(module, "analyze", ordinary)
    thread, errors = shared_worker(prepared, monkeypatch, lambda: update(prepared))
    response = ask(prepared, stream=stream)
    thread.join(3)
    assert not thread.is_alive() and not errors
    if stream:
        result = events(response)
        assert any(kind == "status" and data.get("stage") == "checking_solution" for kind, data in result)
        assert [kind for kind, _ in result].count("beat") == 1
        assert result[-1][0] == "done"
    else:
        assert response.status_code == 200 and received == [True]
    assert received
    assert module.one("SELECT COUNT(*) AS n FROM hints")["n"] == 1


@pytest.mark.parametrize("stream", [True, False])
def test_shared_pending_timeout_is_not_verified_and_does_not_launch_a_duplicate_solve(prepared, monkeypatch, stream):
    module = prepared[1]
    monkeypatch.setattr(module, "settings", replace(module.settings, ai_timeout_seconds=.01))
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: pytest.fail("pending taught as a lesson"))
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: pytest.fail("pending replaced with concept lecture"))
    response = ask(prepared, stream=stream)
    result = events(response)[-1][1] if stream else response.json()
    assert "正在核对" in result["response"] and "看不清" not in result["response"]
    assert all(not beat["scratch"] and not beat["page"] for beat in result["visual_beats"])
    assert module.one("SELECT verification_status FROM prepared_questions")["verification_status"] == "pending"


@pytest.mark.parametrize("precompute", [False, True])
def test_pending_without_a_shared_job_allows_one_current_on_demand_solve(prepared, monkeypatch, precompute):
    module, family, service = prepared[1], prepared[4], prepared[-1]
    service.inflight.clear()
    service.question_jobs.clear()
    with module.transaction(module.settings.database_path) as db:
        db.execute("INSERT INTO family_ai_preferences VALUES(?,?,?,?,?,?,?,?)",
                   (family["id"], "gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-sol", precompute, 1, 0))
    solves, received = [], []
    monkeypatch.setattr(module, "solve_question", lambda *_a: solves.append(1) or dict(SOLUTION))

    def teacher(*args):
        received.append((args[5], args[14]))
        yield "beat", {"speech": "按本题条件计算并检查得到17。", "page": [], "scratch": [],
                       "reply_mode": "none", "teaching_move": "give_clue"}

    monkeypatch.setattr(module, "stream_teaching_beats", teacher)
    result = events(ask(prepared))
    assert result[-1][0] == "done" and solves == [1]
    assert received == [(SOLUTION, True)]


def test_failed_on_demand_solve_never_becomes_a_verified_construction(prepared, monkeypatch):
    module, service = prepared[1], prepared[-1]
    service.inflight.clear()
    service.question_jobs.clear()
    calls = []

    def solve(*_args):
        calls.append(1)
        raise ValueError("synthetic solver failure")

    monkeypatch.setattr(module, "solve_question", solve)
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: pytest.fail("failed solve taught as concept lesson"))
    result = events(ask(prepared))[-1][1]
    assert "核对刚才没有完成" in result["response"] and "看不清" not in result["response"]
    assert not result["visual_beats"][0]["scratch"] and calls == [1]
    assert module.one("SELECT verification_status FROM prepared_questions")["verification_status"] == "pending"


def test_uncertain_old_high_confidence_json_cannot_become_verified_when_reading_improves(prepared, monkeypatch):
    module, service = prepared[1], prepared[-1]
    service.inflight.clear()
    service.question_jobs.clear()
    update(prepared, verification_status="uncertain")
    calls, observed = [], []
    ambiguous = {"answer": "仍需核对具体位置", "steps": "条件尚不能唯一对应", "confidence": .6, "ambiguous": True}
    monkeypatch.setattr(module, "solve_question", lambda *_a: calls.append(1) or ambiguous)

    def teacher(*args):
        observed.append((args[5], args[14]))
        yield "beat", {"speech": "请指出需要对应的那一处条件。", "page": [], "scratch": [],
                       "reply_mode": "none", "teaching_move": "clarify_target"}

    monkeypatch.setattr(module, "stream_teaching_beats", teacher)
    assert events(ask(prepared))[-1][0] == "done"
    assert calls == [1] and observed == [(ambiguous, False)]


@pytest.mark.parametrize("change", ["paused", "version", "wrong_family", "wrong_session", "wrong_photo", "expired"])
def test_solution_reader_rejects_inactive_or_different_scope(prepared, change):
    _, module, _, session, family, image, service = prepared
    update(prepared)
    family_id = family["id"]
    if change in {"paused", "version", "expired"}:
        with module.transaction(module.settings.database_path) as db:
            if change == "paused":
                db.execute("UPDATE sessions SET status='paused' WHERE id=?", (session,))
            elif change == "expired":
                db.execute("UPDATE page_preparations SET expires_at=0")
            else:
                db.execute("UPDATE page_preparations SET model_version=2")
    if change == "wrong_family":
        family_id = "another-family"
    if change == "wrong_session":
        session = "another-session"
    if change == "wrong_photo":
        picture = Image.new("RGB", (640, 960), "white")
        draw = ImageDraw.Draw(picture)
        for y in range(80, 850, 20):
            draw.line((100, y, 600, y + 75), fill="black", width=7)
        output = io.BytesIO()
        picture.save(output, "JPEG")
        image = output.getvalue()
    assert service.teaching_question(session, family_id, image, "current-question") is None


def test_session_paused_during_shared_wait_never_reaches_teacher(prepared, monkeypatch):
    module, session = prepared[1], prepared[3]

    def pause():
        with module.transaction(module.settings.database_path) as db:
            db.execute("UPDATE sessions SET status='paused' WHERE id=?", (session,))

    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: pytest.fail("inactive session taught"))
    thread, errors = shared_worker(prepared, monkeypatch, pause)
    result = events(ask(prepared))
    thread.join(3)
    assert not thread.is_alive() and not errors
    assert result[-1][0] == "error" and not any(kind == "beat" for kind, _ in result)
    assert module.one("SELECT COUNT(*) AS n FROM hints")["n"] == 0


@pytest.mark.parametrize("change", ["version", "paused"])
def test_on_demand_scope_change_cannot_confirm_spoken_answer_before_rechecking(prepared, monkeypatch, change):
    module, session, service = prepared[1], prepared[3], prepared[-1]
    service.inflight.clear()
    service.question_jobs.clear()

    def solve(*_args):
        with module.transaction(module.settings.database_path) as db:
            if change == "version":
                db.execute("UPDATE page_preparations SET model_version=2")
            else:
                db.execute("UPDATE sessions SET status='paused' WHERE id=?", (session,))
        return dict(SOLUTION)

    monkeypatch.setattr(module, "solve_question", solve)
    monkeypatch.setattr(module, "assess_spoken_answer", lambda *_a: pytest.fail("scope changed before confirmation"))
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_a: pytest.fail("stale solve consumed"))
    result = events(ask(prepared, student_thought="我的结果是17"))
    assert result[-1][0] == "error" and not any(kind == "beat" for kind, _ in result)
    assert module.one("SELECT COUNT(*) AS n FROM hints")["n"] == 0


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("change", ["version", "reading"])
def test_scope_change_during_assessor_cannot_confirm_an_answer(prepared, monkeypatch, stream, change):
    module = prepared[1]
    update(prepared)

    def assess(*_args):
        with module.transaction(module.settings.database_path) as db:
            if change == "version":
                db.execute("UPDATE page_preparations SET model_version=2")
            else:
                db.execute("UPDATE prepared_questions SET read_confidence=.6")
        return {"verdict": "correct", "response": "你说出的这个关系成立。"}

    monkeypatch.setattr(module, "assess_spoken_answer", assess)
    response = ask(prepared, stream=stream, student_thought="我的结果是17")
    assert (events(response)[-1][0] == "error") if stream else response.status_code == 409
    assert module.one("SELECT COUNT(*) AS n FROM hints")["n"] == 0


@pytest.mark.parametrize("pause_at", ["before_worker", "during_solve"])
def test_pause_worker_exit_then_resume_does_not_leave_a_ghost_shared_solve(prepared, monkeypatch, pause_at):
    _, module, _, session, family, image, service = prepared
    question = service.questions_for(session, image)[0]
    question["confidence"] = question["read_confidence"]
    calls = []

    def set_status(status):
        with module.transaction(module.settings.database_path) as db:
            db.execute("UPDATE sessions SET status=? WHERE id=?", (status, session))

    def solve(*_args):
        calls.append(1)
        set_status("paused")
        return dict(SOLUTION)

    monkeypatch.setattr(page_prep, "solve_question", solve)
    if pause_at == "before_worker":
        set_status("paused")
    service._solve_one("current-page", session, family["id"], image,
                       module.preferences(module.settings.database_path, family["id"]),
                       module.settings, question, 0, "synthetic-page-key", "synthetic-question-key")
    assert calls == ([1] if pause_at == "during_solve" else [])
    assert ("current-page", 0) not in service.question_jobs
    assert "current-page" not in service.inflight
    set_status("active")
    # Another question may genuinely still be queued on this page. It must not
    # cause a wait for this question or be removed by this question's cleanup.
    service.question_jobs.add(("current-page", 1))
    service.inflight.add("current-page")
    result = service.teaching_question(session, family["id"], image, "current-question", wait_seconds=.1)
    assert result["verification_status"] == "pending" and not result["_solution_inflight"]
    service._release_question_job(("current-page", 0))
    assert ("current-page", 1) in service.question_jobs and "current-page" in service.inflight


def test_cancelled_queued_future_releases_only_its_question_ownership(prepared, monkeypatch):
    _, module, _, session, family, image, service = prepared
    service.question_jobs.clear()
    service.inflight.clear()
    now = int(time.time())
    with module.transaction(module.settings.database_path) as db:
        db.execute("INSERT INTO page_preparations VALUES(?,?,?,?,?,?,?)",
                   ("queued-page", session, image_fingerprint(image), "preparing", 1, now, now + 600))
    items = [{"path": [str(index + 1)], "label": f"第{index + 1}题", "evidence": text,
              "box": [.1, .1 + index * .3, .8, .2], "progress": "blank", "confidence": .96}
             for index, text in enumerate(["合并两个不同部分", "比较另一组独立条件"])]
    monkeypatch.setattr(page_prep, "inventory_page", lambda *_a, **_k: items)
    monkeypatch.setattr(page_prep, "solve_question", lambda *_a: dict(SOLUTION))
    service.solvers.shutdown(wait=True)
    service.solvers = ThreadPoolExecutor(max_workers=1)
    entered, release = Event(), Event()

    def blocker():
        entered.set()
        release.wait(3)

    occupied = service.solvers.submit(blocker)
    assert entered.wait(1)
    service.inflight.add("queued-page")
    try:
        service._prepare("queued-page", session, family["id"], image,
                         module.preferences(module.settings.database_path, family["id"]))
        future = service.question_futures[("queued-page", 1)]
        assert future.cancel()  # Runs the production callback synchronously.
        assert ("queued-page", 1) not in service.question_jobs
        assert ("queued-page", 1) not in service.question_futures
        assert "queued-page" not in service.inflight and "queued-page" not in service.inventory_jobs
    finally:
        release.set()
        occupied.result(timeout=3)
