#!/usr/bin/env python3
"""AirShell v1 daemon — main entry point.

Runs on the Pi. Reads the SEN63C sensor every ~2 seconds, aggregates into
1-minute averages, stores to SQLite, evaluates alarm thresholds, and fires
webhooks to the agent gateway. Serves the Flask API in a background thread.

Boot behavior:
  - First boot (no config): sample with defaults, POST "awaiting setup" with
    retry backoff (5m → 15m → 1hr → daily).
  - Subsequent boots (config on disk): load config, sample + evaluate alarms
    immediately, POST "rebooted, config loaded".

Graceful shutdown on SIGTERM/SIGINT.
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

# Ensure the repo root is on sys.path so `airshell.*` imports work when
# running directly (python3 daemon.py) rather than as a package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from airshell.alarms import AlarmEvaluator
from airshell.api import create_app
from airshell.config import Config
from airshell.db import Database
from airshell.sensor import SEN63CSensor
from airshell.webhook import send_webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("airshell")

# Flask bind address — use Tailscale IP from env, or 0.0.0.0 for dev
FLASK_HOST = os.environ.get("TAILSCALE_IP", "0.0.0.0")
FLASK_PORT = int(os.environ.get("AIRSHELL_PORT", "5000"))

# Sampling
SAMPLE_INTERVAL_S = 2       # Read sensor every ~2s
AGGREGATE_INTERVAL_S = 60   # Aggregate to 1-min averages
PRUNE_INTERVAL_S = 86400    # Prune old data once daily

# Boot webhook retry backoff (seconds): 5m, 15m, 1hr, daily
_BOOT_BACKOFF = [300, 900, 3600, 86400]

# Shutdown flag
_shutdown = threading.Event()


def _signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    log.info("Received signal %d — shutting down", signum)
    _shutdown.set()


def _build_device_url() -> str:
    """Construct the device URL from Tailscale IP or hostname."""
    host = os.environ.get("TAILSCALE_IP", "")
    if not host:
        # Try to read from hostname as fallback
        import socket
        host = socket.getfqdn()
    return f"http://{host}:{FLASK_PORT}"


def _send_boot_webhook(config: Config, message: str):
    """Send a boot notification webhook, with retry backoff for first boot."""
    url = config.get("gateway", {}).get("webhook_url", "")
    token = config.get("gateway", {}).get("token", "")

    if not url:
        log.info("No webhook URL configured — skipping boot webhook")
        if config.awaiting_setup:
            log.info("Awaiting setup — will retry boot webhook with backoff")
            _retry_boot_webhook(config, message)
        return

    status = send_webhook(url, token, message)
    if status == 0 and config.awaiting_setup:
        _retry_boot_webhook(config, message)


def _retry_boot_webhook(config: Config, message: str):
    """Retry boot webhook with escalating backoff until configured or shutdown."""
    def _retry():
        backoff_idx = 0
        while not _shutdown.is_set():
            # Re-check config each iteration — agent may have configured us
            if not config.awaiting_setup:
                url = config.get("gateway", {}).get("webhook_url", "")
                token = config.get("gateway", {}).get("token", "")
                if url:
                    send_webhook(url, token, message)
                    return

            delay = _BOOT_BACKOFF[min(backoff_idx, len(_BOOT_BACKOFF) - 1)]
            log.info("Boot webhook retry in %ds (attempt %d)", delay, backoff_idx + 1)

            if _shutdown.wait(delay):
                return  # Shutting down

            url = config.get("gateway", {}).get("webhook_url", "")
            token = config.get("gateway", {}).get("token", "")
            if url:
                status = send_webhook(url, token, message)
                if status and status < 500:
                    return
            backoff_idx += 1

    t = threading.Thread(target=_retry, daemon=True, name="boot-webhook-retry")
    t.start()


def _start_flask(config: Config, db: Database, daemon_state: dict):
    """Start the Flask API server in a background thread."""
    app = create_app(config, db, daemon_state)

    def _run():
        # Suppress Flask's default startup banner in production
        app.run(host=FLASK_HOST, port=FLASK_PORT, threaded=True,
                use_reloader=False)

    t = threading.Thread(target=_run, daemon=True, name="flask-api")
    t.start()
    log.info("Flask API started on %s:%d", FLASK_HOST, FLASK_PORT)


def _start_daily_prune(db: Database):
    """Run DB pruning once daily in a background thread."""
    def _prune_loop():
        while not _shutdown.is_set():
            if _shutdown.wait(PRUNE_INTERVAL_S):
                return
            db.prune_old_data()

    t = threading.Thread(target=_prune_loop, daemon=True, name="db-prune")
    t.start()


def main():
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    log.info("AirShell daemon starting")

    # -- Init ----------------------------------------------------------------
    config = Config()
    db = Database()
    evaluator = AlarmEvaluator(db)
    device_url = _build_device_url()
    device_id = config.get("device_id", "airshell-01")
    skill = config.get("skill", "airshell")

    # Store device_url in config data for alarm message formatting
    # (transient — not persisted to disk)
    config._data["device_url"] = device_url

    boot_time = datetime.now(timezone.utc).isoformat()
    daemon_state = {
        "boot_time": boot_time,
        "last_reading_ts": None,
        "sensor_ok": False,
        "alarm_states": {},
    }

    # -- Boot webhook --------------------------------------------------------
    if config.awaiting_setup:
        boot_msg = (
            f"AirShell awaiting setup. "
            f"Device: {device_id} @ {device_url}. Use skill:{skill}."
        )
    else:
        boot_msg = (
            f"AirShell rebooted, config loaded. "
            f"Device: {device_id} @ {device_url}. Use skill:{skill}."
        )

    _send_boot_webhook(config, boot_msg)

    # -- Start Flask API -----------------------------------------------------
    _start_flask(config, db, daemon_state)

    # -- Start daily DB prune ------------------------------------------------
    _start_daily_prune(db)

    # -- Open sensor ---------------------------------------------------------
    sensor = SEN63CSensor()
    try:
        sensor.open()
        daemon_state["sensor_ok"] = True
        log.info("SEN63C sensor connected")
    except Exception as e:
        log.error("Failed to open sensor: %s", e)
        log.error("Continuing without sensor — API will still be available")
        daemon_state["sensor_ok"] = False

    # -- Main loop -----------------------------------------------------------
    sample_buffer: list[dict] = []
    last_aggregate = time.monotonic()

    log.info("Entering main loop (sample every %ds, aggregate every %ds)",
             SAMPLE_INTERVAL_S, AGGREGATE_INTERVAL_S)

    while not _shutdown.is_set():
        # Sample
        if daemon_state["sensor_ok"]:
            try:
                reading = sensor.read()
                sample_buffer.append(reading)
            except Exception as e:
                log.warning("Sensor read failed: %s", e)
                daemon_state["sensor_ok"] = False

        # Aggregate every ~60s
        elapsed = time.monotonic() - last_aggregate
        if elapsed >= AGGREGATE_INTERVAL_S and sample_buffer:
            last_aggregate = time.monotonic()
            avg = _average_samples(sample_buffer)
            sample_buffer.clear()

            ts = datetime.now(timezone.utc).isoformat()
            daemon_state["last_reading_ts"] = ts

            # Store to DB
            db.insert_reading(
                ts=ts,
                co2=avg["co2"], pm1=avg["pm1"], pm25=avg["pm25"],
                pm4=avg["pm4"], pm10=avg["pm10"],
                temp=avg["temp"], humidity=avg["humidity"],
            )
            log.info(
                "Stored 1-min avg: CO2=%.0f PM2.5=%.1f T=%.1f H=%.1f",
                avg["co2"], avg["pm25"], avg["temp"], avg["humidity"],
            )

            # Evaluate alarms (only if we have config with alarms)
            if not config.awaiting_setup and config.get("alarms"):
                cfg_snapshot = config.data
                cfg_snapshot["device_url"] = device_url
                events = evaluator.evaluate(avg, cfg_snapshot)

                daemon_state["alarm_states"] = evaluator.get_alarm_states()

                for event in events:
                    url = config.get("gateway", {}).get("webhook_url", "")
                    token = config.get("gateway", {}).get("token", "")
                    status = send_webhook(url, token, event["message"])

                    db.insert_alarm_event(
                        ts=ts,
                        alarm=event["alarm"],
                        event=event["event"],
                        value_raw=event["value_raw"],
                        value_smoothed=event["value_smoothed"],
                        message=event["message"],
                        webhook_status=status,
                    )
                    log.info("Alarm event: %s %s (webhook %d)",
                             event["alarm"], event["event"], status)

        # Sleep until next sample (interruptible by shutdown)
        _shutdown.wait(SAMPLE_INTERVAL_S)

    # -- Shutdown ------------------------------------------------------------
    log.info("Shutting down")
    try:
        sensor.close()
    except Exception:
        pass
    log.info("AirShell daemon stopped")


def _average_samples(samples: list[dict]) -> dict:
    """Average a list of sensor reading dicts."""
    keys = ["co2", "pm1", "pm25", "pm4", "pm10", "temp", "humidity"]
    avg = {}
    for key in keys:
        values = [s[key] for s in samples if s.get(key) is not None]
        avg[key] = sum(values) / len(values) if values else 0.0
    return avg


if __name__ == "__main__":
    main()
