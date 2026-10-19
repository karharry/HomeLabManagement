"""Persisted CPU / memory / network samples (one per machine per minute, kept 7 days) in a local
SQLite file beside the exe, so the History page survives restarts."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

from core import app_dir

DB_PATH = os.path.join(app_dir(), "history.db")
SAMPLE_EVERY = 60
KEEP_SECONDS = 7 * 86400

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_last: dict[str, float] = {}


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("CREATE TABLE IF NOT EXISTS samples (machine TEXT, ts REAL, cpu REAL, mem REAL, net REAL)")
        _conn.execute("CREATE INDEX IF NOT EXISTS samples_idx ON samples (machine, ts)")
        _conn.execute("DELETE FROM samples WHERE ts < ?", (time.time() - KEEP_SECONDS,))
        _conn.commit()
    return _conn


def record(session, now: float | None = None):
    now = now or time.time()
    if now - _last.get(session.id, 0) < SAMPLE_EVERY:
        return
    _last[session.id] = now
    mem = session.mem_used_fraction if session.mem_total > 0 else None
    try:
        with _lock:
            _db().execute("INSERT INTO samples VALUES (?,?,?,?,?)",
                          (session.id, now, session.cpu_percent, mem, session.net_total_rate))
            _db().commit()
    except sqlite3.Error:
        logging.exception("Couldn't record history sample")


def series(machine_id: str, seconds: int, buckets: int = 240, now: float | None = None) -> dict[str, list[float]]:
    """Averaged per time bucket, oldest first. Buckets with no samples are skipped, not zero-filled
    (shortcut: gaps while the machine was offline are not drawn, upgrade if a gap marker is wanted)."""
    now = now or time.time()
    since = now - seconds
    width = seconds / buckets
    with _lock:
        rows = _db().execute(
            "SELECT CAST((ts - ?) / ? AS INTEGER) b, AVG(cpu), AVG(mem), AVG(net) FROM samples "
            "WHERE machine = ? AND ts >= ? GROUP BY b ORDER BY b", (since, width, machine_id, since)).fetchall()
    return {"cpu": [r[1] for r in rows if r[1] is not None],
            "mem": [r[2] for r in rows if r[2] is not None],
            "net": [r[3] for r in rows if r[3] is not None]}
