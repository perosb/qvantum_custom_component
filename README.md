[![qvantum_custom_component](https://img.shields.io/github/release/perosb/qvantum_custom_component/all.svg?label=current%20release)](https://github.com/perosb/qvantum_custom_component) [![downloads](https://img.shields.io/github/downloads/perosb/qvantum_custom_component/total?label=downloads)](https://github.com/perosb/qvantum_custom_component) [![codecov](https://codecov.io/gh/perosb/qvantum_custom_component/graph/badge.svg)](https://codecov.io/gh/perosb/qvantum_custom_component)

## Qvantum Heat Pump Integration for Home Assistant

Connect a Qvantum heat pump to Home Assistant using either the Qvantum cloud API or a direct local Modbus TCP connection.

It has been tested on the **QE** (exhaust air) and **QG** (ground source) families. It uses Qvantum's shared cloud API and Modbus register map and is expected to work with **QA+QH** (air source) where the same metrics and settings exist; the QE-specific architecture in [docs/qe-architecture.md](docs/qe-architecture.md) does not necessarily apply to other products.

- **Cloud mode (HTTP):** Uses your Qvantum account for live metrics, firmware details, SmartControl, and cloud settings.
- **Local Modbus mode:** Connects directly on your LAN without an account or cloud session, with faster local polling.

### Highlights

- **Self-learning heating curve (Modbus).** Instead of the pump's fixed Auto
  curve, the integration learns how your house responds to the forecast, the
  sun and the heat it delivers, then writes its own curve back. It starts in
  shadow mode, takes over only when the comparison is ready, and hands control
  back to the pump on any failure. [How it works →](docs/heating-curve.md)
- **Local, fast and offline.** Modbus TCP on your LAN with 15 s default
  polling and optional writes; a cloud account is only needed for cloud mode.
- **Analytics from data the pump already has.** COP/SCOP, auxiliary-heat
  share, envelope heat loss and time constant, DHW standing loss, solar gain
  and readable alarm names — no extra hardware.

### Installation

Requires Home Assistant **2026.9** or newer (shared Modbus connection).

1. **Install via HACS** (recommended): Search for **Qvantum Heat Pump**, install it, and restart Home Assistant.  
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=perosb&repository=qvantum_custom_component&category=integration)
2. **Manual installation:** Download the latest release, extract it to `custom_components/qvantum/`, and restart Home Assistant.
3. **Add the integration:** Go to **Settings → Devices & Services → Add Integration**, search for **Qvantum Heat Pump**, and choose **Qvantum cloud (HTTP)** or **Local Modbus (offline)**.

Only one Qvantum instance can be configured. Use **Reconfigure** to switch modes later.

#### Cloud setup

Sign in with your Qvantum account email and password. Metrics, firmware, SmartControl, Elevate Access, and most settings use the cloud API.

> [!CAUTION]
> Cloud mode uses the same internal API as the Qvantum app. It is experimental and runs at your own risk.

#### Local Modbus setup

1. In the Qvantum app, enable **Modbus external** (Installer → Service mode → Connectivity).
2. Enter the host (default `Qvantum-HP`), port, unit ID, and poll interval (default 15 seconds, minimum 5).

> [!IMPORTANT]
> Local Modbus is read-only until **Enable writing via Modbus** is enabled. Incorrect or out-of-range writes may affect performance, warranty, and system lifecycle.

### Features

- **Adaptive heating curve (Modbus) — the headline feature:** Learns the house from the forecast, sun and delivered heat and writes its own curve back; shadow mode first, readiness gate, hand-back on failure. [Details](docs/heating-curve.md).
- **Curve card:** A bundled Lovelace card (`qvantum-curve-card`) that graphs the baseline, shadow curve, written pump table and adjustment terms.
- **Monitoring:** Temperatures, pressure, energy, power, system status, defrost, connectivity, filter time, and other heat-pump metrics. Machine-internal values (refrigerant circuit, speeds, lifetime counters, settings read-backs) live under the **Diagnostics** entity category. Alarm codes carry Qvantum's friendly name in a `description` attribute.
- **Efficiency analytics:** COP and rolling SCOP (the DHW COP includes tank charges made with the electric element alone — parallel with, or after, the compressor — and is capped at 1.0 when no compressor work was involved), auxiliary-heat share, building heat-loss coefficient, heating degree hours and weather-normalized consumption, DHW standing loss (quoted at a 40 K tank-to-ambient difference), a DHW heat-meter plausibility check, compressor cycling/duty/speed and average power, plus an A–F efficiency health grade. All derived from data the integration already polls — no extra hardware.
- **Thermal mass:** the envelope time constant and heat capacity (`UA × τ`) are identified from how fast the rooms cool while the pump is not heating — hot water, defrost, idle. They say how quickly a house reacts to a pause; both are diagnostics, and both stay unavailable until the window holds enough decays across a spread of outdoor temperatures.
- **Solar:** Current solar irradiance and a self-calibrated solar-gain estimate from Open-Meteo, in both cloud and Modbus mode. No PV system is required.
- **Control:** Operation modes, target temperatures, vacation mode, ventilation, and supported settings.
- **Hot water (`water_heater`):** Tank temperature and DHW stop target with Eco, Normal, Extra, Smart, and Off modes.
- **Energy Dashboard:** Compressor, heating, DHW, additional, and total energy sensors are ready for one-click setup.
- **Device automations:** Triggers and conditions for defrost, compressor blocking, freeze protection, Wi-Fi/cloud connectivity, and a ventilation filter due in under 48 hours.
- **External room sensor:** The integration can feed a Home Assistant temperature sensor to the pump as the external room temperature, refreshed continuously (never slower than every 4 minutes) so the app's external room sensor mode stays fed.
- **Modbus writes:** Optional local writes for supported targets, DHW, fan, operation, room compensation, and sensor settings.

#### Device automation details

Available triggers and conditions depend on the entities present. They cover defrosting, compressor blocking, freeze protection, Wi-Fi/cloud disconnection, and a ventilation filter with fewer than 48 hours remaining.

#### External room temperature feed

The Qvantum app's sensor settings (**Off / All / BT2 / External Modbus**) can use an
external room temperature that Home Assistant supplies. The pump requires a
fresh external room temperature at least every ~5 minutes: when the value goes
stale, the pump drops the sensor source and raises alarm 8
("Controlling room temperature sensor(s) unavailable").

Enable the built-in feed (Modbus mode, **Enable writing via Modbus** on):

1. Open the integration **Configure** dialog.
2. Pick the HA temperature sensor under **External room temperature source**
   (e.g. an average of your room sensors).
3. Select **External** as "Source for indoor temperature".

The integration then writes a smoothed value (5-minute time constant) on its
own schedule, refreshed even when the value has not changed. It is never
slower than every 4 minutes, regardless of the configured poll interval, so
the pump's watchdog stays satisfied. Manual writes to the feed's number entity
are overwritten by the next refresh while the feed is active. Clear the picker
to turn the feed off.

Either way the pump's external room temperature is also exposed as a sensor —
`room_temp_ext` in cloud mode, and a diagnostic sensor next to the feed number
in Modbus mode — so the recorded series can be used by the building figures and
the adaptive curve. When the pump has no such sensor the metric is absent and
no entity is created.


### Adaptive heating curve

**The pump's Auto curve is a fixed table. This one learns your house.**

Modbus-only, and **Enable writing via Modbus** must be enabled. It always
starts in **shadow mode**: nothing is written, the calculated curve is only
compared with the pump's own. Once the deviation has stayed within the
readiness gate — median ≤ 1.5 °C, p95 ≤ 3.0 °C over at least three days and 24
heating hours — `sensor.qvantum_adaptive_curve_deviation` reports `ready`, and
you can turn on `switch.qvantum_adaptive_curve_control` yourself. The
integration never does.

| | Pump's Auto curve | Adaptive curve |
|---|---|---|
| Input | outdoor temperature + the pump's room term | outdoor forecast, sun, delivered heat, room term |
| Writes | — | only knots the forecast can reach, 0.75 K dead band per knot |
| Safety | — | shadow first, readiness gate, indoor cap, pump supply limits |
| Hand-back | — | switch off, failed write, lost connection or removing the integration |

While active, `sensor.qvantum_adaptive_curve_room_error` reports how far the
rooms actually sit from the target over the last three days. Restarting Home
Assistant does **not** hand back: the pump keeps running the last table written,
so it still heats while nothing is supervising it.

Optional terms via `qvantum.set_curve_terms` — **COP feedback** (reduce supply
while the measured COP is below its learned reference) and **cold-snap
pre-charge** (raise supply ahead of a forecast temperature drop) — are off by
default and never override the indoor cap or the pump's supply limits. If the
pump leaves the curve (the app or a cloud profile changed the curve type), the
integration drops to shadow and opens a repair issue instead of fighting the
choice.

**Get started**

1. Enable **Modbus TCP** and **Enable writing via Modbus**.
2. Let it run in shadow mode until the deviation sensor reports `ready`.
3. Turn on the adaptive-curve switch, and add the card below if you want the graph.

Details: [docs/heating-curve.md](docs/heating-curve.md).

**Curve card.** Add `/qvantum/qvantum-curve-card.js` as a **JavaScript module**
dashboard resource, then add the card:

```yaml
type: custom:qvantum-curve-card
```

It draws the frozen baseline, the computed shadow curve, the seven points
written to the pump, the ±1 °C write band, the optional operating point and
the adjustment term breakdown (outdoor, night/day, solar, load, trims).

<img width="600" alt="Adaptive heating curve" src="https://github.com/user-attachments/assets/03bed4aa-1816-4597-9c3c-fe3faf06ecd6" />


### Services and Elevate Access

#### `qvantum.extra_hot_water`

Schedule extra hot water production. Cloud mode uses a timed HTTP command; local Modbus writes DHW Extra/Normal and requires Modbus writing. The service accepts `device_id` (the heat pump serial) and `minutes` from 0–480 (default 120).

```yaml
service: qvantum.extra_hot_water
data:
  device_id: 123
  minutes: 60
```

Cloud extra ventilation is a timed boost; local Modbus fan extra is a sticky preset. The extra-DHW switch and timer are also available, and the service works in both modes.

#### `qvantum.set_curve_control` and `qvantum.set_curve_terms`

Adaptive-curve control (Modbus only). `set_curve_control` switches between
`shadow` and `active`; `set_curve_terms` toggles the optional control terms.
Both are off/opt-in by default.

```yaml
service: qvantum.set_curve_terms
data:
  cop_feedback: true
  precharge: true
```

#### Summary export and anonymous sharing (opt-in)

`qvantum.export_summary` writes and returns a compact, whitelist-built summary
of the installation: pump model and firmware, plant settings, the identified
envelope and curve figures, and counters. It contains no entity ids or names,
coordinates, energy totals, schedules or series, and the service sends nothing
by itself. Download it from `/api/qvantum/summary` while logged in, or find
`config/qvantum_summary.json`.

Sharing is **opt-in and never implied**: after a week of use a repair issue asks
once. If you accept, the same summary is posted once a week to a small
Cloudflare Worker; declining changes nothing else, including the adaptive curve.
`qvantum.telemetry_upload` sends now and `qvantum.telemetry_delete` erases every
row this installation sent.

Details, field list, retention and erasure: [docs/telemetry.md](docs/telemetry.md).

#### Elevate Access

Cloud HTTP only: the **Elevate Access** button grants temporary access to advanced settings and maintenance functions; local Modbus does not create these entities.

> [!WARNING]
> Elevate Access creates a Qvantum “Remote Service” access for your user and effectively grants service/installer-level permissions. Use it only when needed.

Entities:

- `button.qvantum_elevate_access_<device_id>` — press to elevate access.
- `sensor.qvantum_expires_at_<device_id>` — access expiration timestamp.

Optional auto-renewal automation:

```yaml
alias: "Qvantum: Elevate Access Before Expiration"
triggers:
  - trigger: time
    at:
      entity_id: sensor.qvantum_forhojd_atkomst_upphor
      offset: "10"
actions:
  - target:
      entity_id: button.qvantum_hoj_atkomst
    action: button.press
```

| Qvantum: Climate | Qvantum: Power |
|:-------------:|:------:|
| ![Inomhusklimat](https://github.com/user-attachments/assets/15e56f43-4d3c-41d7-aebc-899d6486586b) | ![Effekt](https://github.com/user-attachments/assets/d150f56f-cabe-44e0-b1b3-8647c1b079c2) |

## Documentation

- [QE architecture and measurement model](docs/qe-architecture.md) — the 175 L buffer tank vs through-flow DHW, the immersion heater, draw power and the sensor map.
- [QE quick reference](docs/qe-architecture-quick-reference.md) — the compact version for an AI context window.
- [QG per-compressor-unit registers](docs/compressor-units.md) — the two-unit block, why the legacy values are means, and gauge pressure vs the display's saturation temperature.
- [Alarm codes](docs/alarm-codes.md) — where the friendly names come from, the app-only codes 252/391 and the English-only policy.
- [Heating curve](docs/heating-curve.md) — Auto vs User defined, registers and the adaptive curve.
- [Derived metrics](docs/derived-metrics.md) — power, COP and DHW: how each figure is computed and where it can mislead.
- [Tap-water capacity](docs/tap-water-capacity.md) — the 1–7 preset, why the start/stop pair is the truth and what the two repair issues mean.
- [Telemetry](docs/telemetry.md) — what the opt-in summary export contains and how it is stored.

## Changelog

Per-release notes live in [`docs/releases/`](docs/releases/). Each file expands
every feature and bug fix in that release into a short paragraph, and the
latest notes are linked from the GitHub release page.


