"""Only parents can promote an AI observation to a reviewed record."""

from __future__ import annotations

from test_api import make_client, pair, parent

from k12_server.db import connect


def test_child_cannot_confirm_observation_used_in_learning_feedback(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    p = parent(client)
    c = pair(client, p)
    other = parent(client, "another-parent@example.com")
    child_id = module.read_token(
        module.settings.app_secret, c["Authorization"].removeprefix("Bearer ")
    )["sub"]
    proof = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    sid = client.post("/v1/sessions", json={"setup_ticket": proof}, headers=c).json()["id"]
    now = module.current()
    with connect(module.settings.database_path) as db:
        db.execute(
            "INSERT INTO observations(id,session_id,kind,label,review_status,created_at,expires_at) "
            "VALUES(?,?,'writing','纸面出现新步骤','unreviewed',?,?)",
            ("observed-step", sid, now, now + 86400),
        )
    url = "/v1/observations/observed-step"
    assert client.patch(url, json={"review_status": "accepted"}, headers=c).status_code == 403
    assert client.patch(url, json={"review_status": "accepted"}, headers=other).status_code == 404
    with connect(module.settings.database_path) as db:
        assert db.execute("SELECT review_status FROM observations WHERE id='observed-step'").fetchone()[0] == "unreviewed"
    assert client.patch(url, json={"review_status": "accepted"}, headers=p).status_code == 200
