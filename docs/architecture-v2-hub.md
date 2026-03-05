# AirShell v2 — Hub Architecture

*Internal engineering spec. The public README uses the simpler v1 direct-to-agent architecture. This is what we'll actually build when scaling to multiple devices or porting to MCU.*

---

## Why v2?

The v1 architecture (sensor does everything, webhooks directly to agent) is elegant and simple. But it has limitations:

- Alarm logic, storage, and dashboard all run on the Pi — hard to port to MCU/ESP32
- Agent queries the sensor over Tailscale for context — adds latency and coupling
- One Pi = one instance — no multi-device support
- Iterating alarm logic means SSH-ing into the Pi

v2 moves all intelligence off the device and into a hub service that runs alongside the agent. The device becomes trivially simple — portable to any hardware.

---

## Design Philosophy

**Sensors as feelers for agents.** AirShell extends an AI agent's awareness into the physical world. The sensor is a feeler — the agent is the brain.

**Domain knowledge, not hardcoded rules.** Through the `airshell` skill, the agent learns what readings mean for a nursery and controls alarms through understanding, not if-statements.

**Keep the sensor simple.** The device (airshell-sensor) samples, averages, and uploads. A service (airshell-hub) on the same machine as the agent stores readings, evaluates alarms, serves dashboards, and integrates with the agent. This keeps the device reliable, portable (Pi today, ESP32 or MCU tomorrow), and easy to reason about.

**Wake the agent when it matters.** The agent doesn't poll. The hub watches incoming data, and when a threshold is crossed, it wakes the agent with context. Most of the time, everyone sleeps.

---

## Architecture

Three components: **airshell-sensor** (the device), **airshell-hub** (the data service), and **OpenClaw** (the agent). They can run on the same machine or different ones — what matters is network reachability.

```
┌──────────────────────┐       ┌──────────────────────────────────────────┐
│  airshell-sensor 🐢  │       │             airshell-hub                 │
│                      │ HTTP  │                                          │
│  SEN63C ─(I2C)─► Pi ────────►  Receiver ─► SQLite ─► Alarm Engine     │
│                      │ POST  │                             │            │
│  sample → aggregate  │       │                             ▼            │
│  → upload → repeat   │       │                   Webhook to OpenClaw   │
│                      │       │                                          │
│  [backfill buffer]   │       │  Dashboard (web UI)                      │
└──────────────────────┘       └──────────────────────────────────────────┘
                                             │
                                             ▼
                               ┌────────────────────────────┐
                               │         OpenClaw            │
                               │                            │
                               │  Agent + skill:airshell    │
                               │  • domain knowledge        │
                               │  • alarm interpretation    │
                               │  • notification decisions  │
                               │  • user communication      │
                               │                            │
                               │  Queries hub for context   │
                               │  Pushes config to hub      │
                               └────────────────────────────┘
```

**Data flow:**
1. Sensor samples, aggregates, uploads to hub
2. Hub stores readings, evaluates alarm thresholds
3. Threshold crossed → hub webhooks agent → agent wakes up
4. Agent interprets alarm, decides whether/how to notify user

**Config flow:**
1. User tells agent: "Set up my AirShell" or "CO₂ threshold is too sensitive"
2. Agent pushes config to hub
3. Hub applies alarm config immediately; sensor config propagates via next upload response

---

## Setup

Setting up AirShell is a conversation:

```
User:  "I'm setting up a new AirShell. Code on the bottom is X7K9-M2P4."

Agent: → Installs airshell-hub if not running
       → Registers device token X7K9-M2P4
       → Pushes sensor config to the Pi via SSH
       → Runs the skill interview: "What room? Who sleeps there?"
       → Configures alarms based on answers
       → "nursery-turtle is live. CO₂ alert at 800ppm,
          PM2.5 at 25µg/m³. Dashboard: http://..."

User:  "Lower the CO₂ threshold to 600."

Agent: → Pushes updated alarm config to hub
       → "Done. I'll let you know when CO₂ crosses 600ppm."
```

No config files. No terminal. The agent handles everything.

---

## airshell-sensor

Target: ~100 lines of Python (or C for MCU port).

### Identity

Each device has a **device token** — a random string generated at flash time and printed on the enclosure (the turtle's belly).

**Format:** `X7K9-M2P4` (8 alphanumeric characters)

Baked into firmware at setup:
- `hub_url` — where to upload
- `token` — the printed device token

The device doesn't know its name, its room, or its thresholds. It just samples and uploads.

### Sensor Config

Controls sampling behavior only. Delivered in every upload response from the hub.

```json
{
  "name": "nursery-turtle",
  "sample_interval_s": 2,
  "aggregate_multiplier": 30,
  "upload_multiplier": 1
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `name` | — | Friendly name (informational) |
| `sample_interval_s` | 2 | How often to read the sensor |
| `aggregate_multiplier` | 30 | Average every N samples into one reading (30 × 2s = 60s) |
| `upload_multiplier` | 1 | Upload every N aggregates (1 = every 60s, 5 = every 5 min) |

**Why multipliers?** Impossible to misconfigure. `upload_multiplier` is always a whole number of aggregates — no divisibility edge cases, no validation needed.

**On boot:** loads cached config from disk, or hardcoded defaults. First upload response delivers the latest config from the hub.

### Main Loop

```
every sample_interval_s (2s):       read SEN63C → append to sample buffer

every aggregate (30 samples = 60s): average buffer → append to upload queue
                                    clear sample buffer

every upload (1 aggregate = 60s):   POST readings to hub
                                    on success: apply config from response, clear queue
                                    on failure: append to backfill buffer

every 5 min:                        if backfill buffer not empty → retry POST
```

No alarm logic. No database. No web server.

### Upload

**Request:**

```json
{
  "readings": [
    {
      "ts": "2026-02-19T12:00:00Z",
      "co2": 623,
      "pm1": 3.2,
      "pm25": 5.1,
      "pm4": 6.0,
      "pm10": 7.3,
      "temp": 22.4,
      "humidity": 45.2
    }
  ],
  "telemetry": {
    "wifi_rssi": -52,
    "cpu_temp": 41.2,
    "uptime_s": 86400,
    "firmware_version": "1.0.0",
    "backfill_depth": 0
  }
}
```

**Response:**

```json
{
  "ok": true,
  "config": {
    "name": "nursery-turtle",
    "sample_interval_s": 2,
    "aggregate_multiplier": 30,
    "upload_multiplier": 1
  }
}
```

- Identity comes from the `Authorization: Bearer <token>` header.
- `readings` supports batch (multiple aggregates or backfill).
- Hub deduplicates by `(device_id, ts)` — retries are safe.
- `telemetry` reflects device state at upload time (no separate timestamp).
- Config in the response is compared with cache; applied and saved if changed.

### Backfill Buffer

| Property | Value |
|----------|-------|
| Format | JSON lines (`backfill.jsonl`) |
| Max size | 1440 entries (~24 hours at 1-min resolution) |
| Behavior | FIFO — oldest dropped when full |
| Retry | Every 5 min, batch POST, clear on success |

### Before Registration

The device doesn't know if it's registered. It boots, samples, and uploads. The hub returns 403 for unknown tokens. The device buffers readings and keeps retrying. Once the user registers the token through the agent, the next upload succeeds and buffered readings flush. No data is lost.

### MCU Portability

| Concern | Pi (current) | ESP32 (future) |
|---------|-------------|----------------|
| Sensor | I2C (smbus2) | I2C (Wire) |
| Upload | HTTP POST (requests) | HTTP POST (WiFiClient) |
| Buffer | JSONL file | SPIFFS/LittleFS |
| Config cache | JSON file | NVS or SPIFFS |
| Network | WiFi + Tailscale | WiFi direct |
| Deep sleep | N/A (always on) | Between uploads |
| Code | ~100 lines Python | ~200 lines C |

The hub doesn't care what uploads the data.

---

## airshell-hub

Standalone data service. Receives sensor readings, stores them, evaluates alarms, serves dashboards. Not part of OpenClaw — communicates with the agent via webhooks and API.

### Hub Config

```yaml
# airshell-hub.yaml

server:
  host: "<VPS_TAILSCALE_IP>"        # bind address (e.g. Tailscale IP)
  port: 8080

auth:
  agent_token: "random-secret" # for agent-facing endpoints

storage:
  db_path: "data/airshell.db"
  retention:
    raw_days: 30               # 1-min resolution
    downsample_days: 365       # 15-min resolution
    aggregate: "forever"       # 1-hour resolution

gateway:
  webhook_url: "http://localhost:3456/hooks/agent"
  webhook_token: "<YOUR_WEBHOOK_TOKEN>"

stale_device:
  timeout_min: 5               # alert if no upload for this long
```

Generated by the agent during setup. Device tokens live in the database, not here.

### API

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/api/upload` | Sensor pushes readings; response includes sensor config | Device token |
| `POST` | `/api/devices` | Register a new device token | Agent token |
| `GET` | `/api/devices` | List all devices | Agent token |
| `GET` | `/api/devices/<id>` | Device status + alarm states | Agent token |
| `PUT` | `/api/devices/<id>/alarms` | Push alarm + notification config | Agent token |
| `GET` | `/api/devices/<id>/readings` | Fetch readings | Agent token |
| `GET` | `/dashboard/<id>` | Live dashboard | None (network-level auth) |

**Auth:**
- **Device token** — printed on enclosure, sent as `Authorization: Bearer <token>`. Unknown tokens get 403 until registered.
- **Agent token** — static secret from hub config. Used for all management endpoints.

### Database

`devices`

| Column | Type | Description |
|--------|------|-------------|
| `token` | text PK | Device token (printed on enclosure) |
| `device_id` | text UNIQUE | Friendly name assigned at registration |
| `type` | text | Device type (e.g. `airshell`) |
| `skill` | text | Skill hint for agent |
| `registered_at` | datetime | Registration time |
| `last_seen` | datetime | Last successful upload |
| `sensor_config` | text (JSON) | Sampling config (returned to sensor) |
| `alarm_config` | text (JSON) | Alarm thresholds + notification rules |

`readings` — unique index on `(device_id, ts)` for dedup

| Column | Type | Description |
|--------|------|-------------|
| `device_id` | text | FK to devices |
| `ts` | datetime | Aggregate timestamp |
| `co2` | real | ppm |
| `pm1`, `pm25`, `pm4`, `pm10` | real | µg/m³ |
| `temp` | real | °C |
| `humidity` | real | % RH |

`telemetry`

| Column | Type | Description |
|--------|------|-------------|
| `device_id` | text | FK to devices |
| `ts` | datetime | Upload time |
| `wifi_rssi` | integer | dBm |
| `cpu_temp` | real | °C |
| `uptime_s` | integer | Seconds since boot |
| `firmware_version` | text | |
| `backfill_depth` | integer | Buffered entries on device |

`alarm_events`

| Column | Type | Description |
|--------|------|-------------|
| `device_id` | text | FK to devices |
| `ts` | datetime | Event time |
| `alarm` | text | Alarm key (e.g. `co2_high`) |
| `event` | text | `raise`, `clear`, `repeat` |
| `value_raw` | real | Raw value |
| `value_smoothed` | real | Smoothed value that triggered evaluation |
| `message` | text | Webhook message sent |
| `webhook_status` | integer | HTTP response from gateway |

**Retention:**

| Tier | Resolution | Retention |
|------|-----------|-----------|
| Raw | 1 min | 30 days |
| Downsampled | 15 min | 1 year |
| Aggregated | 1 hour | Forever |

### Alarm Engine

Runs on every upload. Evaluates thresholds from the device's `alarm_config`.

**On each upload:**
1. Store readings (deduplicate by timestamp)
2. For each alarm: compute smoothed value → evaluate threshold → update state
3. If state changed or repeat due → webhook to OpenClaw

### Alarm Config

Pushed by the agent via `PUT /api/devices/<id>/alarms`:

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
    "temp_high":     { "measurand": "temp",     "operator": ">", "raise": 26,  "clear": 24, "smoothing_min": 10 },
    "temp_low":      { "measurand": "temp",     "operator": "<", "raise": 18,  "clear": 20, "smoothing_min": 10 },
    "humidity_high": { "measurand": "humidity", "operator": ">", "raise": 60,  "clear": 55, "smoothing_min": 10 },
    "humidity_low":  { "measurand": "humidity", "operator": "<", "raise": 30,  "clear": 35, "smoothing_min": 10 }
  },
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

| Field | Description |
|-------|-------------|
| `measurand` | Column in readings table |
| `operator` | `>`, `<`, `>=`, `<=`, `=` |
| `raise` | Threshold to trigger |
| `clear` | Threshold to clear (hysteresis) |
| `smoothing_min` | Moving average window (minutes) |
| `on_raise` / `on_clear` | Webhook on state change |
| `repeat.mode` | `"constant"` or `"escalating"` |
| `repeat.intervals_min` | Reminder schedule (escalating walks the list, then repeats last) |
| `agent_message` | Agent's note-to-self, included in webhook |

Overrides merge on top of `default` by alarm name.

### Webhook Messages

```json
{ "message": "<see below>", "token": "<webhook_token>" }
```

> AirShell RAISED: co2_high — co2 > 800 ppm (smoothed: 805, raw: 823). Note: "Nursery CO₂ — check if window needs opening." Device: nursery-turtle. Use skill:airshell.

> AirShell REPEAT: co2_high — co2 still > 800 ppm (smoothed: 830, raw: 845). Raised 45 min ago, repeat #2. Device: nursery-turtle. Use skill:airshell.

> AirShell CLEARED: co2_high — co2 < 700 ppm (smoothed: 695, raw: 680). Was raised 30 min (2 repeats). Device: nursery-turtle. Use skill:airshell.

> AirShell OFFLINE: nursery-turtle — last seen 10 min ago. Use skill:airshell.

### Stale Device Detection

If no upload within `stale_device.timeout_min` (default: 5 min), hub sends an OFFLINE webhook. Clears automatically when uploads resume.

---

## Dashboard

Served by airshell-hub.

| Property | Value |
|----------|-------|
| URL | `http://<hub-host>:<port>/dashboard/<device_id>` |
| Charts | uPlot — CO₂, PM2.5, temperature, humidity |
| Refresh | Every 30s |
| Thresholds | Horizontal lines from alarm config |
| Current values | Large, prominent, with alarm state |
| Mobile | Designed for glancing, not navigating |

---

## v1 → v2 Comparison

| Aspect | v1 (on-device) | v2 (hub-centric) |
|--------|----------------|-------------------|
| Alarm logic | On sensor | In airshell-hub |
| Storage (SQLite) | On sensor | In airshell-hub |
| Dashboard | Served by sensor | Served by airshell-hub |
| API surface | 6 endpoints on sensor | Sensor has 0 endpoints |
| Sensor firmware | ~500 lines | ~100 lines |
| Agent ↔ data | HTTP to sensor over network | Local API on hub |
| MCU portable | Difficult | Trivial |
| Network dependency | Can operate offline | Needs connectivity (24h buffer) |
| Iterate alarm logic | SSH to sensor | Redeploy hub |
| Multi-device | One sensor = one instance | Hub handles N sensors |
