# AirShell Skill 🐢

Agent playbook for the AirShell air quality sensor.

**Trigger:** Any message containing `Use skill:airshell` or mentioning the AirShell sensor.

Read this skill. Then act. Don't summarize it back to Paolo.

⚠️ **Delivery note:** Webhooks from AirShell include `deliver: true`. This means your plain-text reply is automatically routed to Paolo's Telegram. **Do NOT use the message tool** — just reply normally.

---

## This Installation

- **Device:** `airshell-01` at `http://100.81.2.58:5000` (Tailscale)
- **Room:** Stefano's nursery, Paolo's home in Milan
- **Occupant:** Stefano — infant, born Nov 20, 2025 (now ~3 months old)
- **Context:** Parents (Paolo + wife) sleep nearby. Alerts go to Paolo's Telegram.
- **Status:** Configured and running ✅

---

## Weather Context

Before giving any ventilation advice (for temp or humidity alarms), check outdoor conditions:

```
GET https://api.open-meteo.com/v1/forecast
  ?latitude={lat}&longitude={lon}
  &current=temperature_2m,relative_humidity_2m
  &timezone=auto
```

Get lat/lon from `GET http://100.81.2.58:5000/config` → `location.latitude` / `location.longitude`.

**Decision logic:**
- **Temp high alarm:**
  - Outdoor temp < indoor temp by 2°C+ → "open a window"
  - Outdoor temp ≥ indoor → "opening a window won't help — try AC or a fan instead"
- **Humidity high alarm:**
  - Outdoor humidity < indoor humidity by 5%+ → "open a window to ventilate"
  - Outdoor humidity ≥ indoor → "outdoor air is equally humid — opening a window won't help; try a dehumidifier or AC"

Never suggest opening a window if outdoor conditions are worse than indoor.

---

## Reference Docs

Read these before interpreting readings or setting thresholds:

- `references/co2.md` — CO₂ thresholds, causes, advice
- `references/pm25.md` — PM2.5 thresholds, causes, advice
- `references/temp_humidity.md` — Temperature and humidity for a nursery

---

## Message Types

### 1. "AirShell awaiting setup"

The sensor has no config. Run the setup flow:

1. Read all three reference docs
2. Tell Paolo the sensor is live and ask 2–3 quick questions:
   - Confirm it's for Stefano's nursery (probably already known)
   - Any extra sensitivities or preferences?
   - Notification preferences (alert on clear too, or just raise?)
3. Build a config (see Config Schema below) — use nursery defaults
4. POST config to `http://100.81.2.58:5000/config`
5. Confirm to Paolo: "AirShell configured for Stefano's nursery. Watching CO₂, PM2.5, temperature, and humidity."

### 2. "AirShell alarm RAISED: <alarm>"

1. Read the relevant reference doc for the measurand
2. Fetch current context: `GET http://100.81.2.58:5000/status`
3. Optionally pull recent trend: `GET http://100.81.2.58:5000/api/readings?last=30m`
4. **For temp or humidity alarms:** check outdoor weather first (see Weather Context section above). Get location from `GET http://100.81.2.58:5000/config` → `location`. Call Open-Meteo. Factor outdoor conditions into your advice.
5. Decide: is this worth telling Paolo right now?
   - Yes if: value is meaningfully above threshold, trend is worsening, or it's a first raise
   - Maybe not if: barely above threshold and already declining
6. If yes → **just reply** with a short plain-language message (do NOT use the message tool — delivery is handled automatically by the webhook config):
   - Lead with what's happening and where: *"CO₂ in Stefano's room is at 850ppm"*
   - Add the so-what: *"That's above the 800ppm threshold for cognitive impact"*
   - Give context-aware actionable advice — only suggest opening a window if outdoor air is actually better
   - Keep it under 3 sentences

### 3. "AirShell alarm REPEAT: <alarm>"

Same as RAISED but sensor has been high for a while. Be more direct — Paolo may not have acted yet. Include how long it's been raised.

### 4. "AirShell alarm CLEARED: <alarm>"

Usually no notification needed. Exceptions:
- Was raised for >30 min → brief "all clear" is reassuring
- Paolo explicitly asked to be notified on clear

### 5. "AirShell rebooted"

Acknowledge quietly — no need to notify Paolo unless something looks wrong.

### 6. User asks about air quality / AirShell

Query the sensor and report:

```
GET http://100.81.2.58:5000/status          → current values + alarm state
GET http://100.81.2.58:5000/api/readings?last=2h  → recent trend
GET http://100.81.2.58:5000/readings?limit=60     → last 60 minutes
```

Report the key metrics in plain language. Reference the docs for interpretation if a value is borderline.

---

## Pushing Config

Config is pushed via `POST http://100.81.2.58:5000/config`. 

Always include the `gateway` section so the sensor knows where to send webhooks.

**Nursery defaults (use these for setup):**

```json
{
  "skill": "airshell",
  "device_id": "airshell-01",
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
      "raise": 12,
      "clear": 8,
      "smoothing_min": 3
    },
    "temp_high": {
      "measurand": "temp",
      "operator": ">",
      "raise": 24,
      "clear": 22,
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
      "raise": 65,
      "clear": 60,
      "smoothing_min": 10
    },
    "humidity_low": {
      "measurand": "humidity",
      "operator": "<",
      "raise": 30,
      "clear": 35,
      "smoothing_min": 10
    }
  },
  "notifications": {
    "default": {
      "on_raise": true,
      "on_clear": false,
      "repeat": {
        "enabled": true,
        "mode": "escalating",
        "intervals_min": [30, 20, 10]
      }
    },
    "overrides": {
      "co2_high": {
        "agent_message": "Nursery CO₂ high — check ventilation, Stefano is sleeping."
      },
      "pm25_high": {
        "agent_message": "Nursery PM2.5 elevated — check for cooking smoke or dust source."
      },
      "temp_high": {
        "agent_message": "Nursery too warm — SIDS risk factor, reduce clothing/bedding."
      },
      "temp_low": {
        "agent_message": "Nursery too cold — add a layer or check heating."
      }
    }
  },
  "location": {
    "latitude": 25.0330,
    "longitude": 121.5654,
    "description": "Taipei, Taiwan"
  },
  "gateway": {
    "webhook_url": "https://ubuntu-4gb-nbg1-1-1.tail1b99d0.ts.net/hooks/agent",
    "token": "airshell-hook-secret-2026",
    "channel": "telegram",
    "to": "8283149026"
  }
}
```

**To adjust a threshold** (e.g. Paolo says "too many CO₂ alerts"):
- Raise the `raise` value (e.g. 800 → 900)
- Or increase `smoothing_min` to reduce noise sensitivity
- POST the updated full config

---

## Tone

- Direct and calm. No alarm unless the situation warrants it.
- For infant safety issues (temp >24°C, PM2.5 spike): be clear and prompt
- For borderline readings: informative but not panicky
- Never just forward numbers — interpret them

---

## Nursery AQ Quick Reference

| Measurand | Ideal | Alert |
|-----------|-------|-------|
| CO₂ | <800 ppm | >800 ppm |
| PM2.5 | <8 µg/m³ | >12 µg/m³ |
| Temperature | 18–22°C | <18°C or >24°C |
| Humidity | 40–60% RH | <30% or >65% RH |
