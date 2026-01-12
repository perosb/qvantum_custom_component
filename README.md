[![qvantum_custom_component](https://img.shields.io/github/release/perosb/qvantum_custom_component/all.svg?label=current%20release)](https://github.com/perosb/qvantum_custom_component) [![downloads](https://img.shields.io/github/downloads/perosb/qvantum_custom_component/total?label=downloads)](https://github.com/perosb/qvantum_custom_component) [![codecov](https://codecov.io/gh/perosb/qvantum_custom_component/graph/badge.svg)](https://codecov.io/gh/perosb/qvantum_custom_component)

## Qvantum Exhaust Air Heat Pump Integration for Home Assistant


> [!CAUTION]
>This custom component uses the same internal API for all metrics that is also used by the app.  
>It pulls live metrics from FLVP.  
>Use at your own risk ;)  

> [!WARNING]
>The Elevate access rights works by creating a "Remote Service" access to your user.  
>Essentially it gives you service tech/installer access.  
>This is apparently required for us to change some settings on our own pumps.  

### Transform Your Home's Energy Efficiency with Qvantum

Discover the power of intelligent home climate control with the Qvantum Exhaust Air Heat Pump integration for Home Assistant. Seamlessly monitor and control your Qvantum heat pump directly from your smart home dashboard, giving you unprecedented insight into your energy usage and system performance.

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
   - Sign in using your Qvantum account email and password

The integration will automatically discover your Qvantum devices and create comprehensive sensors for all supported metrics.

### Features

- **Real-time Monitoring**: Temperature sensors, pressure readings, energy consumption
- **System Control**: Adjust operation modes, set temperatures, control ventilation
- **Energy Analytics**: Daily and total energy usage tracking
- **Smart Status**: Heat pump status, defrost cycles, priority modes
- **Comprehensive Coverage**: Supports all major Qvantum heat pump parameters

### Services

The integration provides the following services for advanced control and testing:

#### `qvantum.extra_hot_water`
Schedule extra hot water production for a specified duration.

**Parameters:**
- `device_id` (integer, required): The device ID to control
- `minutes` (integer, optional, default: 120): Duration in minutes (0-480)

**Example:**
```yaml
service: qvantum.extra_hot_water
data:
  device_id: 123
  minutes: 60
```

### Elevate Access Button

The **Elevate Access** button grants temporary elevated permissions to access advanced heat pump settings and maintenance functions.

**Features:**
- Temporarily elevates access level for configuration tasks
- Automatically expires after a set time period
- Includes expiration timestamp sensor for monitoring

**Entities:**
- `button.qvantum_elevate_access_<device_id>` - Press to elevate access
- `sensor.qvantum_expires_at_<device_id>` - Shows when access expires

**Auto-renewal automation:**
```yaml
automation:
  - alias: "Auto-Elevate Access"
    trigger:
      - platform: time_pattern
        hours: "9"
    condition:
      - condition: template
        value_template: >
          {% set expire_time = as_datetime(states('sensor.qvantum_expires_at_test_device_123')) %}
          {{ expire_time is not none and (expire_time - now()).days < 1 }}
    action:
      - service: button.press
        target:
          entity_id: button.qvantum_elevate_access_test_device_123
```

*Qvantum Controls in Home Assistant:*  
![image](https://github.com/user-attachments/assets/3b04bf83-3f1a-45d8-9aad-fdcb780abc9b)

*Daily energy usage of Qvantum Heat Pump:*   
![image](https://github.com/user-attachments/assets/4f2f58f8-eae2-4a72-a2e8-b8468f869da4)
