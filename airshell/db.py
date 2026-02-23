"""SQLite storage for AirShell.

Single-file database (airshell.db) storing 1-minute aggregated readings and
alarm event logs. Thread-safe — sqlite3 connections are created per-thread.

Retention: rows older than 30 days are pruned on startup and once daily.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = "airshell.db"
RETENTION_DAYS = 30


class Database:
    """Thread-safe SQLite wrapper for readings and alarm events.

    Each thread gets its own sqlite3 connection via thread-local storage.
    All timestamps are ISO 8601 strings in UTC.
    """

    def __init__(self, path: str = DEFAULT_DB_PATH):
        self._path = path
        self._local = threading.local()
        self._init_tables()
        self.prune_old_data()

    def _conn(self) -> sqlite3.Connection:
        """Return a per-thread connection with row_factory set to dict."""
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self._path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def _init_tables(self):
        """Create tables if they don't exist."""
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS readings (
                ts       TEXT NOT NULL,
                co2      REAL,
                pm1      REAL,
                pm25     REAL,
                pm4      REAL,
                pm10     REAL,
                temp     REAL,
                humidity REAL
            );
            CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

            CREATE TABLE IF NOT EXISTS alarm_events (
                ts              TEXT NOT NULL,
                alarm           TEXT NOT NULL,
                event           TEXT NOT NULL,
                value_raw       REAL,
                value_smoothed  REAL,
                message         TEXT,
                webhook_status  INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_alarm_events_ts ON alarm_events(ts);
        """)
        conn.commit()
        log.info("Database initialized at %s", self._path)

    def insert_reading(self, ts: str, co2: float, pm1: float, pm25: float,
                       pm4: float, pm10: float, temp: float, humidity: float):
        """Insert a 1-minute aggregated reading."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO readings (ts, co2, pm1, pm25, pm4, pm10, temp, humidity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, co2, pm1, pm25, pm4, pm10, temp, humidity),
        )
        conn.commit()

    def get_readings(self, from_ts: str = None, to_ts: str = None,
                     measurand: str = None, limit: int = None) -> list[dict]:
        """Query readings with optional time range, measurand filter, and limit.

        Returns a list of dicts. If measurand is specified, only ts + that
        column are returned; otherwise all columns.
        """
        if measurand:
            # Validate column name to prevent injection
            valid = {"co2", "pm1", "pm25", "pm4", "pm10", "temp", "humidity"}
            if measurand not in valid:
                raise ValueError(f"Invalid measurand: {measurand}")
            cols = f"ts, {measurand}"
        else:
            cols = "*"

        query = f"SELECT {cols} FROM readings"
        params = []
        clauses = []

        if from_ts:
            clauses.append("ts >= ?")
            params.append(from_ts)
        if to_ts:
            clauses.append("ts <= ?")
            params.append(to_ts)

        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        query += " ORDER BY ts ASC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        conn = self._conn()
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_recent_readings(self, minutes: int) -> list[dict]:
        """Get readings from the last N minutes (for smoothing calculations)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM readings WHERE ts >= ? ORDER BY ts ASC", (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_alarm_event(self, ts: str, alarm: str, event: str,
                           value_raw: float, value_smoothed: float,
                           message: str, webhook_status: int):
        """Log an alarm state change or repeat."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO alarm_events "
            "(ts, alarm, event, value_raw, value_smoothed, message, webhook_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, alarm, event, value_raw, value_smoothed, message, webhook_status),
        )
        conn.commit()

    def prune_old_data(self):
        """Delete rows older than RETENTION_DAYS."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        conn = self._conn()
        deleted_readings = conn.execute(
            "DELETE FROM readings WHERE ts < ?", (cutoff,)
        ).rowcount
        deleted_events = conn.execute(
            "DELETE FROM alarm_events WHERE ts < ?", (cutoff,)
        ).rowcount
        conn.commit()
        if deleted_readings or deleted_events:
            log.info("Pruned %d readings and %d alarm events older than %d days",
                     deleted_readings, deleted_events, RETENTION_DAYS)
