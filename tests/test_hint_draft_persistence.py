"""Hint board scenes survive reconnects without retaining camera data."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import time
from copy import deepcopy

import pytest
from PIL import Image
from test_api import make_client, pair, parent

from k12_server.db import connect, initialize

BOX = [0.2, 0.3, 0.4, 0.2]
BEATS = [
    {
        "speech": "先找两个已知量。",
        "page": [{"kind": "circle", "box": BOX}],
        "scratch": [{"kind": "text", "role": "prose", "x": 10, "y": 10, "text": "先写两个已知量"}],
        "teaching_move": "give_clue",
        "reply_mode": "none",
        "needs_parent": False,
    },
    {
        "speech": "试着用图表示它们的关系。",
        "page": [],
        "scratch": [
            {"kind": "rect", "x": 20, "y": 30, "w": 40, "h": 20},
            {"kind": "line", "x1": 20, "y1": 30, "x2": 60, "y2": 50},
            {"kind": "text", "role": "label", "x": 25, "y": 40, "text": "A"},
        ],
        "teaching_move": "wait_for_writing",
        "reply_mode": "writing",
        "needs_parent": False,
    },
]


@pytest.fixture
def api(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    parent_headers = parent(client)
    child_headers = pair(client, parent_headers)
    child_id = module.read_token(
        module.settings.app_secret, child_headers["Authorization"].removeprefix("Bearer ")
    )["sub"]
    ticket = module.make_token(module.settings.app_secret, "setup", child_id, 120)
    session = client.post(
        "/v1/sessions", json={"setup_ticket": ticket}, headers=child_headers
    ).json()["id"]
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="JPEG")
    located = {
        "found": True,
        "label": "第1题",
        "box": BOX,
        "evidence": "找两个已知量并画出关系",
        "confidence": 0.96,
        "read_confidence": 0.96,
        "verification_status": "verified",
        "solution": {
            "answer": "42",
            "steps": "先列出两个量再画图",
            "confidence": 0.96,
            "ambiguous": False,
        },
    }

    class Prepared:
        def match(self, *_args, **_kwargs):
            return deepcopy(located)

        def status(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(module, "page_preparation", lambda: Prepared())
    monkeypatch.setattr(module, "locate_question", lambda *_args, **_kwargs: deepcopy(located))
    monkeypatch.setattr(
        module,
        "stream_teaching_beats",
        lambda *_args, **_kwargs: iter(("beat", beat) for beat in deepcopy(BEATS)),
    )
    monkeypatch.setattr(
        module,
        "analyze",
        lambda *_args, **_kwargs: {
            "response": "先列出两个已知量。",
            "visual_cue": None,
            "teaching_move": "give_clue",
            "reply_mode": "writing",
            "needs_parent": False,
        },
    )
    return client, module, parent_headers, child_headers, session, output.getvalue()


def ask(api, *, stream=True, **data):
    client, _, _, child_headers, session, image = api
    response = client.post(
        "/v1/hints/stream" if stream else "/v1/hints",
        data={"session_id": session, "question": "第1题请给我一步提示", **data},
        files={"image": ("page.jpg", image, "image/jpeg")},
        headers=child_headers,
    )
    assert response.status_code == 200, response.text
    if not stream:
        return response.json()
    done = [
        json.loads(frame.split("data: ", 1)[1])
        for frame in response.text.split("\n\n")
        if frame.startswith("event: done\n")
    ]
    assert done, response.text
    return done[-1]


def saved_scene(api, hint_id):
    client, _, _, child_headers, _, _ = api
    hints = client.get("/v1/families/dashboard", headers=child_headers).json()["hints"]
    return next(item["teaching_scene"] for item in hints if item["id"] == hint_id)


def test_real_api_scene_survives_same_session_pause_and_application_reload(
    api, monkeypatch, tmp_path
):
    client, _, parent_headers, child_headers, session, _ = api
    result = ask(api)
    expected = {
        "box": result["target_box"],
        "label": result["target_label"],
        "beats": result["visual_beats"],
    }
    assert expected["beats"][1]["scratch"][1]["kind"] == "line"
    assert saved_scene(api, result["id"]) == expected
    assert (
        client.patch(
            f"/v1/sessions/{session}", json={"status": "paused"}, headers=child_headers
        ).status_code
        == 200
    )
    reloaded_client, _ = make_client(monkeypatch, tmp_path)
    for headers in (child_headers, parent_headers):
        dashboard = reloaded_client.get("/v1/families/dashboard", headers=headers).json()
        assert dashboard["session"]["id"] == session
        assert dashboard["session"]["status"] == "paused"
        restored = next(item for item in dashboard["hints"] if item["id"] == result["id"])
        assert restored["teaching_scene"] == expected
        assert "teaching_json" not in restored


@pytest.mark.parametrize("stream", [False, True])
def test_nonstream_and_stream_fallback_preserve_current_prose_board(api, monkeypatch, stream):
    module = api[1]
    monkeypatch.setattr(module, "stream_teaching_beats", lambda *_args, **_kwargs: iter(()))
    result = ask(api, stream=stream)
    scene = saved_scene(api, result["id"])
    assert scene == {
        "box": result["target_box"],
        "label": result["target_label"],
        "beats": result["visual_beats"],
    }
    assert scene["beats"][0]["scratch"]
    assert all(
        item["kind"] == "text" and item["role"] == "prose"
        for beat in scene["beats"]
        for item in beat["scratch"]
    )


def test_explicit_no_scratch_beat_does_not_invent_a_board_on_restore(api, monkeypatch):
    beat = {**deepcopy(BEATS[0]), "scratch": []}
    monkeypatch.setattr(
        api[1], "stream_teaching_beats", lambda *_args, **_kwargs: iter([("beat", beat)])
    )
    result = ask(api)
    scene = saved_scene(api, result["id"])
    assert scene["beats"] == result["visual_beats"]
    assert scene["beats"][0]["scratch"] == []


def test_all_48_short_steps_and_pacing_metadata_survive_api_readback(api, monkeypatch):
    beats = []
    for index in range(48):
        beat = deepcopy(BEATS[0])
        beat.update(
            speech=f"观察第{index + 1}处关系。",
            step_id=index + 1,
            step_title=f"第{index + 1}步",
            step_end=True,
        )
        beat["page"] = [
            {
                "kind": "line",
                "anchor_id": "edge",
                "anchor_text": "这条边",
                "x1": 0.2,
                "y1": 0.3,
                "x2": 0.3,
                "y2": 0.3,
            }
        ]
        beats.append(beat)
    monkeypatch.setattr(
        api[1],
        "stream_teaching_beats",
        lambda *_args, **_kwargs: iter(("beat", beat) for beat in deepcopy(beats)),
    )
    result = ask(api)
    scene = saved_scene(api, result["id"])
    assert len(scene["beats"]) == 48
    assert scene["beats"] == result["visual_beats"] == beats
    assert scene["beats"][-1]["step_id"] == 48
    assert scene["beats"][-1]["step_title"] == "第48步"
    assert scene["beats"][-1]["step_end"] is True
    assert scene["beats"][-1]["page"][0]["anchor_text"] == "这条边"


@pytest.mark.parametrize("stream", [False, True])
def test_confirmed_answer_board_is_persisted_in_both_success_paths(api, monkeypatch, stream):
    monkeypatch.setattr(
        api[1],
        "_answer_feedback",
        lambda *_args, **_kwargs: {"verdict": "correct", "response": "这一步的关系说对了。"},
    )
    result = ask(api, stream=stream, student_thought="我先比较两个已知量")
    assert result["answer_assessment"] == "correct"
    scene = saved_scene(api, result["id"])
    assert scene["beats"] == result["visual_beats"]
    assert scene["beats"][0]["scratch"][0]["text"] == "这一步说对了 ✓"


def test_dashboard_filters_other_families_expired_hints_and_expired_sessions(api):
    client, module, parent_headers, child_headers, session, _ = api
    result = ask(api)
    other_parent = parent(client, "other-family@example.com")
    other_child = pair(client, other_parent)
    assert client.get("/v1/families/dashboard", headers=other_parent).json()["hints"] == []
    assert client.get("/v1/families/dashboard", headers=other_child).json()["hints"] == []
    now = int(time.time())
    with connect(module.settings.database_path) as db:
        current = db.execute(
            "SELECT family_id,device_id FROM sessions WHERE id=?", (session,)
        ).fetchone()
        db.execute(
            "INSERT INTO sessions(id,family_id,device_id,status,started_at,expires_at) VALUES(?,?,?,?,?,?)",
            (
                "expired-session",
                current["family_id"],
                current["device_id"],
                "finished",
                now - 100,
                now - 1,
            ),
        )
        for identifier, session_id, expiry in (
            ("expired-hint", session, now - 1),
            ("expired-session-hint", "expired-session", now + 3600),
        ):
            db.execute(
                "INSERT INTO hints(id,session_id,question,response,source,created_at,expires_at,teaching_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    session_id,
                    "问题",
                    "提示",
                    "ai",
                    now,
                    expiry,
                    json.dumps({"box": BOX, "label": "旧题", "beats": BEATS}),
                ),
            )
    for headers in (parent_headers, child_headers):
        hints = client.get("/v1/families/dashboard", headers=headers).json()["hints"]
        assert {item["id"] for item in hints} == {result["id"]}


def test_empty_legacy_and_malformed_scenes_remain_null(api):
    client, module, _, child_headers, session, _ = api
    now = int(time.time())
    values = [None, "", "null", "{}", "[]", '{"beats":[]}', '{"beats":[{}]}', "{invalid"]
    with connect(module.settings.database_path) as db:
        for index, value in enumerate(values):
            db.execute(
                "INSERT INTO hints(id,session_id,question,response,source,created_at,expires_at,teaching_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (f"legacy-{index}", session, "旧问题", "旧提示", "ai", now, now + 3600, value),
            )
    hints = client.get("/v1/families/dashboard", headers=child_headers).json()["hints"]
    assert len(hints) == len(values)
    assert all(item["teaching_scene"] is None for item in hints)


def test_scene_whitelist_retains_shapes_without_camera_data_or_urls(api, monkeypatch):
    module = api[1]
    beat = deepcopy(BEATS[0])
    beat.update(
        image="data:image/jpeg;base64,PRIVATE-PHOTO",
        imageUrl="blob:private-camera",
        url="https://private.test",
    )
    beat["scratch"][0].update(
        image_jpeg="PRIVATE-JPEG", image_url="data:image/jpeg;base64,PRIVATE-PHOTO"
    )
    monkeypatch.setattr(
        module, "stream_teaching_beats", lambda *_args, **_kwargs: iter([("beat", beat)])
    )
    result = ask(api)
    scene = saved_scene(api, result["id"])
    assert scene["beats"][0]["scratch"][0] == BEATS[0]["scratch"][0]
    with connect(module.settings.database_path) as db:
        row = db.execute("SELECT teaching_json FROM hints WHERE id=?", (result["id"],)).fetchone()
        columns = list(db.execute("PRAGMA table_info(hints)"))
    assert all(
        value not in row["teaching_json"]
        for value in ("PRIVATE-", "data:", "blob:", "https://", '"image"', '"imageUrl"', '"image_jpeg"', '"image_url"')
    )
    persisted = json.loads(row["teaching_json"])
    assert persisted["source_image_sha256"] == hashlib.sha256(api[-1]).hexdigest()
    assert not any(column["type"] == "BLOB" for column in columns)


def test_old_hint_schema_migration_is_idempotent_and_preserves_text(tmp_path):
    path = str(tmp_path / "old-hints.sqlite3")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE hints(id TEXT PRIMARY KEY,session_id TEXT,task_id TEXT,question TEXT,"
        "response TEXT,source TEXT,created_at INTEGER,expires_at INTEGER)"
    )
    db.execute("INSERT INTO hints VALUES('legacy','session',NULL,'旧问题','旧提示','ai',1,2)")
    db.commit()
    db.close()
    initialize(path)
    initialize(path)
    with connect(path) as db:
        assert [item["name"] for item in db.execute("PRAGMA table_info(hints)")].count(
            "teaching_json"
        ) == 1
        row = dict(
            db.execute(
                "SELECT response,level,teaching_json FROM hints WHERE id='legacy'"
            ).fetchone()
        )
    assert row == {"response": "旧提示", "level": 1, "teaching_json": None}
