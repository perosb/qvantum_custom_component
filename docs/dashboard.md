# Seasonal dashboard

Copy-paste recipes for a Qvantum dashboard: instantaneous and seasonal COP,
energy distribution, hot-water statistics, and heat-pump status history.
Everything uses standard Home Assistant cards unless a card is marked as HACS.

## Entity IDs and modes

The examples below use the English entity IDs for a single **Qvantum** device.
Home Assistant builds entity IDs from the translated names, so on a non-English
instance (or after a rename) yours differ — check **Developer Tools → States**
and adjust the YAML.

| Metric | Entity (English) | Unit | Cloud | Modbus |
|---|---|---|---|---|
| `powertotal` | `sensor.qvantum_total_power` | W | ✅ | ✅ |
| `heatingpower` | `sensor.qvantum_heating_power` | W | ⚠️ disabled by default, may be missing | ✅ derived |
| `dhwpower` | `sensor.qvantum_dhw_power` | W | ⚠️ disabled by default, may be missing | ✅ derived |
| `compressorenergy` | `sensor.qvantum_compressor_energy` | kWh | ✅ | ✅ |
| `additionalenergy` | `sensor.qvantum_additional_energy` | kWh | ✅ | ✅ |
| `totalenergy` | `sensor.qvantum_total_energy` | kWh | ✅ | ✅ |
| `heatingenergy` | `sensor.qvantum_heating_energy` | kWh | ✅ | ✅ |
| `dhwenergy` | `sensor.qvantum_dhw_energy` | kWh | ✅ | ✅ |
| `hp_status` | `sensor.qvantum_heat_pump_status` | — | ✅ | ✅ |
| `tap_water_cap` | `sensor.qvantum_hot_water_capacity` | showers | ⚠️ disabled by default | ✅ derived |
| `tap_water_minutes` | `sensor.qvantum_hot_water_remaining` | min | ❌ | ✅ derived |
| `bf1_l_min` | `sensor.qvantum_flow_sensor_dhw_bf1` | L/min | ✅ | ✅ |

`totalenergy` is the integration's sum of `compressorenergy` + `additionalenergy`
(the pump's electrical input). `heatingenergy` and `dhwenergy` count produced
heat, which is why they are used for COP but not as electrical consumption.

`hp_status` values:

| Value | Meaning |
|---|---|
| `0` | Idle |
| `1` | Defrosting |
| `2` | Hot water |
| `3` | Heating |
| `4` | Cooling |

Lovelace shows the labels; templates, automations and History Stats match the
raw number: `states('sensor.qvantum_heat_pump_status')` returns `"3"`. Use
`{{ state_translated('sensor.qvantum_heat_pump_status') }}` when you want the
label inside a template.

## Instantaneous COP

COP is heat out divided by electricity in:

```text
COP = (heatingpower + dhwpower) / powertotal
```

`heatingpower` and `dhwpower` are derived from the `heatingenergy` and
`dhwenergy` counter deltas, so they need the fast Modbus poll. In cloud mode the
entities are disabled by default and the API may not report them at all; enable
them in the entity settings if you see values.

Add a template sensor (merge into your existing `template:` block):

```yaml
template:
  - sensor:
      - name: "Qvantum COP"
        unique_id: qvantum_cop
        unit_of_measurement: "COP"
        state_class: measurement
        icon: mdi:heat-pump
        availability: >-
          {% set input = states('sensor.qvantum_total_power') | float(0) %}
          {% set output = states('sensor.qvantum_heating_power') | float(0)
                        + states('sensor.qvantum_dhw_power') | float(0) %}
          {{ input > 50 and output > 0 }}
        state: >-
          {% set input = states('sensor.qvantum_total_power') | float(1) %}
          {% set output = states('sensor.qvantum_heating_power') | float(0)
                        + states('sensor.qvantum_dhw_power') | float(0) %}
          {{ (output / input) | round(2) }}
```

The sensor reports only while the pump produces heat (unavailable otherwise), so
statistics are not diluted by standby draw. `powertotal` includes the electric
backup stages, while `heatingpower`/`dhwpower` are produced heat — the ratio is
the system COP, not the compressor-only COP.

Gauge card:

```yaml
type: gauge
entity: sensor.qvantum_cop
name: Current COP
min: 0
max: 6
needle: true
severity:
  red: 0
  yellow: 2
  green: 3
```

Trend card:

```yaml
type: statistics-graph
title: COP (30 days)
entities:
  - sensor.qvantum_cop
days_to_show: 30
stat_types:
  - mean
  - max
```

## Energy distribution

For a pie, install [apexcharts-card](https://github.com/RomRider/apexcharts-card)
from HACS. The card uses the latest value of each series.

Produced heat (heating vs hot water):

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Produced heat (lifetime)
chart_type: pie
series:
  - entity: sensor.qvantum_heating_energy
    name: Heating
  - entity: sensor.qvantum_dhw_energy
    name: Hot water
```

Electrical input (compressor vs backup heater):

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Electrical input (lifetime)
chart_type: pie
series:
  - entity: sensor.qvantum_compressor_energy
    name: Compressor
  - entity: sensor.qvantum_additional_energy
    name: Additional
```

Do not add `sensor.qvantum_total_energy` to either pie — it is already the sum
of the electrical slices. Swap the lifetime sensors for the season utility
meters below to show the current season instead.

Without HACS, the built-in **Energy Dashboard** has a distribution view
(**Settings → Dashboards → Energy**); the Qvantum energy sensors are ready for
it (`device_class: energy`, `state_class: total_increasing`). Add
`sensor.qvantum_total_energy` (or `compressorenergy` + `additionalenergy`) as an
individual device. Leave `heatingenergy` and `dhwenergy` out of the electrical
sections; they count produced heat.

## Hot water and tap-water draws

Modbus mode derives `tap_water_cap` (estimated showers left) and
`tap_water_minutes` (minutes left); cloud mode only reports `tap_water_cap`, and
that entity is disabled by default there.

The `dhw_shower_started` device trigger fires when `bf1_l_min` crosses
**3.0 L/min**. It also fires for dishwashers and washing machines, and a draw
with pauses can fire more than once, so treat it as "tap water draw" rather than
a perfect shower counter.

Count draws per day with a **Counter** helper: **Settings → Devices & Services
→ Helpers → Create helper → Counter**, named "DHW draws today" (entity
`counter.dhw_draws_today`).

Automation that increments the counter (replace `<qvantum-device-id>` with the
ID from the device page URL, `/config/devices/device/<id>`):

```yaml
alias: "Qvantum: Count tap water draws"
triggers:
  - trigger: device
    domain: qvantum
    device_id: <qvantum-device-id>
    entity_id: sensor.qvantum_flow_sensor_dhw_bf1
    type: dhw_shower_started
actions:
  - action: counter.increment
    target:
      entity_id: counter.dhw_draws_today
mode: single
```

Reset it every midnight:

```yaml
alias: "Qvantum: Reset daily tap-water counter"
triggers:
  - trigger: time
    at: "00:00:00"
actions:
  - action: counter.reset
    target:
      entity_id: counter.dhw_draws_today
mode: single
```

Cards:

```yaml
type: entities
title: Hot water
entities:
  - entity: counter.dhw_draws_today
    name: Tap water draws today
    icon: mdi:shower-head
  - entity: sensor.qvantum_hot_water_capacity
  - entity: sensor.qvantum_hot_water_remaining
```

```yaml
type: history-graph
title: Hot water capacity (7 days)
hours_to_show: 168
entities:
  - entity: sensor.qvantum_hot_water_capacity
```

Optional bar chart per day (apexcharts-card):

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Tap water draws per day
graph_span: 7d
span:
  start: day
  offset: "-6d"
series:
  - entity: counter.dhw_draws_today
    name: Draws
    type: column
    group_by:
      func: max
      duration: 1d
```

The `dhw_tank_low` device trigger/condition fires below **2** showers
(`sensor.qvantum_hot_water_capacity`). For a notification:

```yaml
alias: "Qvantum: Hot water is low"
triggers:
  - trigger: device
    domain: qvantum
    device_id: <qvantum-device-id>
    entity_id: sensor.qvantum_hot_water_capacity
    type: dhw_tank_low
actions:
  - action: notify.persistent_notification
    data:
      message: "Qvantum: fewer than 2 showers of hot water left."
mode: single
```

The `extra_dhw_finished` device trigger fires when
`switch.qvantum_extra_hot_water` turns off — handy for an "extra hot water is
ready" notification. See [Device automation
details](../README.md#device-automation-details) for the full list.

## Heat-pump status and defrost time

Timeline of the last week:

```yaml
type: history-graph
title: Heat pump status (7 days)
hours_to_show: 168
entities:
  - entity: sensor.qvantum_heat_pump_status
```

For "hours defrosting last 30 days" add History Stats sensors. The same helper
is available in the UI (**Settings → Devices & Services → Helpers → History
Stats**); state `1` is Defrosting and `3` is Heating.

```yaml
sensor:
  - platform: history_stats
    name: Qvantum defrosting last 30 days
    unique_id: qvantum_defrosting_last_30_days
    entity_id: sensor.qvantum_heat_pump_status
    state: "1"
    type: time
    start: "{{ now() - timedelta(days=30) }}"
    end: "{{ now() }}"
  - platform: history_stats
    name: Qvantum heating last 30 days
    unique_id: qvantum_heating_last_30_days
    entity_id: sensor.qvantum_heat_pump_status
    state: "3"
    type: time
    start: "{{ now() - timedelta(days=30) }}"
    end: "{{ now() }}"
```

```yaml
type: entities
title: Status hours (last 30 days)
entities:
  - entity: sensor.qvantum_defrosting_last_30_days
  - entity: sensor.qvantum_heating_last_30_days
```

## Monthly and seasonal energy meters

The Energy Dashboard already aggregates by month and year. Add
[utility_meter](https://www.home-assistant.io/integrations/utility_meter/)
sensors when you also want entities for cards, automations, billing, or a
seasonal COP.

```yaml
utility_meter:
  qvantum_heating_monthly:
    source: sensor.qvantum_heating_energy
    name: Qvantum heating monthly
    unique_id: qvantum_heating_monthly
    cycle: monthly
  qvantum_dhw_monthly:
    source: sensor.qvantum_dhw_energy
    name: Qvantum DHW monthly
    unique_id: qvantum_dhw_monthly
    cycle: monthly
  qvantum_total_monthly:
    source: sensor.qvantum_total_energy
    name: Qvantum total monthly
    unique_id: qvantum_total_monthly
    cycle: monthly
```

A heating season (1 October → 30 September) uses a cron reset instead of a
built-in cycle:

```yaml
utility_meter:
  qvantum_heating_season:
    source: sensor.qvantum_heating_energy
    name: Qvantum heating season
    unique_id: qvantum_heating_season
    cron: "0 0 1 10 *"   # 00:00 on 1 October
  qvantum_dhw_season:
    source: sensor.qvantum_dhw_energy
    name: Qvantum DHW season
    unique_id: qvantum_dhw_season
    cron: "0 0 1 10 *"
  qvantum_total_season:
    source: sensor.qvantum_total_energy
    name: Qvantum total season
    unique_id: qvantum_total_season
    cron: "0 0 1 10 *"
```

The utility meter keeps the source `device_class: energy` and a
`total_increasing` state class, so the seasonal sensors are Energy Dashboard
compatible as well — just don't add both a lifetime and a utility meter version
of the same energy to the same dashboard section, or it will be counted twice.

Seasonal COP:

```yaml
template:
  - sensor:
      - name: "Qvantum seasonal COP"
        unique_id: qvantum_seasonal_cop
        unit_of_measurement: "COP"
        state_class: measurement
        icon: mdi:heat-pump-outline
        availability: "{{ states('sensor.qvantum_total_season') | float(0) > 0 }}"
        state: >-
          {% set produced = states('sensor.qvantum_heating_season') | float(0)
                          + states('sensor.qvantum_dhw_season') | float(0) %}
          {% set consumed = states('sensor.qvantum_total_season') | float(1) %}
          {{ (produced / consumed) | round(2) }}
```

Reload or restart after editing YAML. `utility_meter.reset` resets a meter
manually (for example when you want to start the season at your own date).

## See also

- [Heating curve](heating-curve.md) — Auto vs User defined, registers, and the
  heating curve advisor.
- [Device automation details](../README.md#device-automation-details)