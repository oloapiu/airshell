---

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

1. The **sensor** (Sensirion SEN63C) measures PM2.5, CO₂, temperature, and humidity
2. The **Pi** samples every 2 seconds, averages into 1-minute readings, stores locally, and evaluates alarm thresholds
3. When a threshold is crossed, the Pi sends a **webhook** to the agent
4. The **agent** wakes up, interprets the alarm using the `airshell` skill, and decides whether and how to tell you
5. You can talk back: *"Lower the CO₂ threshold"* or *"What was the air like last night?"*

The agent pushes alarm configuration to the sensor. The sensor never needs to know what a "good" CO₂ level is — that's the agent's job.

---

## Design Philosophy

**Sensors as feelers for agents.** Most agent proactivity today is driven by heartbeats — scheduled check-ins that fire at regular intervals regardless of what's happening in the world. But there's nothing special about the moment a heartbeat fires. The agent has no real reason to reach out, so it either stays quiet or fills the silence with a list of reminders that starts to feel repetitive. With sensors as feelers, agents react to things actually happening. The air quality dropped. The CO₂ is rising while the baby sleeps. There's a real reason to say something — and that changes everything about how the agent feels. Not a scheduled notification. A genuine response.

**Domain knowledge, not hardcoded rules.** The agent learns what CO₂ means for a nursery, what PM2.5 levels concern infants, and how to read trends. It controls alarms through understanding, not if-statements.

**Wake the agent when it matters.** The agent doesn't poll. The sensor watches its own data, and when a threshold is crossed, it wakes the agent with context. Most of the time, everyone sleeps.

**One sensor, one agent, direct connection.** No cloud. No middleware. No account. The sensor talks to the agent over [Tailscale](https://tailscale.com/), and that's it.

---

## Hardware

| Component | Part | Price | Notes |
|-----------|------|-------|-------|
| Compute | [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/) | ~$15 | Any Pi works, Zero 2 W is the sweet spot |
| Sensor | [Sensirion SEN63C](https://sensirion.com/products/catalog/SEN63C) | ~$50 | PM, CO₂, temp, humidity over I2C |
| Power | [Micro USB 5V/2.5A](https://www.raspberrypi.com/products/micro-usb-power-supply/) | ~$8 | Official Pi power supply |

**Total: ~$70.**

### Wiring

```
Pi Zero 2 W          SEN63C
-----------          ------
3.3V (pin 1)  ────►  VDD
GND  (pin 6)  ────►  GND
SDA  (pin 3)  ────►  SDA
SCL  (pin 5)  ────►  SCL
```

Four wires.

---

## Setup

### 1. Enable I2C on the Pi

```bash
sudo raspi-config
# Interface Options → I2C → Enable → reboot
```

### 2. Install Tailscale on the Pi

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Note your Pi's Tailscale IP — you'll need it later:

```bash
tailscale ip -4
```

### 3. Install the daemon on the Pi

```bash
git clone https://github.com/oloapiu/airshell.git
cd airshell
python3 -m venv venv
venv/bin/pip install -r requirements.txt
sudo cp airshell.service /etc/systemd/system/
sudo systemctl enable --now airshell
```

Verify it's running and reading the sensor:

```bash
sudo systemctl status airshell
curl http://localhost:5000/status
```

You should see live CO₂, PM2.5, temperature, and humidity. The live dashboard is already included — no extra install needed. The daemon is now sampling and waiting to be configured.

### 4. Install the skill on your agent

The `airshell` skill gives your agent the domain knowledge to interpret readings, run the setup interview, and push config to the sensor.

**Option A — via ClawHub (recommended):**

```bash
clawhub install airshell
```

**Option B — manually:**

```bash
cp -r skill ~/.openclaw/workspace/skills/airshell
```

OpenClaw picks up new skills automatically. No restart needed.

### 5. Connect the sensor to your agent

Send your agent one message:

> "I set up an AirShell sensor at `http://<PI_TAILSCALE_IP>:5000`. Set it up for me."

Your agent will:
1. Ask a few questions — what room, who uses it, where you are, how many alerts you want
2. Suggest alarm thresholds with reasoning based on your occupants
3. Push the config to the sensor — including its own webhook URL and token, so the sensor knows how to reach it
4. Confirm everything is live

The webhook is configured automatically. The agent knows its own endpoint and pushes it to the sensor during setup — you don't need to touch it.

That's it. The sensor is now connected to your agent. When the air needs attention, your agent wakes up and tells you in plain language.

---

## The Agent's Role

AirShell is designed to be used with an AI agent running the `airshell` skill. The skill gives the agent:

- **Domain knowledge** — what readings mean, health thresholds by occupant type, ventilation advice
- **Setup flow** — interviews you, translates answers into config, pushes it to the sensor
- **Alarm interpretation** — decides whether a threshold crossing is worth mentioning right now
- **Weather context** — checks outdoor air quality before suggesting you open a window
- **Ongoing tuning** — "too many alerts" → agent adjusts

The sensor identifies itself with a **skill hint** in every webhook (`Use skill:airshell`), so the agent knows which knowledge to load without being told.

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

### Webhook Messages

When an alarm fires, the sensor POSTs to your agent:

> AirShell alarm RAISED: co2_high — CO₂ > 800 ppm (smoothed: 805, raw: 823). Note: "Nursery CO₂ — check if window needs opening." Device: airshell-01 @ http://\<PI_TAILSCALE_IP\>:5000. Use skill:airshell.

The device URL is included so the agent can pull `/readings` or `/status` for context.

### Storage

| Property | Value |
|----------|-------|
| Engine | SQLite |
| Resolution | 1-min averages |
| Retention | 30 days (configurable) |
| Size | ~73 MB/year |

### Dashboard

The sensor serves a live dashboard at `GET /` — CO₂, PM2.5, temperature, and humidity charts with alarm thresholds. Auto-refreshes every 30 seconds. Mobile-friendly.

To view it, open a browser on any device connected to your Tailscale network and go to:

```
http://<PI_TAILSCALE_IP>:5000
```

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

## License

MIT

## Contributing

Issues and PRs welcome. If you build a feeler for a different sense, we'd love to hear about it.
