"""Timer accounting at the day boundary."""

from k12_server.db import connect, initialize
from k12_server.timer import daily_summary, suggestion


def test_daily_timer_splits_cross_midnight_and_labels_legacy(tmp_path):
    path = str(tmp_path / "timer.sqlite3")
    initialize(path)
    with connect(path) as db:
        db.execute("INSERT INTO parents VALUES('p','p@example.com','hash',0)")
        db.execute("INSERT INTO families(id,parent_id,child_name,age_band,grade,textbook,created_at) VALUES('f','p','孩子','primary',NULL,NULL,0)")
        db.execute("INSERT INTO devices(id,secret_hash,family_id,created_at) "
                   "VALUES('d','hash','f',0)")
        db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,ended_at,"
                   "expires_at,timer_task_counts) VALUES('s','f','d','finished',86390,86410,"
                   "900000,'{}')")
        db.execute("INSERT INTO timer_events VALUES('e','s',1,'focus','自然',20000,86410,"
                   "86410,900000)")
        db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,ended_at,"
                   "expires_at) VALUES('old','f','d','finished',86300,86330,900000)")
    first = daily_summary(path, "f", 0, 86400)
    second = daily_summary(path, "f", 86400, 172800)
    assert first["duration_seconds"] == second["duration_seconds"] == 10
    assert first["by_subject"] == [{"subject": "自然", "seconds": 10}]
    assert first["legacy_duration_seconds"] == 30
    assert second["legacy_duration_seconds"] == 0


def test_goal_suggestion_uses_three_distinct_recent_days_and_caps_history(tmp_path):
    path = str(tmp_path / "goals.sqlite3")
    initialize(path)
    now = 10 * 86400
    with connect(path) as db:
        db.execute("INSERT INTO parents VALUES('p','p@example.com','hash',0)")
        db.execute("INSERT INTO families(id,parent_id,child_name,age_band,grade,textbook,created_at) VALUES('f','p','孩子','primary',NULL,NULL,0)")
        db.execute("INSERT INTO devices(id,secret_hash,family_id,created_at) "
                   "VALUES('d','hash','f',0)")
        for index, minutes in enumerate((30, 35, 40), start=1):
            end = now - index * 86400
            sid = f"s{index}"
            db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,"
                       "ended_at,expires_at,timer_task_counts) VALUES(?,?,?,'finished',?,?,?,?)",
                       (sid, "f", "d", end - minutes * 60, end, now + 86400,
                        '{"地理":1}'))
            db.execute("INSERT INTO timer_events VALUES(?,?,?,?,?,?,?,?,?)",
                       (f"e{index}", sid, 1, "focus", "地理", minutes * 60000,
                        end, end, now + 86400))
    goal = suggestion(path, {"id": "f", "age_band": "primary"},
                      [{"subject": "地理", "status": "todo"}], now)
    assert goal["source"] == "history"
    assert goal["goal_seconds"] == 30 * 60
