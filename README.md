[![qvantum_custom_component](https://img.shields.io/github/release/perosb/qvantum_custom_component/all.svg?label=current%20release)](https://github.com/perosb/qvantum_custom_component) [![downloads](https://img.shields.io/github/downloads/perosb/qvantum_custom_component/total?label=downloads)](https://github.com/perosb/qvantum_custom_component) [![codecov](https://codecov.io/gh/perosb/qvantum_custom_component/graph/badge.svg)](https://codecov.io/gh/perosb/qvantum_custom_component)

## Qvantum Heat Pump Integration for Home Assistant

Connect a Qvantum heat pump to Home Assistant using either the Qvantum cloud API or a direct local Modbus TCP connection:

- **Cloud mode (HTTP):** Uses your Qvantum account for live metrics, firmware details, SmartControl, and cloud settings.
- **Local Modbus mode:** Connects directly on your LAN without an account or cloud session, with faster local polling.

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

- **Monitoring:** Temperatures, pressure, energy, power, system status, defrost, connectivity, filter time, and other heat-pump metrics. Machine-internal values (refrigerant circuit, speeds, lifetime counters, settings read-backs) live under the **Diagnostics** entity category.
- **Efficiency analytics:** COP and rolling SCOP, auxiliary-heat share, building heat-loss coefficient, heating degree hours and weather-normalized consumption, DHW standing loss, a DHW heat-meter plausibility check, compressor cycling/duty/speed and average power, plus an A–F efficiency health grade. All derived from data the integration already polls — no extra hardware.
- **Solar:** Current solar irradiance and a self-calibrated solar-gain estimate from Open-Meteo, in both cloud and Modbus mode. No PV system is required.
- **Control:** Operation modes, target temperatures, vacation mode, ventilation, and supported settings.
- **Hot water (`water_heater`):** Tank temperature and DHW stop target with Eco, Normal, Extra, Smart, and Off modes.
- **Energy Dashboard:** Compressor, heating, DHW, additional, and total energy sensors are ready for one-click setup.
- **Device automations:** Triggers and conditions for defrost, compressor blocking, freeze protection, Wi-Fi/cloud connectivity, and a ventilation filter due in under 48 hours.
- **External room sensor:** The integration can feed a Home Assistant temperature sensor to the pump as the external room temperature, refreshed continuously (never slower than every 4 minutes) so the app's external room sensor mode stays fed.
- **Modbus writes:** Optional local writes for supported targets, DHW, fan, operation, room compensation, and sensor settings.
- **Adaptive heating curve (Modbus):** A self-learning curve based on the weather forecast and the pump's own data. It starts in shadow mode and replaces the pump's Auto curve once you switch it on; see [docs/heating-curve.md](docs/heating-curve.md).
- **Curve card:** A bundled Lovelace card (`qvantum-curve-card`) that graphs the baseline, shadow curve, written pump table and adjustment terms.

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


#### Adaptive heating curve

Modbus-only, and **Enable writing via Modbus** must be enabled. Instead of
using the pump's fixed Auto curve, the integration learns how your house
responds — from the outdoor forecast, the local daylight rhythm, the sun and
the heat the pump delivers — and calculates its own version of the curve.

It always starts in **shadow mode**: nothing is written, and the calculated
curve is only compared with the pump's own curve. When the comparison has
looked good for a few days, the deviation sensor reports `ready` and you can
turn on `switch.qvantum_adaptive_curve_control`. The integration then takes over
the curve; turning the switch off — or a failed write, or a lost connection —
hands control straight back to the pump's Auto curve.

While active you can optionally enable extra control terms with
`qvantum.set_curve_terms`: **COP feedback** (reduce supply while the measured
COP is below its learned reference) and **cold-snap pre-charge** (raise supply
ahead of a forecast temperature drop). Both are off by default and never
override the indoor cap or the pump's supply limits.

**What is expected of you:**

- Enable Modbus writing.
- Wait until the deviation sensor reports `ready` (a few days of shadow data).
- Flip the switch yourself; the integration never does.

A bundled Lovelace card visualizes the curve. Add
`/qvantum/qvantum-curve-card.js` as a **JavaScript module** dashboard
resource, then add the card:

```yaml
type: custom:qvantum-curve-card
```

It draws the frozen baseline, the computed shadow curve, the seven points
written to the pump, the ±1 °C write band, the optional operating point and
the adjustment term breakdown (outdoor, night/day, solar, load, trims).

Details: [docs/heating-curve.md](docs/heating-curve.md).  

<img width="250" alt="Adaptive Curve" src="https://github.com/user-attachments/assets/03bed4aa-1816-4597-9c3c-fe3faf06ecd6" />


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


