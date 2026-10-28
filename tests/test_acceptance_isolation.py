"""Live acceptance must prove run ownership before sending synthetic credentials."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def modules(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts" / "acceptance"))
    monkeypatch.syspath_prepend(str(root))
    return (
        importlib.import_module("evaluate"),
        importlib.import_module("bootstrap"),
        importlib.import_module("scripts.acceptance.serve"),
        importlib.import_module("scripts.acceptance.run"),
    )


def _transport(monkeypatch, module, handler):
    original = httpx.Client
    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize("identity", [None, "different-run"])
def test_evaluator_rejects_unowned_local_api_before_any_token(
    modules, monkeypatch, tmp_path, identity
):
    evaluator, _, _, _ = modules
    evaluator.prepare(tmp_path)
    monkeypatch.setenv("ACCEPTANCE_CHILD_TOKEN", "DO_NOT_SEND_CHILD")
    monkeypatch.setenv("ACCEPTANCE_PARENT_TOKEN", "DO_NOT_SEND_PARENT")
    monkeypatch.setenv("ACCEPTANCE_SESSION_ID", "synthetic-session")
    requests = []

    def handle(request):
        requests.append(request)
        headers = {"X-K12-Acceptance-Run": identity} if identity else {}
        return httpx.Response(200, json={"status": "ok"}, headers=headers)

    _transport(monkeypatch, evaluator, handle)
    with pytest.raises(ValueError, match="ownership"):
        evaluator.run(tmp_path, "http://127.0.0.1:8016", ["fraction_concept"], 1, 10)
    assert [request.url.path for request in requests] == ["/health"]
    assert all("authorization" not in request.headers for request in requests)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "setup_failed" and summary["completed_at"]
    assert "DO_NOT_SEND" not in json.dumps(summary)


def test_bootstrap_rejects_header_mismatch_before_registration(modules, monkeypatch, tmp_path):
    _, bootstrap, _, _ = modules
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200, json={"status": "ok"}, headers={"X-K12-Acceptance-Run": "another-run"}
        )

    _transport(monkeypatch, bootstrap, handle)
    with pytest.raises(ValueError, match="ownership"):
        bootstrap.bootstrap(
            "http://127.0.0.1:8016", "http://127.0.0.1:5186", tmp_path, tmp_path / "paper.jpg"
        )
    assert [request.url.path for request in requests] == ["/health"]


@pytest.mark.parametrize(
    ("same_run", "session_status", "expected_dashboard"),
    [
        (False, "active", False),
        (True, "paused", True),
        (True, "finished", True),
    ],
)
def test_bootstrap_does_not_reuse_other_run_or_inactive_session(
    modules, monkeypatch, tmp_path, same_run, session_status, expected_dashboard
):
    _, bootstrap, _, _ = modules
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "run_id": tmp_path.name if same_run else "other-run",
                "base_url": "http://127.0.0.1:8016",
                "child_token": "old-token",
                "session_id": "session",
            }
        ),
        encoding="utf-8",
    )
    requests = []

    def handle(request):
        requests.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(
                200, json={"status": "ok"}, headers={"X-K12-Acceptance-Run": tmp_path.name}
            )
        if request.url.path == "/v1/families/dashboard":
            return httpx.Response(
                200, json={"session": {"id": "session", "status": session_status}}
            )
        return httpx.Response(
            400, json={"detail": "stop before synthetic registration in this test"}
        )

    _transport(monkeypatch, bootstrap, handle)
    with pytest.raises(httpx.HTTPStatusError):
        bootstrap.bootstrap(
            "http://127.0.0.1:8016", "http://127.0.0.1:5186", tmp_path, tmp_path / "paper.jpg"
        )
    assert ("/v1/families/dashboard" in requests) == expected_dashboard
    assert requests[-1] == "/v1/auth/register"


def test_isolated_database_rejects_a_link_to_external_file(modules, tmp_path):
    _, _, serve, _ = modules
    directory = tmp_path / "run"
    directory.mkdir()
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"synthetic placeholder")
    try:
        (directory / "api.sqlite3").symlink_to(outside)
    except OSError:
        pytest.skip("File symlinks are unavailable on this Windows account.")
    with pytest.raises(ValueError, match="inside"):
        serve.database_for_run(directory)


def test_serve_rejects_protected_port_before_creating_files(modules, monkeypatch, tmp_path):
    _, _, serve, _ = modules
    target = tmp_path / "must-not-be-created"
    monkeypatch.setattr(sys, "argv", ["serve.py", "--output", str(target), "--port", "8003"])
    with pytest.raises(SystemExit) as error:
        serve.main()
    assert error.value.code == 2 and not target.exists()


def test_orchestrator_limit_matches_six_case_dataset(modules, monkeypatch, tmp_path):
    _, _, _, runner = modules
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    target = tmp_path / "output" / "acceptance" / "must-not-be-created"
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(target), "--limit", "7"])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2 and not target.exists()


def test_unowned_api_failure_is_a_failed_gate_not_a_missing_round(modules, tmp_path):
    _, _, _, runner = modules
    folder = tmp_path / "model"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
    (folder / "summary.json").write_text(
        json.dumps({"status": "setup_failed", "setup_error": "ValueError"}), encoding="utf-8"
    )
    audit = runner.collect(tmp_path)
    gate = audit["rounds"][0]["checks"][0]
    assert gate["id"] == "acceptance_setup" and gate["status"] == "failed"
    assert gate["source"]["model"] == "none"
