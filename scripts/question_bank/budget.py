"""Process-safe persistent limits shared by backend, oracle and teacher calls."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY, round INTEGER, kind TEXT, trace TEXT, created REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS usage (reservation INTEGER PRIMARY KEY, model TEXT, input_tokens INTEGER, output_tokens INTEGER)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def reserve(self, round_id: int, kind: str, trace: str) -> int:
        if round_id not in {1, 2, 3} or kind not in {"task", "upstream"}:
            raise ValueError("Invalid round or reservation kind")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM reservations WHERE round=? AND kind=?", (round_id, kind)).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM reservations WHERE kind=?", (kind,)).fetchone()[0]
            cap = 300 if kind == "task" else 1500
            if count >= cap or total >= 3 * cap:
                raise BudgetExceeded(f"{kind} budget exhausted for round {round_id}")
            cursor = db.execute("INSERT INTO reservations(round,kind,trace,created) VALUES(?,?,?,?)", (round_id, kind, trace[:128], time.time()))
            return cursor.lastrowid

    def record(self, reservation: int, model: str, usage: dict | None):
        usage = usage or {}
        def count(*keys):
            return next((int(usage[k]) for k in keys if isinstance(usage.get(k), int) and usage[k] >= 0), 0)
        with self.connect() as db:
            db.execute("INSERT INTO usage VALUES(?,?,?,?) ON CONFLICT(reservation) DO UPDATE SET "
                       "model=CASE WHEN excluded.model='unknown' THEN usage.model ELSE excluded.model END, "
                       "input_tokens=MAX(usage.input_tokens,excluded.input_tokens), "
                       "output_tokens=MAX(usage.output_tokens,excluded.output_tokens)", (reservation, str(model)[:200],
                       count("prompt_tokens", "input_tokens"), count("completion_tokens", "output_tokens")))

    def summary(self):
        with self.connect() as db:
            counts = db.execute("SELECT round,kind,COUNT(*) FROM reservations GROUP BY round,kind").fetchall()
            usage = db.execute("SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COUNT(*) FROM usage").fetchone()
        return {"counts": [{"round": r, "kind": k, "count": n} for r, k, n in counts],
                "reported_input_tokens": usage[0], "reported_output_tokens": usage[1],
                "calls_with_usage_record": usage[2], "price": "not_estimated"}
