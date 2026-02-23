"""Alarm evaluation engine for AirShell.

Evaluates alarm thresholds against smoothed sensor readings, tracks alarm
state in memory (raised/cleared), and produces webhook messages following
the exact format defined in the spec.

Alarm flow:
  1. Compute smoothed value (moving average over N minutes from DB)
  2. Compare against raise/clear thresholds with hysteresis
  3. On state change or repeat timer, generate a webhook message
"""

import logging
import operator
from datetime import datetime, timezone
from typing import Any

from airshell.db import Database

log = logging.getLogger(__name__)

# Supported comparison operators
_OPERATORS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "=": operator.eq,
}

# Human-readable units for webhook messages
_UNITS = {
    "co2": "ppm",
    "pm1": "µg/m³",
    "pm25": "µg/m³",
    "pm4": "µg/m³",
    "pm10": "µg/m³",
    "temp": "°C",
    "humidity": "%RH",
}


class _AlarmState:
    """In-memory state for a single alarm."""

    def __init__(self):
        self.raised = False
        self.raised_at: datetime | None = None
        self.repeat_count = 0
        self.last_repeat_at: datetime | None = None


class AlarmEvaluator:
    """Evaluates alarm thresholds and generates webhook messages.

    Tracks alarm state in memory across evaluation cycles. State is lost on
    restart — alarms re-evaluate from scratch on boot (by design; the agent
    gets a reboot notification separately).
    """

    def __init__(self, db: Database):
        self._db = db
        self._states: dict[str, _AlarmState] = {}

    def _get_state(self, alarm_name: str) -> _AlarmState:
        if alarm_name not in self._states:
            self._states[alarm_name] = _AlarmState()
        return self._states[alarm_name]

    def _compute_smoothed(self, measurand: str, smoothing_min: int,
                          current_value: float) -> float:
        """Compute a moving average over the last N minutes from the DB.

        Includes the current (not yet stored) 1-min reading in the average.
        """
        if smoothing_min <= 1:
            return current_value

        recent = self._db.get_recent_readings(smoothing_min)
        values = [r[measurand] for r in recent if r.get(measurand) is not None]
        values.append(current_value)
        return sum(values) / len(values)

    def _get_notification_config(self, alarm_name: str,
                                 notifications: dict) -> dict:
        """Merge default notification config with per-alarm overrides."""
        default = notifications.get("default", {})
        overrides = notifications.get("overrides", {}).get(alarm_name, {})
        merged = dict(default)
        merged.update(overrides)
        return merged

    def _get_repeat_interval_min(self, notif_config: dict,
                                 repeat_count: int) -> float | None:
        """Return the repeat interval in minutes, or None if repeats disabled."""
        repeat = notif_config.get("repeat", {})
        if not repeat.get("enabled", False):
            return None

        intervals = repeat.get("intervals_min", [30])
        mode = repeat.get("mode", "constant")

        if mode == "escalating":
            # Walk the list, then repeat the last value
            idx = min(repeat_count, len(intervals) - 1)
            return intervals[idx]
        else:
            # Constant mode: always use the last value in the list
            return intervals[-1] if intervals else 30

    def evaluate(self, reading_1min: dict, config: dict) -> list[dict]:
        """Evaluate all alarms against a 1-minute averaged reading.

        Args:
            reading_1min: dict with measurand keys (co2, pm25, temp, etc.)
            config: full config dict (alarms, notifications, gateway, etc.)

        Returns:
            List of dicts, each with keys:
                - alarm: alarm name
                - event: "raise", "clear", or "repeat"
                - message: formatted webhook message string
                - value_raw: raw 1-min value
                - value_smoothed: smoothed value used for evaluation
        """
        alarms_config = config.get("alarms", {})
        notifications = config.get("notifications", {})
        device_id = config.get("device_id", "airshell-01")
        skill = config.get("skill", "airshell")
        device_url = config.get("device_url", "")

        now = datetime.now(timezone.utc)
        results = []

        for alarm_name, alarm_cfg in alarms_config.items():
            measurand = alarm_cfg["measurand"]
            raw_value = reading_1min.get(measurand)
            if raw_value is None:
                continue

            op_str = alarm_cfg["operator"]
            op_fn = _OPERATORS.get(op_str)
            if op_fn is None:
                log.warning("Unknown operator %r in alarm %s", op_str, alarm_name)
                continue

            raise_threshold = alarm_cfg["raise"]
            clear_threshold = alarm_cfg["clear"]
            smoothing_min = alarm_cfg.get("smoothing_min", 1)

            smoothed = self._compute_smoothed(measurand, smoothing_min, raw_value)
            state = self._get_state(alarm_name)
            notif = self._get_notification_config(alarm_name, notifications)
            unit = _UNITS.get(measurand, "")

            if not state.raised:
                # Check if we should raise
                if op_fn(smoothed, raise_threshold):
                    state.raised = True
                    state.raised_at = now
                    state.repeat_count = 0
                    state.last_repeat_at = now

                    if notif.get("on_raise", True):
                        msg = self._format_raise(
                            now, alarm_name, measurand, op_str,
                            raise_threshold, unit, smoothed, raw_value,
                            notif.get("agent_message", ""),
                            device_id, device_url, skill,
                        )
                        results.append({
                            "alarm": alarm_name,
                            "event": "raise",
                            "message": msg,
                            "value_raw": raw_value,
                            "value_smoothed": smoothed,
                        })
            else:
                # Alarm is raised — check for clear
                # For ">" alarms, clear when smoothed goes below clear threshold
                # For "<" alarms, clear when smoothed goes above clear threshold
                clear_op = _OPERATORS["<"] if op_str in (">", ">=") else _OPERATORS[">"]
                if clear_op(smoothed, clear_threshold):
                    raised_duration = int((now - state.raised_at).total_seconds() / 60)
                    repeats = state.repeat_count

                    state.raised = False
                    state.raised_at = None
                    state.repeat_count = 0
                    state.last_repeat_at = None

                    if notif.get("on_clear", True):
                        clear_dir = "below" if op_str in (">", ">=") else "above"
                        msg = self._format_clear(
                            now, alarm_name, measurand, clear_threshold,
                            unit, smoothed, raw_value, raised_duration,
                            repeats, clear_dir, device_id, device_url, skill,
                        )
                        results.append({
                            "alarm": alarm_name,
                            "event": "clear",
                            "message": msg,
                            "value_raw": raw_value,
                            "value_smoothed": smoothed,
                        })
                else:
                    # Still raised — check if repeat is due
                    interval = self._get_repeat_interval_min(notif, state.repeat_count)
                    if interval is not None and state.last_repeat_at:
                        elapsed = (now - state.last_repeat_at).total_seconds() / 60
                        if elapsed >= interval:
                            state.repeat_count += 1
                            state.last_repeat_at = now
                            raised_min = int((now - state.raised_at).total_seconds() / 60)

                            msg = self._format_repeat(
                                now, alarm_name, measurand, smoothed,
                                raw_value, raised_min, state.repeat_count,
                                notif.get("agent_message", ""),
                                device_id, device_url, skill,
                            )
                            results.append({
                                "alarm": alarm_name,
                                "event": "repeat",
                                "message": msg,
                                "value_raw": raw_value,
                                "value_smoothed": smoothed,
                            })

        return results

    def get_alarm_states(self) -> dict[str, bool]:
        """Return current raised/cleared state for all tracked alarms."""
        return {name: state.raised for name, state in self._states.items()}

    # -- Message formatting --------------------------------------------------

    @staticmethod
    def _format_raise(now: datetime, alarm: str, measurand: str, op: str,
                      threshold: float, unit: str, smoothed: float,
                      raw: float, agent_msg: str, device_id: str,
                      device_url: str, skill: str) -> str:
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = (
            f"[{ts}] AirShell alarm RAISED: {alarm} — "
            f"{measurand} {op} {threshold} {unit} "
            f"(smoothed: {smoothed:.0f}, raw: {raw:.0f})."
        )
        if agent_msg:
            msg += f' Note: "{agent_msg}"'
        msg += f" Device: {device_id} @ {device_url}. Use skill:{skill}."
        return msg

    @staticmethod
    def _format_repeat(now: datetime, alarm: str, measurand: str,
                       smoothed: float, raw: float, raised_min: int,
                       repeat_count: int, agent_msg: str, device_id: str,
                       device_url: str, skill: str) -> str:
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = (
            f"[{ts}] AirShell alarm REPEAT: {alarm} — "
            f"{measurand} still raised "
            f"(smoothed: {smoothed:.0f}, raw: {raw:.0f}). "
            f"Raised {raised_min} min ago, repeat #{repeat_count}."
        )
        if agent_msg:
            msg += f' Note: "{agent_msg}"'
        msg += f" Device: {device_id} @ {device_url}. Use skill:{skill}."
        return msg

    @staticmethod
    def _format_clear(now: datetime, alarm: str, measurand: str,
                      threshold: float, unit: str, smoothed: float,
                      raw: float, raised_min: int, repeats: int,
                      direction: str, device_id: str, device_url: str,
                      skill: str) -> str:
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = (
            f"[{ts}] AirShell alarm CLEARED: {alarm} — "
            f"{measurand} back {direction} {threshold} {unit} "
            f"(smoothed: {smoothed:.0f}, raw: {raw:.0f}). "
            f"Was raised for {raised_min} min ({repeats} repeats)."
        )
        msg += f" Device: {device_id} @ {device_url}. Use skill:{skill}."
        return msg
