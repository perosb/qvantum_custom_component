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

- **Monitoring:** Temperatures, pressure, energy, power, system status, defrost, connectivity, filter time, and other heat-pump metrics.
- **Control:** Operation modes, target temperatures, vacation mode, ventilation, and supported settings.
- **Hot water (`water_heater`):** Tank temperature and DHW stop target with Eco, Normal, Extra, Smart, and Off modes.
- **Energy Dashboard:** Compressor, heating, DHW, additional, and total energy sensors are ready for one-click setup.
- **Device automations:** Triggers and conditions for defrost, compressor blocking, freeze protection, Wi-Fi/cloud connectivity, and a ventilation filter due in under 48 hours.
- **External room sensor:** When configured by the pump, a Modbus number entity can mirror an external temperature into the control setpoint.
- **Modbus writes:** Optional local writes for supported targets, DHW, fan, operation, room compensation, and sensor settings.

#### Device automation details

Available triggers and conditions depend on the entities present. They cover defrosting, compressor blocking, freeze protection, Wi-Fi/cloud disconnection, and a ventilation filter with fewer than 48 hours remaining.

#### External room sensor example

When the pump uses an external room sensor, enable Modbus writing and mirror the sensor as follows:

```yaml
alias: "Qvantum: Update external room temperature"
trigger:
  - platform: state
    entity_id: sensor.some_external_room_temperature
action:
  - service: number.set_value
    target:
      entity_id: number.qvantum_room_temp_external_<device_id>
    data:
      value: "{{ states('sensor.some_external_room_temperature') | float }}"
```

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


