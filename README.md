# AirShell 🐢

An open-source air quality sensor that gives your AI agent a sense of smell.

Built for a nursery. Shaped like a turtle. Powered by [OpenClaw](https://github.com/openclaw/openclaw).

---

## What Is This?

AirShell is an indoor air quality monitor that connects directly to an AI agent. No app, no cloud dashboard, no account. The sensor reads the air, and when something matters, it wakes the agent. The agent understands what the readings mean — and tells you in plain language.

```
"Hey — CO₂ in Stefano's room hit 820ppm. That's above the threshold
for cognitive impact. Might want to crack a window. It's been climbing
for about 20 minutes."
```

The agent isn't just forwarding numbers. It has domain knowledge about air quality, knows your space, and decides whether something is worth mentioning. Most of the time, it says nothing — because the air is fine.

---

## How It Works

```
 ┌────────┐         ┌────────────┐            ┌─────────────┐          ┌──────┐
 │ SEN63C │── i2c ─►│ Pi Zero 2W │── webhook ►│ Agent+skill │◄─ chat ─►│ User │
 │ sensor │         │  sample    │            │  interpret  │          └──────┘
 └────────┘         │  store     │            │  decide     │
                    │  evaluate  │◄── config ─┤  configure  │
                    └────────────┘            └─────────────┘
```

1. The **sensor** (a Sensirion SEN63C) measures PM1/2.5/4/10, CO₂, temperature, and humidity
2. The **Pi** samples every 2 seconds, averages into 1-minute readings, stores locally, and evaluates alarm thresholds
3. When a threshold is crossed, the Pi sends a **webhook** to the agent
4. The **agent** wakes up, interprets the alarm using the `airshell` skill, and decides whether and how to tell you
5. You can talk back: *"Lower the CO₂ threshold"* or *"What was the air like last night?"*

The agent pushes alarm configuration to the sensor. The sensor never needs to know what a "good" CO₂ level is — that's the agent's job.

---

## Design Philosophy

**Sensors as feelers for agents.** AirShell extends an AI agent's awareness into the physical world. The sensor is a feeler — the agent is the brain.

**Domain knowledge, not hardcoded rules.** The agent learns what CO₂ means for a nursery, what PM2.5 levels concern infants, and how to read trends. It controls alarms through understanding, not if-statements.

**Wake the agent when it matters.** The agent doesn't poll. The sensor watches its own data, and when a threshold is crossed, it wakes the agent with context. Most of the time, everyone sleeps.

**One sensor, one agent, direct connection.** No cloud. No middleware. No account. The sensor talks to the agent over [Tailscale](https://tailscale.com/), and that's it.

---

## Hardware

| Component | Part | Price | Notes |
|-----------|------|-------|-------|
| Compute | [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/) | ~$15 | Any Pi works, Zero 2 W is the sweet spot |
| Sensor | [Sensirion SEN63C](https://sensirion.com/products/catalog/SEN63C) | ~$50 | All-in-one: PM, CO₂, temp, humidity over I2C |
| Power | USB-C 5V/1A | ~$5 | Any phone charger |

**Total: ~$70** for a sensor that measures everything that matters for indoor air.

### Wiring

```
Pi Zero 2 W          SEN63C
-----------          ------
3.3V (pin 1)  ────►  VDD
GND  (pin 6)  ────►  GND
SDA  (pin 3)  ────►  SDA
SCL  (pin 5)  ────►  SCL
```

That's it. Four wires.

---

## Software

### Prerequisites

- A Pi running Linux with [Tailscale](https://tailscale.com/download) installed
- An AI agent that can receive webhooks (we use [OpenClaw](https://github.com/openclaw/openclaw) with the `airshell` skill)
- Python 3

### Install

```bash
ssh pi@<tailscale-ip>
git clone https://github.com/your-org/airshell.git
cd airshell
pip install -r requirements.txt
sudo cp airshell.service /etc/systemd/system/
sudo systemctl enable --now airshell
```

The sensor starts sampling immediately with sensible defaults. To connect it to your agent, tell your agent:

> "I set up an AirShell at `http://<tailscale-ip>:5000`. Configure it for a nursery."

The agent takes it from there — it'll push alarm thresholds, set up notifications, and start monitoring.

---

## The Agent's Role

AirShell is designed to be used with an AI agent running the `airshell` skill. The skill gives the agent:

- **Domain knowledge** — what readings mean, health thresholds for infants, ventilation advice
- **Setup flow** — interview the user ("What room? Who sleeps there?"), translate answers into config
- **Alarm interpretation** — decide whether a threshold crossing is worth mentioning right now
- **Ongoing tuning** — user says "too many alerts" → agent adjusts

The sensor identifies itself with a **skill hint** in every webhook message (`Use skill:airshell`), so the agent knows which knowledge to load. The sensor doesn't know what it's for — it just says "I exist, use this skill to understand me."

---

## Architecture Details

### Sampling

| Stage | Rate | What happens |
|-------|------|-------------|
| Sample | ~2s | Read SEN63C |
| Aggregate | 1 min | Average samples, store to SQLite |
| Evaluate | 1 min | Check alarm thresholds, fire webhooks if needed |

**Principle:** Storage is a fact, alerting is an opinion. The 1-minute averages are ground truth. Smoothing for alarm evaluation is computed on the fly, never persisted.

### API

All endpoints served over Tailscale only.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/config` | Agent pushes config (alarms, notifications) |
| `GET` | `/config` | Current config |
| `GET` | `/readings` | Historical readings (`from`, `to`, `measurand`, `limit`) |
| `GET` | `/status` | Uptime, last reading, sensor health |
| `GET` | `/` | Live dashboard |

**Outbound:** webhook POST to agent gateway on alarm raise, clear, and repeat.

### Config

The agent pushes config to the sensor. The sensor persists it to disk.

```json
{
  "skill": "airshell",
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
    }
  },
  "notifications": {
    "default": {
      "on_raise": true,
      "on_clear": true,
      "repeat": {
        "enabled": true,
        "mode": "escalating",
        "intervals_min": [30, 15, 10, 5]
      }
    },
    "overrides": {
      "co2_high": {
        "agent_message": "Nursery CO₂ — check if window needs opening."
      }
    }
  },
  "gateway": {
    "webhook_url": "http://localhost:3456/hooks/agent",
    "token": "..."
  }
}
```

| Field | Description |
|-------|-------------|
| `measurand` | What to watch (`co2`, `pm25`, `temp`, `humidity`) |
| `operator` | Comparison (`>`, `<`, `>=`, `<=`) |
| `raise` | Threshold to trigger alarm |
| `clear` | Threshold to clear (hysteresis prevents flapping) |
| `smoothing_min` | Moving average window before evaluation |
| `repeat.mode` | `constant` (fixed interval) or `escalating` (increasingly urgent) |
| `agent_message` | Agent's note-to-self, included in the webhook |

Notifications are decoupled from detection. The `default` block applies to all alarms; `overrides` merge on top by alarm name.

### Webhook Messages

When an alarm fires, the sensor POSTs a natural-language message to the agent:

> AirShell alarm RAISED: co2_high — CO₂ > 800 ppm (smoothed: 805, raw: 823). Note: "Nursery CO₂ — check if window needs opening." Device: airshell-01 @ http://100.81.2.58:5000. Use skill:airshell.

The device URL is included so the agent can pull `/readings` or `/status` for context. The agent decides whether to notify the user, and how.

### Storage

| Property | Value |
|----------|-------|
| Engine | SQLite |
| Resolution | 1-min averages |
| Retention | 30 days (configurable) |
| Size | ~73 MB/year |

### Dashboard

The sensor serves a live dashboard at `GET /`.

- **uPlot** charts — CO₂, PM2.5, temperature, humidity
- Auto-refreshes every 30 seconds
- Alarm thresholds shown as horizontal lines
- Current values displayed prominently
- Mobile-friendly — designed for glancing after an alert

---

## The Bigger Picture

AirShell is a proof of concept for a simple idea: **AI agents should have senses.**

Today, agents live in a text world — chat messages, emails, documents. But the physical world is full of signals that matter: air quality, temperature, noise, light, motion, moisture. Each of these can be a simple sensor that wakes an agent when something is worth knowing.

The pattern is always the same:

```
Sensor ──(event)──► Agent + domain skill ──► User
```

The sensor is a feeler. The skill is domain knowledge. The agent is the brain. AirShell is the first feeler — a turtle that watches the air in a baby's room.

What's the next one?

---

## Status

- ✅ Pi Zero 2 W running, on Tailscale
- ✅ SEN63C sensor wired and reading — CO2, PM, temp, humidity confirmed working
- 🐢 Turtle enclosure: concept stage

## License

MIT

## Contributing

Issues and PRs welcome. If you build a feeler for a different sense, we'd love to hear about it.
