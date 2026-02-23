"""Configuration management for AirShell.

Loads and saves config from a JSON file on disk. Provides sensible defaults
for first boot when no config exists yet.

Thread-safe: the Flask API thread and the main loop both read config through
the same Config instance, protected by a threading lock.
"""

import json
import logging
import os
import threading
from typing import Any

log = logging.getLogger(__name__)

# Default path — lives next to daemon.py in the working directory
DEFAULT_CONFIG_PATH = "config.json"

# Defaults applied when no config file exists on disk
_DEFAULTS = {
    "device_id": "airshell-01",
    "skill": "airshell",
    "alarms": {},
    "notifications": {
        "default": {
            "on_raise": True,
            "on_clear": True,
            "repeat": {
                "enabled": True,
                "mode": "escalating",
                "intervals_min": [30, 15, 10, 5],
            },
            "agent_message": "",
        },
        "overrides": {},
    },
    "gateway": {
        "webhook_url": "",
        "token": "",
    },
}


class Config:
    """Thread-safe configuration store backed by a JSON file.

    On construction, loads from disk if the file exists. Otherwise populates
    with defaults and sets awaiting_setup=True.

    Usage:
        cfg = Config()              # loads or creates defaults
        cfg["alarms"]               # read a key
        cfg.data                    # full dict (read-only snapshot)
        cfg.update(new_dict)        # merge new config, persist to disk
    """

    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._awaiting_setup = False
        self._load()

    def _load(self):
        """Load config from disk, or fall back to defaults."""
        if os.path.exists(self._path):
            try:
                with open(self._path, "r") as f:
                    self._data = json.load(f)
                self._awaiting_setup = False
                log.info("Config loaded from %s", self._path)
            except (json.JSONDecodeError, OSError) as e:
                log.error("Failed to load config from %s: %s — using defaults", self._path, e)
                self._data = dict(_DEFAULTS)
                self._awaiting_setup = True
        else:
            self._data = dict(_DEFAULTS)
            self._awaiting_setup = True
            log.info("No config file found — using defaults, awaiting setup")

    @property
    def awaiting_setup(self) -> bool:
        with self._lock:
            return self._awaiting_setup

    @property
    def data(self) -> dict[str, Any]:
        """Return a snapshot of the current config."""
        with self._lock:
            return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def update(self, new_config: dict[str, Any]):
        """Merge new config values and persist to disk.

        After update, awaiting_setup is cleared — the agent has configured us.
        """
        with self._lock:
            self._data.update(new_config)
            self._awaiting_setup = False
            self._save_locked()

    def _save_locked(self):
        """Write current config to disk. Caller must hold self._lock."""
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
            log.info("Config saved to %s", self._path)
        except OSError as e:
            log.error("Failed to save config: %s", e)
