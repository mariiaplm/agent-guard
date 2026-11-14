"""SQLite storage: `runs` (one row per run) and `tool_calls` (one row per tool call). 
CREATES SCHEMA FOR RUNS, TOOL_CALLS, RUN STATS"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.environ.get("AGENTGUARD_DB", "agentguard.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL,
    attack_id         TEXT,
    guard_enabled     INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'running',   -- running | finished
    attack_succeeded  INTEGER NOT NULL DEFAULT 0,
    task_succeeded    INTEGER NOT NULL DEFAULT 0,
    compromise_reason TEXT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    step         INTEGER NOT NULL,
    tool_name    TEXT NOT NULL,
    args         TEXT NOT NULL,          -- JSON
    provenance   TEXT NOT NULL,
    source_text  TEXT,
    decision     TEXT NOT NULL,          -- allow | block
    reason       TEXT NOT NULL,
    score        REAL NOT NULL,
    executed     INTEGER NOT NULL,
    result       TEXT,
    timestamp    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    """New connection per request: safe with FastAPI's thread pool."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")  # lets many runs write at once
        conn.executescript(SCHEMA)


# writes
def insert_run(run_id: str, task_id: str, attack_id: Optional[str], guard_enabled: bool) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, task_id, attack_id, guard_enabled, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, task_id, attack_id, int(guard_enabled), now()),
        )


def finish_run(run_id: str, attack_succeeded: bool, task_succeeded: bool,
               compromise_reason: Optional[str]) -> str:
    ts = now()
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET status='finished', attack_succeeded=?, task_succeeded=?, "
            "compromise_reason=?, finished_at=? WHERE run_id=?",
            (int(attack_succeeded), int(task_succeeded), compromise_reason, ts, run_id),
        )
    return ts


def insert_tool_call(log: dict) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tool_calls (run_id, step, tool_name, args, provenance, source_text, "
            "decision, reason, score, executed, result, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                log["run_id"], log["step"], log["tool_name"], json.dumps(log["args"]),
                log["provenance"], log.get("source_text"), log["decision"], log["reason"],
                log["score"], int(log["executed"]), log.get("result"), log["timestamp"],
            ),
        )
        return cur.lastrowid


# reads
def get_run(run_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return _run_row(row) if row else None


def list_runs(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_run_row(r) for r in rows]


def list_tool_calls(run_id: Optional[str] = None, since_id: int = 0, limit: int = 200) -> list[dict]:
    """since_id lets the dashboard poll for only new rows."""
    q, params = "SELECT * FROM tool_calls WHERE id > ?", [since_id]
    if run_id:
        q += " AND run_id = ?"
        params.append(run_id)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["args"] = json.loads(d["args"])
        d["executed"] = bool(d["executed"])
        out.append(d)
    return out


def stats() -> dict:
    """Headline numbers, split by guard off / on. Only finished runs count."""
    q = """
    SELECT
      guard_enabled,
      COUNT(*)                                                         AS runs,
      SUM(CASE WHEN attack_id IS NOT NULL THEN 1 ELSE 0 END)           AS attack_runs,
      SUM(CASE WHEN attack_id IS NOT NULL AND attack_succeeded=1 THEN 1 ELSE 0 END) AS attacks_succeeded,
      SUM(task_succeeded)                                              AS tasks_succeeded,
      SUM(CASE WHEN attack_id IS NULL THEN 1 ELSE 0 END)               AS benign_runs,
      SUM(CASE WHEN attack_id IS NULL AND task_succeeded=1 THEN 1 ELSE 0 END) AS benign_tasks_succeeded
    FROM runs WHERE status='finished'
    GROUP BY guard_enabled
    """
    with connect() as conn:
        rows = conn.execute(q).fetchall()
        blocked = dict(conn.execute(
            "SELECT r.guard_enabled, COUNT(*) FROM tool_calls t JOIN runs r USING(run_id) "
            "WHERE t.decision='block' GROUP BY r.guard_enabled"
        ).fetchall())

    def rate(n, d):
        return round(n / d, 4) if d else None

    out = {}
    for r in rows:
        r = dict(r)
        key = "guard_on" if r["guard_enabled"] else "guard_off"
        out[key] = {
            "runs": r["runs"],
            "attack_runs": r["attack_runs"],
            "attack_success_rate": rate(r["attacks_succeeded"], r["attack_runs"]),
            "task_success_rate": rate(r["tasks_succeeded"], r["runs"]),
            "benign_runs": r["benign_runs"],
            "benign_task_success_rate": rate(r["benign_tasks_succeeded"], r["benign_runs"]),
            "calls_blocked": blocked.get(r["guard_enabled"], 0),
        }
    return out


def _run_row(row) -> dict:
    d = dict(row)
    for k in ("guard_enabled", "attack_succeeded", "task_succeeded"):
        d[k] = bool(d[k])
    return d