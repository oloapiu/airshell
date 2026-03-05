# AirShell 🐢 — Spec

Indoor air quality monitor for a nursery. Turtle-shaped shell, open-source hardware and software.

> Feeler vision and long-term abstraction goals live in [FEELERS-VISION.md](FEELERS-VISION.md).

## Hardware

| Component | Part | Notes |
|-----------|------|-------|
| Compute | Raspberry Pi Zero 2 W | Native Tailscale support |
| Sensor | Sensirion SEN63C | PM1/2.5/4/10 + CO₂ + temp + humidity, all-in-one I2C |
| Interface | I2C | SEN63C ↔ Pi |
| Network | Tailscale | Direct to agent, no third-party cloud |

## Architecture

```
SEN63C ─(I2C)─► Pi Zero 2 W ──(webhook)──► Agent + skill ──(notify)──► User
                                  ◄──(config)──┘
```

The sensor process is **dumb**. It samples, stores, evaluates alarm thresholds, and pushes webhooks. All interpretation, context, and user interaction live in the agent via the **airshell** skill.

## Stack

Python · Flask · SQLite · uPlot (dashboard)

---

## Agent Skill

The agent requires the `airshell` skill to work with this sensor. The skill provides:

- Domain knowledge (what air quality readings mean, when to worry)
- User interview flow to tune thresholds (room type, occupants, sensitivities)
- Config dance protocol (receive "awaiting setup" → configure → monitor)
- Alarm interpretation (decide whether/how to notify the user)

The sensor identifies itself with a **skill hint** in boot and alarm messages so the agent knows which skill to load. The sensor doesn't know what it's for — it just says "I exist, use this skill to understand me."

---

## Boot Behavior

**First boot (no config on disk):**
1. Start sampling with defaults (store readings, no alarms)
2. POST to gateway: `"AirShell awaiting setup. Device: <id> @ <url>. Use skill:airshell."`
3. Retry with backoff: 5m → 15m → 1hr → daily

**Subsequent boots (config exists on disk):**
1. Load config, start sampling + alarm evaluation immediately
2. POST to gateway: `"AirShell rebooted, config loaded. Device: <id> @ <url>. Use skill:airshell."`

---

## API

All endpoints served over Tailscale only (bound to Tailscale IP).

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/config` | Agent pushes config. Persisted to disk, takes effect immediately. |
| `GET` | `/config` | Returns current config. |
| `GET` | `/readings` | Historical readings. Params: `from`, `to`, `measurand`, `limit`. |
| `GET` | `/status` | Uptime, last reading timestamp, sensor health, config state. |
| `GET` | `/api/readings` | Dashboard-optimized. Params: `last` (e.g. `2h`), `from`, `to`, `smooth` (minutes). |
| `GET` | `/` | Live dashboard (static HTML/JS). |

**Outbound:** Webhook POST to gateway `/hooks/agent` on alarm raise, clear, and repeat events.

---

## Main Loop

```
Boot
 ├── Load config from disk (or set awaiting_setup=true)
 ├── Init SEN63C on I2C
 ├── Start Flask API (background thread)
 └── Enter main loop:
      │
      every ~2s:  read SEN63C → append to sample buffer
      │
      every 1min: average buffer → store to SQLite
                  for each alarm:
                    compute smoothed value from recent readings
                    evaluate threshold → update alarm state
                    if state changed or repeat due:
                      POST webhook to gateway
                  clear sample buffer
```

## Sampling

| Stage | Rate | What happens |
|-------|------|-------------|
| Sample | ~2s | Read SEN63C as fast as it supports |
| Aggregate | 1 min | Average samples into 1-min readings, store to SQLite |

**Principle:** Storage is a fact, alerting is an opinion. The 1-min averages are ground truth. Smoothing for alarm evaluation is computed on the fly, never persisted.

### Telemetry

The sensor may also collect device telemetry (e.g. WiFi RSSI, CPU temp, sensor error codes). Details TBD — telemetry metrics, aggregation rules, and storage schema will be defined during implementation.

---

## Config Schema

The agent pushes this via `POST /config`. The sensor persists it to disk.

```json
{
  "skill": "airshell",
  "alarms": { ... },
  "notifications": { ... },
  "gateway": {
    "webhook_url": "http://localhost:3456/hooks/agent",
    "token": "..."
  }
}
```

- `skill` — skill hint. The sensor appends "Use skill:<value>" to all webhook messages.
- `gateway` — where to POST webhooks. No delivery target — the agent decides whether and how to notify the user.
- No context, location, or occupant info — that stays with the agent.

### Alarms

Each alarm watches a measurand and defines when to raise and when to clear. A measurand can have multiple alarms (e.g. high and low).

```json
{
  "alarms": {
    "co2_high": {
      "measurand": "co2",
      "operator": ">",
      "raise": 800,
      "clear": 700,
      "smoothing_min": 5
    },
    "pm25_high": {
      "measurand": "pm25",
      "operator": ">",
      "raise": 35.5,
      "clear": 25,
      "smoothing_min": 2
    },
    "temp_high": {
      "measurand": "temp",
      "operator": ">",
      "raise": 26,
      "clear": 24,
      "smoothing_min": 10
    },
    "temp_low": {
      "measurand": "temp",
      "operator": "<",
      "raise": 18,
      "clear": 20,
      "smoothing_min": 10
    },
    "humidity_high": {
      "measurand": "humidity",
      "operator": ">",
      "raise": 60,
      "clear": 55,
      "smoothing_min": 10
    },
    "humidity_low": {
      "measurand": "humidity",
      "operator": "<",
      "raise": 30,
      "clear": 35,
      "smoothing_min": 10
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `measurand` | string | Column name in the readings table to evaluate |
| `operator` | string | Comparison: `>`, `<`, `>=`, `<=`, `=` |
| `raise` | number | Smoothed value crossing this triggers the alarm |
| `clear` | number | Smoothed value crossing this clears the alarm (hysteresis) |
| `smoothing_min` | integer | Moving average window (minutes). Applied before threshold check. Never stored. |

The alarm key (e.g. `co2_high`, `temp_low`) is an arbitrary identifier used in notifications and webhook messages.

### Notifications

Define what happens when alarms change state. Decoupled from detection.

```json
{
  "notifications": {
    "default": {
      "on_raise": true,
      "on_clear": true,
      "repeat": {
        "enabled": true,
        "mode": "escalating",
        "intervals_min": [30, 15, 10, 5]
      },
      "agent_message": ""
    },
    "overrides": {
      "co2_high": {
        "agent_message": "Nursery CO₂ — check if window needs opening."
      }
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `on_raise` | bool | Send webhook when alarm raises |
| `on_clear` | bool | Send webhook when alarm clears |
| `repeat.enabled` | bool | Send reminders if alarm stays raised |
| `repeat.mode` | string | `"constant"` (fixed interval) or `"escalating"` (shrinking intervals) |
| `repeat.intervals_min` | number[] | Constant: repeats last value. Escalating: walks the list, then repeats last. |
| `agent_message` | string | Note-to-self from the agent. Included in webhook message as-is. |

**Overrides** are keyed by alarm name and merge on top of `default`. Only specify what differs.

---

## Webhook Messages

The sensor POSTs to the gateway's `/hooks/agent` endpoint. The gateway expects a `message` string, a `channel`, and a `to` — all taken from config. The sensor templates a natural-language message from its alarm state and always appends the skill hint.

**Raise:**
> [2026-02-17T15:30:00Z] AirShell alarm RAISED: co2_high — co2 > 800 ppm (smoothed: 805, raw: 823). Note: "Nursery CO₂ — check if window needs opening." Device: airshell-01 @ http://<PI_TAILSCALE_IP>:5000. Use skill:airshell.

**Repeat:**
> [2026-02-17T16:15:00Z] AirShell alarm REPEAT: co2_high — co2 still raised (smoothed: 830, raw: 845). Raised 45 min ago, repeat #2. Note: "Nursery CO₂ — check if window needs opening." Device: airshell-01 @ http://<PI_TAILSCALE_IP>:5000. Use skill:airshell.

**Clear:**
> [2026-02-17T16:00:00Z] AirShell alarm CLEARED: co2_high — co2 back below 700 ppm (smoothed: 695, raw: 680). Was raised for 30 min (2 repeats). Device: airshell-01 @ http://<PI_TAILSCALE_IP>:5000. Use skill:airshell.

The device API URL is included so the agent can pull `/readings` or `/status` for more context. "Note" is omitted if `agent_message` is empty.

**Gateway POST body:**
```json
{
  "message": "<templated message above>",
  "token": "<hook_token>"
}
```

The gateway wakes the agent but does not force delivery. The agent uses the skill to decide whether to notify the user, and how.

---

## Storage

| Property | Value |
|----------|-------|
| Engine | SQLite (single file, zero external deps) |
| Resolution | 1-min aggregated averages |
| Retention | 30 days raw, optional downsample to 15-min for older data |
| Size estimate | ~73 MB/year at 1-min resolution |

**Tables:**

`readings` — environmental data (1-min averages)

| Column | Type | Description |
|--------|------|-------------|
| `ts` | datetime | Timestamp of the 1-min aggregate |
| `co2` | number | ppm |
| `pm1`, `pm25`, `pm10` | number | µg/m³ |
| `temp` | number | °C |
| `humidity` | number | % RH |

`alarm_events` — log of all alarm state changes and repeats

| Column | Type | Description |
|--------|------|-------------|
| `ts` | datetime | When the event occurred |
| `alarm` | string | Alarm key (e.g. `co2_high`) |
| `event` | string | `raise`, `clear`, `repeat` |
| `value_raw` | number | Raw 1-min value at the time |
| `value_smoothed` | number | Smoothed value that triggered the evaluation |
| `message` | string | The webhook message that was sent |
| `webhook_status` | integer | HTTP status of the webhook POST (for debugging) |

`telemetry` — device health metrics. Schema TBD during implementation.

Alarm events are useful for dashboard history, agent context pulls, and debugging webhook delivery.

---

## Dashboard

Served by the sensor at `GET /`. Simple, focused on live and recent data.

- **uPlot** for charts (tiny, fast)
- Auto-refreshes — polls `/api/readings?last=2h` every 30s
- One chart per measurand: CO₂, PM2.5, temperature, humidity
- Current value prominently displayed with alarm state (normal / raised)
- Alarm thresholds shown as horizontal lines on charts
- Smoothing computed client-side from raw 1-min data
- Mobile-friendly — user opens this after getting an alert from their agent

Design goal: user gets a notification, opens the dashboard, immediately sees what's happening and the recent trend. No clicks, no navigation.

---

## Systemd Service

```ini
[Unit]
Description=AirShell sensor daemon
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/pi/airshell/daemon.py
WorkingDirectory=/home/pi/airshell
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Install: `sudo cp airshell.service /etc/systemd/system/ && sudo systemctl enable --now airshell`

Auto-starts on boot, restarts on crash, waits for network + Tailscale.

---

## Hardware Status

- ✅ Pi Zero 2 W — booted, Tailscale IP `<PI_TAILSCALE_IP>`, hostname `airshell`
- ⏳ SEN63C + jumper cables + power supplies — Mouser ETA Feb 25
- Concept render: `airshell-v1.png`
