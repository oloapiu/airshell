# Temperature & Humidity — Domain Knowledge

## Temperature

### Standards — multi-source

| Source | Recommended range | Notes |
|--------|------------------|-------|
| **NHS UK / Lullaby Trust** | **16–20°C** | Infant bedrooms; overheating = SIDS risk factor |
| **AAP (US)** | No specific number | "One extra layer vs. adult in same room" |
| WHO Housing & Health Guidelines 2018 | Min 18°C | Cold-season minimum for health (cardiovascular/respiratory risk below 18°C) |
| EN 16798-1 Category I (children/vulnerable) | 21–23°C (winter) / 23.5–25.5°C (summer) | EU standard, Category I = highest quality, for sensitive occupants |
| Italian national decree (D.M. 1992, asili nido) | 20°C ±2°C = **18–22°C** | Legally binding for Italian nurseries |
| ASHRAE 55 (general comfort) | 20–24°C | Adult sedentary comfort range |
| Harvard 9 Foundations | 20–24°C (stable) | Thermal comfort range |

### Thresholds for a nursery (infant sleep)

| Level | Range | Note |
|-------|-------|------|
| 🚨 Too cold | <16°C | Risk of infant hypothermia; NHS lower bound |
| ⚠️ Cool | 16–18°C | Acceptable, use extra layer |
| ✅ Ideal | 18–20°C | NHS recommended range; optimal for infant sleep safety |
| ⚠️ Warm | 20–22°C | Acceptable for adults; borderline for infants |
| ❗ Hot | >22°C | Above Italian nursery decree upper limit; overheating risk |
| 🚨 Dangerous | >26°C | Act immediately |

**Key stat:** Overheating is an established SIDS risk factor. The NHS 16–20°C guidance is the most conservative, evidence-based official standard for infant bedrooms. Fan use above ~21°C has been associated with lower SIDS risk in research.

### AirShell nursery alarm thresholds (pending Paolo confirmation)
- **temp_high:** current raise=24°C / clear=22°C — **may be too permissive**: NHS upper safe limit is 20°C
- **temp_low:** current raise=10°C / clear=12°C — **likely too low**: NHS "Too cold" threshold is 16°C

### Advice
- If too hot: fan (not pointed directly at baby), remove layers, open window if outdoor is cooler and PM2.5 is low
- If too cold: close windows, add layer, check heating
- Room temperature matters more at night — infants cannot regulate temperature as well as adults, especially newborns
- Sensor self-heats ~1–2°C above ambient — account for this: if sensor reads 22°C, actual room temp is ~20–21°C

---

## Humidity

### Standards — multi-source

| Source | Range | Notes |
|--------|-------|-------|
| ASHRAE 62.1/55 | 30–60% RH | General indoor comfort and mold prevention |
| EN 16798-1 Category I | 30–50% RH | Highest quality, for sensitive occupants |
| Italian nursery decree (D.M. 1992) | 45–55% RH | Recommended for Italian asili nido |
| Harvard 9 Foundations | 30–60% RH | Mold/microbial threshold |
| Infant consensus (Dr. Sears, Sleep Foundation) | 40–60% RH | Optimal for infant airways |
| Infant ideal (refined) | 45–55% RH | Physiological optimum; prevents dry nasal passages |
| WHO dampness/mold guidelines | <60% (surface <80%) | Mold risk threshold |

### Thresholds

| Level | Range | Note |
|-------|-------|------|
| 🚨 Too dry | <30% RH | Irritates airways, increases static, dries mucous membranes, increases viral survival |
| ⚠️ Dry | 30–40% RH | Slightly below optimal; consider humidifier |
| ✅ Ideal | 40–60% RH | Comfortable for airways, inhibits dust mite growth |
| ✅ Ideal (infant) | 45–55% RH | Refined target for 0–6 months; prevents nasal congestion |
| ⚠️ High | 60–70% RH | Dust mites thrive, mold risk increases |
| ❗ Very High | >70% RH | Mold growth likely, respiratory issues |

### AirShell nursery alarm thresholds (pending Paolo confirmation)
- **humidity_high:** current raise=65% / clear=60% — **slightly permissive**: mold/dust mite territory begins at 60%; all standards agree max is 60%
- **humidity_low:** current raise=20% / clear=25% — **too permissive**: ASHRAE minimum is 30%; dry airways for infant before alarm fires

### Advice
- **Dry (<30%):** cool-mist humidifier (not warm-mist — burn risk near infants), bowls of water near radiators, houseplants
- **High (>60%):** dehumidifier or better ventilation; check for moisture sources (wet clothes, cooking steam, poor sealing)
- Brief spikes after bathing or cooking are normal and not a concern
- In Milan winters: heating systems dry the air significantly — humidity low alarms more likely October–March
- In Taipei summers: humidity high alarms more likely — outdoor air is humid and ventilating may worsen it

---

## Sensor note (SEN63C)
- Temperature from SHT45 (high-accuracy) — expect ±0.1°C precision
- Humidity from SHT45 — expect ±1.5% RH precision
- **Sensor self-heats ~1–2°C above ambient** — actual room temperature is ~1–2°C lower than sensor reading. Account for this when interpreting temperature alarms.
- No sentinel values for temp/humidity; readings are always valid after warmup
- Smoothing: 10-minute averages recommended (temperature and humidity change slowly; short smoothing adds noise without value)
