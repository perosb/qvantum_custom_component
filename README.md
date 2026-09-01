[![qvantum_custom_component](https://img.shields.io/github/release/perosb/qvantum_custom_component/all.svg?label=current%20release)](https://github.com/perosb/qvantum_custom_component) [![downloads](https://img.shields.io/github/downloads/perosb/qvantum_custom_component/total?label=downloads)](https://github.com/perosb/qvantum_custom_component) [![codecov](https://codecov.io/gh/perosb/qvantum_custom_component/graph/badge.svg)](https://codecov.io/gh/perosb/qvantum_custom_component)

## Qvantum Heat Pump Integration for Home Assistant

This integration supports two connection modes for your Qvantum heat pump:

- **Cloud mode (HTTP)**: Connects through your Qvantum account and cloud API to read live metrics, firmware details, SmartControl, and cloud-based settings.
- **Modbus mode (offline/local)**: Connects directly to the heat pump on your local network over Modbus TCP, without requiring a Qvantum account or cloud session.

Choose the mode that matches your setup:
- Use **Cloud mode** for the full cloud-integrated feature set and account-based controls.
- Use **Modbus (offline/local)** when you want local, direct access to the heat pump, faster local polling, or to keep everything running without cloud connectivity.

> [!CAUTION]
>Cloud mode uses the same internal API that the Qvantum app uses to fetch live metrics from the heat pump.  
>This is a cloud-based integration and should be considered experimental and at your own risk.  

> [!WARNING]
>Cloud mode only: the Elevate Access feature creates a "Remote Service" access for your user.  
>It effectively grants service/installer-level access to the heat pump.  
>This is required for some advanced cloud-based settings and maintenance actions.  

> [!IMPORTANT]
>Modbus mode is local and offline. It does not use the cloud API.
>When you enable Modbus write mode, you are directly writing values to the heat pump.  
>Only enable writing if you understand the consequences. Incorrect or out-of-range values may affect performance, warranty, and the system lifecycle.  

### Transform Your Home's Energy Efficiency with Qvantum

Discover the power of intelligent home climate control with the Qvantum Heat Pump integration for Home Assistant. Seamlessly monitor and control your Qvantum heat pump directly from your smart home dashboard, giving you unprecedented insight into your energy usage and system performance.

**Why choose this integration?**
- **Complete Control**: Monitor temperatures, energy consumption, and system status in real-time
- **Smart Automation**: Create automations based on heat pump data for optimal comfort and efficiency
- **Energy Insights**: Track daily energy usage and optimize your heating costs
- **Professional Integration**: Built with reliability and performance in mind for Qvantum systems
- **Easy Setup**: Install via HACS with just a few clicks

### Energy for all – without compromises

Our needs, lifestyles and ways of working have changed rapidly. Demands on our standard of living have skyrocketed, but how will we make the resources last?

Disrupting the ordinary takes courage, but with experience, deep knowledge and determination, we have the power to change everything. We have to break free from the past with technology for the future. To focus on values and experience. To give access to millions of homes, to be part of the energy transition without sacrificing their livelihood or their comfort.

### Installation

Requires Home Assistant **2026.9** or newer (shared Modbus connection).

1. **Install via HACS** (recommended):
   - Search for "Qvantum Heat Pump" in HACS
   - Install the Qvantum Heat Pump integration
   - Restart Home Assistant

2. **Manual Installation**:
   - Download the latest release
   - Extract to `custom_components/qvantum/`
   - Restart Home Assistant

3. **Setup**:
   - Go to Settings → Devices & Services → Add Integration
   - Search for "Qvantum Heat Pump"
   - Choose **Qvantum cloud (HTTP)** or **Local Modbus (offline)**

Only one Qvantum instance can be configured. Use **reconfigure** to switch between cloud and local later.

#### Qvantum cloud (HTTP)

Sign in with your Qvantum account email and password. Metrics, firmware, SmartControl, elevate-access, and most settings use the cloud API.

#### Local Modbus (offline)

Reads the heat pump on your LAN. No login and no cloud session.

1. In the Qvantum app, enable **Modbus external** (Installer → Service mode → Connectivity).
2. Enter host (default `Qvantum-HP`), port, unit ID, and poll interval (default 15 seconds, minimum 5).
3. Home Assistant probes serial and firmware from identity registers 180–193.

Cloud-only sensors (firmware boards and access expiry) are not created in this mode. Extra-DHW `tap_stop` is created from the local restore deadline.

### Features

- **Real-time Monitoring**: Temperature sensors, pressure readings, energy consumption
- **System Control**: Adjust operation modes, set temperatures, control ventilation
- **Energy Analytics**: Daily and total energy usage tracking
- **Smart Status**: Heat pump status, defrost cycles, priority modes
- **Comprehensive Coverage**: Supports all major Qvantum heat pump parameters

### Local Modbus

- **Offline**: no Qvantum account, no HTTP fallback
- **Faster polling** of live data from the pump’s Modbus interface (default 15 s)
- **Extra local metrics**: heating/DHW power, tap-water capacity estimate, compressor state, extra demand relays
- **Optional writes** for supported holding-register controls when you enable **Enable writing via Modbus**

> [!IMPORTANT]
> Local Modbus is **read-only** until you enable writing. Writes never go to the cloud API.
>
> Supported local writes include indoor target/offset, DHW start/stop, extra DHW, fan preset, operation/manual switches, room compensation, fan speeds, extra-DHW stop, external room temperature, and indoor sensor source.
>
> **SmartControl** (`use_adaptive`, `enable_sc_sh`, `enable_sc_dhw`) and **elevate-access** stay cloud-only. They are unavailable in local mode.
>
> By enabling Modbus writing you accept full responsibility for values written to the pump. Incorrect or out-of-range values may void the warranty and/or affect lifecycle and performance.

Extra DHW on local Modbus writes DHW mode Extra/Normal. Use the extra-DHW button (60 minutes) or `qvantum.extra_hot_water` with `minutes` (0–480). Home Assistant restores Normal when the timer ends, including after a restart. The extra-DHW timer entity (`tap_stop`) shows the end time. The extra switch without a duration stays Extra until you turn it off.

Fan extra on local Modbus is a sticky preset. Cloud extra ventilation is a timed boost.

### External room sensor support

When the pump is configured to use an external room sensor (`use_operation_sensor == 4`), the integration exposes a `number` entity for `room_temp_external`.
This allows you to mirror an external temperature sensor into the pump’s control setpoint via Modbus when Modbus writes are enabled.

**Example automation:**
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

### Services

The integration provides the following services for advanced control and testing:

#### `qvantum.extra_hot_water`
Schedule extra hot water production for a specified duration. On cloud this uses the HTTP extra-DHW command. On local Modbus it writes DHW Extra/Normal (requires Modbus writing).

**Parameters:**
- `device_id` (integer, required): The heat pump id (same as the serial)
- `minutes` (integer, optional, default: 120): Duration in minutes (0-480)

**Example:**
```yaml
service: qvantum.extra_hot_water
data:
  device_id: 123
  minutes: 60
```

### Elevate Access Button

Cloud HTTP only. The **Elevate Access** button grants temporary elevated permissions to access advanced heat pump settings and maintenance functions. It is not created in local Modbus mode.

**Features:**
- Temporarily elevates access level for configuration tasks
- Automatically expires after a set time period
- Includes expiration timestamp sensor for monitoring

**Entities:**
- `button.qvantum_elevate_access_<device_id>` - Press to elevate access
- `sensor.qvantum_expires_at_<device_id>` - Shows when access expires

**Auto-renewal automation:**
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

*Qvantum Controls in Home Assistant:*  
![image](https://github.com/user-attachments/assets/3b04bf83-3f1a-45d8-9aad-fdcb780abc9b)

*Daily energy usage of Qvantum Heat Pump:*   
![image](https://github.com/user-attachments/assets/4f2f58f8-eae2-4a72-a2e8-b8468f869da4)
