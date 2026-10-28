"""Offline contracts for the isolated grading acceptance runner."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    return importlib.import_module("grading_flow")


@pytest.mark.parametrize("url", ["http://127.0.0.1:8003", "https://example.com", "http://127.0.0.1:8017/path"])
def test_protected_or_nonlocal_api_is_rejected(runner, url):
    with pytest.raises(ValueError):
        runner.safe_base_url(url)


def test_prepare_preserves_camera_bytes_and_labels_simulation(runner, tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (100, 100), "white").save(source)
    original = source.read_bytes()
    manifest = runner.prepare(tmp_path, source)
    assert source.read_bytes() == original
    assert manifest["source_has_student_answer"] is False
    assert manifest["mode"] == "saved_camera_and_code_native_simulation"
    assert [row["written"] for row in manifest["rows"]] == ["15", "5", ""]
    assert Image.open(manifest["simulated_sheet"]).size == (1200, 900)


def test_blank_policy_is_disclosed_without_inventing_wrong_calculation(runner):
    findings = runner.score_blank({"checks": [{"answer_state": "blank", "answer": "", "status": "check_again"}]})
    assert all(item["status"] == "passed" for item in findings)
    assert "not evidence" in findings[1]["detail"]
    invented = runner.score_blank({"checks": [{"answer_state": "written", "answer": "36", "status": "looks_correct"}]})
    assert invented[0]["status"] == "failed"
    assert runner.score_blank({"checks": []})[0]["status"] == "needs_review"


def test_scoring_uses_independent_truth_and_abstains_on_missing_rows(runner):
    rows = [{"id": 1, "y": 235, "written": "15", "reference": "15", "expected_status": "looks_correct"}]
    wrong = runner.score_sheet({"checks": [{"question": "第1题", "status": "looks_correct", "answer": "15",
                                               "reference_answer": "16"}]}, rows)
    assert wrong[0]["status"] == "failed"
    assert runner.score_sheet({"checks": []}, rows)[0]["status"] == "needs_review"


def test_unowned_api_gets_no_credentials(runner, tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (10, 10), "white").save(source)
    manifest = runner.prepare(tmp_path, source)
    state = {"base_url": "http://127.0.0.1:8017", "parent_token": "PRIVATE_PARENT",
             "child_token": "PRIVATE_CHILD", "session_id": "session"}
    flow = runner.Flow(tmp_path, state, 1)
    flow.client.close()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"}, headers={"X-K12-Acceptance-Run": "other-run"})

    flow.client = httpx.Client(base_url=state["base_url"], transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="ownership"):
        flow.execute(manifest)
    assert [request.url.path for request in requests] == ["/health"]
    assert "authorization" not in requests[0].headers
    flow.client.close()


def test_full_simulated_permission_review_pause_flow_is_redacted(runner, tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (100, 100), "white").save(source)
    manifest = runner.prepare(tmp_path, source)
    state = {"base_url": "http://127.0.0.1:8017", "parent_token": "PRIVATE_PARENT",
             "child_token": "PRIVATE_CHILD", "session_id": "session"}
    flow = runner.Flow(tmp_path, state, 1)
    flow.client.close()
    model_calls = []
    current = {"allowed": False, "paused": False, "posted": 0, "reviewed": False, "finished": False}
    check = {"question": "第2题", "status": "check_again", "answer": "5", "parent_revision": 0,
             "box": [.49, .50, .24, .10]}

    def handler(request):
        path, method = request.url.path, request.method
        is_parent = request.headers.get("authorization") == "Bearer PRIVATE_PARENT"
        if path == "/health":
            return httpx.Response(200, headers={"X-K12-Acceptance-Run": tmp_path.name}, json={})
        if path.endswith("/correction-request"):
            return httpx.Response(200, json={"allowed": False})
        if path.endswith("/correction-access"):
            if not is_parent:
                return httpx.Response(403, json={})
            current["allowed"] = json.loads(request.content)["allowed"]
            return httpx.Response(200, json={"allowed": current["allowed"]})
        if path == "/v1/families/dashboard":
            corrections = [{"id": "written", "review_status": "accepted", "checks": [check]}]
            return httpx.Response(200, json={
                "session": {"status": "paused" if current["paused"] else "active", "correction_requested_at": 123,
                            "correction_authorized_until": 456 if current["allowed"] else None},
                "corrections": corrections if is_parent or current["allowed"] else [],
            })
        if path.endswith("/corrections") and method == "POST":
            if current["paused"]:
                return httpx.Response(409, json={})
            if not current["allowed"]:
                return httpx.Response(403, json={})
            current["posted"] += 1
            model_calls.append("grading")
            if current["posted"] == 1:
                return httpx.Response(200, json={"id": "blank", "checks": [
                    {"status": "check_again", "answer_state": "blank", "answer": ""}]})
            return httpx.Response(200, json={"id": "written", "checks": [
                {"question": "第1题", "status": "looks_correct", "answer": "15", "reference_answer": "15"},
                check,
                {"question": "第3题", "status": "check_again", "answer_state": "blank", "answer": ""},
            ]})
        if path.endswith("/recheck"):
            model_calls.append("recheck")
            check["original_review"] = {"status": "check_again"}
            return httpx.Response(200, json={"check": check})
        if path.endswith("/parent-review"):
            if not is_parent:
                return httpx.Response(403, json={})
            if current["reviewed"]:
                return httpx.Response(409, json={})
            current["reviewed"] = True
            check.update(parent_review_history=[{}], ai_review={"status": "check_again"})
            return httpx.Response(200, json={"check": check})
        if path.endswith("/image"):
            return httpx.Response(403, json={})
        if path == "/v1/sessions/session" and method == "PATCH":
            body = json.loads(request.content)
            if body["status"] == "active" and not body.get("setup_ticket"):
                return httpx.Response(403, json={})
            current["paused"] = body["status"] == "paused"
            current["allowed"] = False
            return httpx.Response(200, json={})
        if path == "/v1/setup/check":
            model_calls.append("setup")
            return httpx.Response(200, json={"ready": True, "setup_ticket": "SECRET_SETUP"})
        if path.endswith("/finish"):
            current["finished"] = True
        return httpx.Response(200, json={})

    flow.client = httpx.Client(base_url=state["base_url"], transport=httpx.MockTransport(handler))
    result = flow.execute(manifest)
    assert all(item["status"] == "passed" for item in result["stages"] + result["findings"])
    assert current["finished"] is True
    assert model_calls == ["grading", "grading", "recheck", "setup"]
    events = (tmp_path / "grading" / "events.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE_" not in events and "SECRET_SETUP" not in events
    assert "[REDACTED]" in events
    flow.client.close()
