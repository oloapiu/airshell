"""Flask API for AirShell.

Serves the REST endpoints and static dashboard. Designed to run in a
background thread started by daemon.py.

All endpoints are served over Tailscale only (bound to TAILSCALE_IP env var,
defaulting to 0.0.0.0 for local development).
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, send_from_directory

from airshell.config import Config
from airshell.db import Database

log = logging.getLogger(__name__)

# Shared references — set by create_app()
_config: Config = None
_db: Database = None
_daemon_state: dict = None

app = Flask(__name__, static_folder=None)


def create_app(config: Config, db: Database, daemon_state: dict) -> Flask:
    """Initialize the Flask app with shared config, DB, and daemon state.

    daemon_state is a dict maintained by the main loop with keys:
        - boot_time: ISO 8601 string
        - last_reading_ts: ISO 8601 string or None
        - sensor_ok: bool
        - alarm_states: dict of alarm_name -> bool
    """
    global _config, _db, _daemon_state
    _config = config
    _db = db
    _daemon_state = daemon_state
    return app


# -- Config endpoints --------------------------------------------------------

@app.route("/config", methods=["POST"])
def post_config():
    """Accept JSON config from the agent, persist to disk."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    _config.update(data)
    log.info("Config updated via API")
    return jsonify({"status": "ok"})


@app.route("/config", methods=["GET"])
def get_config():
    """Return the current config."""
    return jsonify(_config.data)


# -- Readings endpoints ------------------------------------------------------

@app.route("/readings", methods=["GET"])
def get_readings():
    """Historical readings with optional filtering.

    Query params: from, to (ISO 8601), measurand, limit (int).
    """
    from_ts = request.args.get("from")
    to_ts = request.args.get("to")
    measurand = request.args.get("measurand")
    limit = request.args.get("limit", type=int)

    try:
        rows = _db.get_readings(from_ts=from_ts, to_ts=to_ts,
                                measurand=measurand, limit=limit)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(rows)


@app.route("/api/readings", methods=["GET"])
def get_api_readings():
    """Dashboard-optimized readings endpoint.

    Query params:
        last: duration string like "2h", "30m", "1d" (default: 2h)
        from, to: ISO 8601 (override 'last')
        smooth: int minutes for server-side smoothing (optional)

    Returns arrays keyed by measurand, ready for uPlot:
        { "ts": [...], "co2": [...], "pm25": [...], ... }
    """
    from_ts = request.args.get("from")
    to_ts = request.args.get("to")

    if not from_ts:
        last = request.args.get("last", "2h")
        minutes = _parse_duration(last)
        from_ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

    rows = _db.get_readings(from_ts=from_ts, to_ts=to_ts)

    smooth = request.args.get("smooth", type=int)
    if smooth and smooth > 1 and rows:
        rows = _smooth_readings(rows, smooth)

    # Pivot to column-oriented arrays for uPlot
    result = {
        "ts": [], "co2": [], "pm1": [], "pm25": [],
        "pm4": [], "pm10": [], "temp": [], "humidity": [],
    }
    for r in rows:
        for key in result:
            result[key].append(r.get(key))

    # Convert ISO timestamps to unix seconds for uPlot
    result["ts"] = [_iso_to_unix(t) for t in result["ts"]]

    # Include alarm config so dashboard can draw threshold lines
    alarm_cfg = _config.get("alarms", {})
    alarm_states = _daemon_state.get("alarm_states", {}) if _daemon_state else {}

    return jsonify({
        "data": result,
        "alarms": alarm_cfg,
        "alarm_states": alarm_states,
    })


# -- Status endpoint ---------------------------------------------------------

@app.route("/status", methods=["GET"])
def get_status():
    """Return daemon status: uptime, last reading, sensor health, config."""
    boot_time = _daemon_state.get("boot_time", "") if _daemon_state else ""
    uptime_s = 0
    if boot_time:
        boot_dt = datetime.fromisoformat(boot_time)
        uptime_s = int((datetime.now(timezone.utc) - boot_dt).total_seconds())

    return jsonify({
        "uptime_s": uptime_s,
        "boot_time": boot_time,
        "last_reading_ts": _daemon_state.get("last_reading_ts") if _daemon_state else None,
        "sensor_ok": _daemon_state.get("sensor_ok", False) if _daemon_state else False,
        "awaiting_setup": _config.awaiting_setup if _config else True,
        "alarm_states": _daemon_state.get("alarm_states", {}) if _daemon_state else {},
    })


# -- Dashboard ---------------------------------------------------------------

@app.route("/", methods=["GET"])
def dashboard():
    """Serve the static dashboard HTML."""
    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    return send_from_directory(static_dir, "dashboard.html")


# -- Helpers -----------------------------------------------------------------

def _iso_to_unix(ts: str) -> int | None:
    """Convert an ISO 8601 timestamp to a unix timestamp (seconds)."""
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _parse_duration(s: str) -> int:
    """Parse a duration string like '2h', '30m', '1d' into minutes."""
    match = re.match(r"^(\d+)([mhd])$", s.strip().lower())
    if not match:
        return 120  # default 2h

    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return value
    elif unit == "h":
        return value * 60
    elif unit == "d":
        return value * 1440
    return 120


def _smooth_readings(rows: list[dict], window: int) -> list[dict]:
    """Apply a simple moving average over 'window' readings.

    Returns a new list with smoothed numeric values. Non-numeric fields (ts)
    are passed through.
    """
    numeric_keys = ["co2", "pm1", "pm25", "pm4", "pm10", "temp", "humidity"]
    result = []

    for i in range(len(rows)):
        start = max(0, i - window + 1)
        window_rows = rows[start:i + 1]
        smoothed = {"ts": rows[i]["ts"]}
        for key in numeric_keys:
            values = [r[key] for r in window_rows if r.get(key) is not None]
            smoothed[key] = sum(values) / len(values) if values else None
        result.append(smoothed)

    return result
