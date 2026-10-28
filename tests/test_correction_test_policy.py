"""Test-server correction policy changes only the parent-approval requirement."""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from PIL import Image
from test_api import make_client, pair, parent


@pytest.fixture
def family(monkeypatch, tmp_path):
    monkeypatch.delenv("CORRECTION_REQUIRE_PARENT_APPROVAL", raising=False)
    client, module = make_client(monkeypatch, tmp_path)
    guardian = parent(client)
    child = pair(client, guardian)
    device_id = module.read_token(
        module.settings.app_secret, child["Authorization"].removeprefix("Bearer ")
    )["sub"]
    ticket = module.make_token(module.settings.app_secret, "setup", device_id, 120)
    session_id = client.post(
        "/v1/sessions", json={"setup_ticket": ticket}, headers=child
    ).json()["id"]
    image = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(image, format="JPEG")
    monkeypatch.setattr(module, "correction_regions", lambda page: [("整页", (0, 0, 1, 1), page)])
    monkeypatch.setattr(module, "analyze", lambda *_a, **_k: {
        "confidence": .96,
        "checks": [{"question": "第 1 题", "status": "check_again", "answer": "7",
                    "evidence": "纸面写 7", "reason": "应写 8", "confidence": .96,
                    "box": [.2, .2, .3, .1]}],
    })
    monkeypatch.setattr(module, "recheck_single", lambda *_a, **_k: {
        "status": "looks_correct", "answer": "8", "evidence": "已经订正为 8",
        "reason": "与题意相符", "explanation": "3+5=8", "confidence": .96,
    })
    return client, module, guardian, child, session_id, image.getvalue()


def _bypass(module, monkeypatch, enabled=True):
    monkeypatch.setattr(module, "settings", replace(
        module.settings, correction_require_parent_approval=not enabled
    ))


def _submit(family, **data):
    client, _module, _guardian, child, session_id, image = family
    return client.post(f"/v1/sessions/{session_id}/corrections", data=data,
                       files={"image": ("page.jpg", image, "image/jpeg")}, headers=child)


def _session(family):
    client, _module, _guardian, child, _session_id, _image = family
    return client.get("/v1/families/dashboard", headers=child).json()["session"]


def test_parent_approval_is_required_by_default(family):
    client, module, _guardian, child, session_id, _image = family
    assert module.settings.correction_require_parent_approval is True
    assert _session(family)["correction_access"] == {
        "allowed": False, "mode": "parent_authorization", "authorized_until": None,
    }
    assert _submit(family).status_code == 403
    response = client.post(f"/v1/sessions/{session_id}/correction-request", headers=child)
    assert response.status_code == 200
    assert response.json()["allowed"] is False
    assert response.json()["mode"] == "parent_authorization"
    assert response.json()["requested_at"]


@pytest.mark.parametrize("value, expected", [
    ("true", True), ("1", True), (" YES ", True), ("on", True),
    ("false", False), ("0", False), (" NO ", False), ("off", False),
])
def test_correction_policy_accepts_explicit_boolean_settings(monkeypatch, value, expected):
    from k12_server.config import Settings

    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("CORRECTION_REQUIRE_PARENT_APPROVAL", value)
    assert Settings.from_env().correction_require_parent_approval is expected


@pytest.mark.parametrize("value", ["", "falsee", "disabled", "2"])
def test_invalid_correction_policy_does_not_silently_disable_approval(monkeypatch, value):
    from k12_server.config import Settings

    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("CORRECTION_REQUIRE_PARENT_APPROVAL", value)
    with pytest.raises(RuntimeError, match="CORRECTION_REQUIRE_PARENT_APPROVAL"):
        Settings.from_env()


def test_bypass_allows_real_routes_without_persisting_parent_grants(family, monkeypatch):
    client, module, guardian, child, session_id, image = family
    _bypass(module, monkeypatch)
    expected = {"allowed": True, "mode": "test_bypass", "authorized_until": None}
    assert _session(family)["correction_access"] == expected
    assert client.post(f"/v1/sessions/{session_id}/correction-request", headers=child).json() == expected
    result = _submit(family)
    assert result.status_code == 200, result.text
    correction = result.json()
    record = client.get("/v1/families/dashboard", headers=child).json()["corrections"][0]
    assert record["id"] == correction["id"] and record["image_available"]
    assert client.get(f"/v1/corrections/{correction['id']}/image", headers=child).content == image
    recheck = client.post(f"/v1/corrections/{correction['id']}/checks/0/recheck", headers=child)
    assert recheck.status_code == 200, recheck.text
    assert recheck.json()["check"]["status"] == "looks_correct"
    session = module.one("SELECT * FROM sessions WHERE id=?", (session_id,))
    assert session["correction_authorized_until"] is None
    assert session["correction_requested_at"] is None
    assert client.patch(f"/v1/corrections/{correction['id']}",
                        json={"review_status": "accepted"}, headers=child).status_code == 403
    assert client.patch(f"/v1/corrections/{correction['id']}/checks/0/parent-review",
                        json={"status": "looks_correct", "answer": "8", "revision": 0},
                        headers=child).status_code == 403
    assert client.patch(f"/v1/sessions/{session_id}/correction-access",
                        json={"allowed": True}, headers=child).status_code == 403
    assert client.get(f"/v1/corrections/{correction['id']}/image", headers=guardian).content == image


def test_bypass_keeps_family_task_and_device_boundaries(family, monkeypatch):
    client, module, _guardian, child, session_id, image = family
    _bypass(module, monkeypatch)
    correction_id = _submit(family).json()["id"]
    outsider = parent(client, "outside@example.com")
    outside_child = pair(client, outsider)
    task = client.post("/v1/tasks/import", json={"text": "外部家庭作业"}, headers=outsider).json()["tasks"][0]
    assert _submit(family, task_id=task["id"]).status_code == 404
    assert client.get("/v1/families/dashboard", headers=outside_child).json()["corrections"] == []
    assert client.get(f"/v1/corrections/{correction_id}/image", headers=outside_child).status_code == 404
    assert client.post(f"/v1/corrections/{correction_id}/checks/0/recheck", headers=outside_child).status_code == 404
    assert client.post(f"/v1/sessions/{session_id}/correction-request", headers=outside_child).status_code == 404
    assert client.post(f"/v1/sessions/{session_id}/corrections",
                       files={"image": ("page.jpg", image, "image/jpeg")}, headers=outside_child).status_code == 404
    another_device = client.post("/v1/devices/claim", json={}).json()["device_id"]
    with module.transaction(module.settings.database_path) as db:
        db.execute("UPDATE sessions SET device_id=? WHERE id=?", (another_device, session_id))
    assert _session(family)["correction_access"]["allowed"] is False
    assert _submit(family).status_code == 409
    assert client.post(f"/v1/sessions/{session_id}/correction-request", headers=child).status_code == 409
    assert client.post(f"/v1/corrections/{correction_id}/checks/0/recheck", headers=child).status_code == 403


def test_pause_denies_bypass_but_finished_history_remains_readable(family, monkeypatch):
    client, module, _guardian, child, session_id, image = family
    _bypass(module, monkeypatch)
    correction_id = _submit(family).json()["id"]
    assert client.patch(f"/v1/sessions/{session_id}", json={"status": "paused"}, headers=child).status_code == 200
    assert _session(family)["correction_access"]["allowed"] is False
    assert _submit(family).status_code == 409
    assert client.post(f"/v1/sessions/{session_id}/correction-request", headers=child).status_code == 409
    assert client.get("/v1/families/dashboard", headers=child).json()["corrections"] == []
    assert client.get(f"/v1/corrections/{correction_id}/image", headers=child).status_code == 403
    assert client.post(f"/v1/corrections/{correction_id}/checks/0/recheck", headers=child).status_code == 403
    assert client.post(f"/v1/sessions/{session_id}/finish", headers=child).status_code == 200
    assert _submit(family).status_code == 409
    assert client.post(f"/v1/sessions/{session_id}/correction-request", headers=child).status_code == 409
    assert client.post(f"/v1/corrections/{correction_id}/checks/0/recheck", headers=child).status_code == 403
    assert client.get(f"/v1/corrections/{correction_id}/image", headers=child).content == image
    assert client.get("/v1/families/dashboard", headers=child).json()["corrections"][0]["id"] == correction_id


def test_reenabling_approval_revokes_only_test_access_and_preserves_parent_grants(family, monkeypatch):
    client, module, guardian, child, session_id, _image = family
    _bypass(module, monkeypatch)
    correction_id = _submit(family).json()["id"]
    _bypass(module, monkeypatch, False)
    assert _session(family)["correction_access"]["allowed"] is False
    assert _submit(family).status_code == 403
    assert client.get(f"/v1/corrections/{correction_id}/image", headers=child).status_code == 403
    assert client.post(f"/v1/corrections/{correction_id}/checks/0/recheck", headers=child).status_code == 403
    assert client.get("/v1/families/dashboard", headers=child).json()["corrections"] == []
    approved = client.patch(f"/v1/sessions/{session_id}/correction-access",
                            json={"allowed": True}, headers=guardian).json()
    until = approved["authorized_until"]
    assert _session(family)["correction_access"] == {
        "allowed": True, "mode": "parent_authorization", "authorized_until": until,
    }
    _bypass(module, monkeypatch)
    assert _session(family)["correction_access"]["authorized_until"] is None
    _bypass(module, monkeypatch, False)
    assert _session(family)["correction_access"]["authorized_until"] == until
    assert _submit(family).status_code == 200
    assert client.patch(f"/v1/sessions/{session_id}/correction-access",
                        json={"allowed": False}, headers=guardian).status_code == 200
    assert _submit(family).status_code == 403


@pytest.mark.parametrize("change", ["approval_required", "paused", "finished", "device"])
def test_correction_rechecks_access_after_model_analysis(family, monkeypatch, change):
    client, module, _guardian, _child, session_id, _image = family
    _bypass(module, monkeypatch)
    analyze = module.analyze
    another_device = client.post("/v1/devices/claim", json={}).json()["device_id"]

    def interrupt(*args, **kwargs):
        result = analyze(*args, **kwargs)
        if change == "approval_required":
            _bypass(module, monkeypatch, False)
        else:
            field, value = ("device_id", another_device) if change == "device" else ("status", change)
            with module.transaction(module.settings.database_path) as db:
                db.execute(f"UPDATE sessions SET {field}=? WHERE id=?", (value, session_id))
        return result

    monkeypatch.setattr(module, "analyze", interrupt)
    assert _submit(family).status_code == 409
    assert module.one("SELECT COUNT(*) AS count FROM corrections")["count"] == 0
    assert client.get("/v1/families/dashboard", headers=family[2]).json()["corrections"] == []


@pytest.mark.parametrize("change", ["approval_required", "paused", "finished", "device"])
def test_recheck_rechecks_access_before_saving_model_feedback(family, monkeypatch, change):
    client, module, _guardian, child, session_id, _image = family
    _bypass(module, monkeypatch)
    correction_id = _submit(family).json()["id"]
    original = module.one("SELECT checks_json FROM corrections WHERE id=?", (correction_id,))["checks_json"]
    recheck = module.recheck_single
    another_device = client.post("/v1/devices/claim", json={}).json()["device_id"]

    def interrupt(*args, **kwargs):
        result = recheck(*args, **kwargs)
        if change == "approval_required":
            _bypass(module, monkeypatch, False)
        else:
            field, value = ("device_id", another_device) if change == "device" else ("status", change)
            with module.transaction(module.settings.database_path) as db:
                db.execute(f"UPDATE sessions SET {field}=? WHERE id=?", (value, session_id))
        return result

    monkeypatch.setattr(module, "recheck_single", interrupt)
    response = client.post(f"/v1/corrections/{correction_id}/checks/0/recheck", headers=child)
    assert response.status_code == 409
    assert module.one("SELECT checks_json FROM corrections WHERE id=?", (correction_id,))["checks_json"] == original
