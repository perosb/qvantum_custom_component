# Heating curve

How Qvantum Auto vs User defined heating curves map to Modbus holdings and
the cloud HTTP `update_settings` command, what the official app does, and
what this integration exposes.

Source for register names and ranges: *Modbus Communication* addendum
QAD EN 2609-AXC (D100041). Behaviour below is from live dumps on one
unit (holdings 22–30 and input 35 `cal_heat_temp`), not from Qvantum
firmware source.

## Registers

| Holding | Datasheet | Range | HA entity |
|---|---|---|---|
| 22 | Curve type heating | 0 = Auto, 1 = User defined | `curve_type_heating` (select) |
| 23 | Temperature compensation curve heating | 1–50, no unit | `temp_compensation_curve` (number) |
| 24–30 | User defined heating curve @ −30…+30 °C | 10–80 °C | `curve_30` … `curve_minus_30` (numbers) |

Related, but not the Auto curve itself:

- Holding 15 — parallel offset (°C)
- Holdings 19 / 20 — max / min heating supply (°C)
- Holding 18 — outdoor temperature stop heating (°C)
- Input 35 — calculated supply temp heating (`cal_heat_temp`)

Holdings **16, 17, 21** (gaps next to the curve block) always read **0**.
They are not DUT or supply-at-DUT.

## Two UIs, two runtimes

The **app** Auto screen is DUT (°C) plus “Framledning vid DUT” (supply at
design outdoor temperature). Those two fields are **not** Modbus
registers. The app **generates** a curve from them and writes:

1. Holding **23** — discrete family 1–50
2. Holdings **24–30** — a 7-point mirror of that curve (for the graph
   and for switching to User defined)

**User defined** in the app is the same 7-point table, edited by hand.
Holding 22 selects which UI the app shows.

On the **controller**, 22 selects which table is interpolated:

| 22 | What `cal_heat_temp` follows |
|---|---|
| 0 Auto | Holding **23** (preset family). 24–30 ignored. |
| 1 User defined | Holdings **24–30**. 23 ignored. |

So Auto in firmware is a real second runtime. It does **not** keep
running leftover user-defined points when 22 is set to 0.

## Live tests

Outdoor BT1 and offset were held still within each test. Input 35 is
scale 10 (253 → 25.3 °C).

### Auto vs User defined vs Auto from HA

Same outdoor 13.2 °C. Curve number 23 stayed at 20.

| Step | 22 | 24–30 (@ −30…+30 °C) | `cal_heat_temp` |
|---|---|---|---|
| Auto (app) | 0 | 60, 58, 50, 42, 33, 20, 20 | 25.3 °C |
| User defined, raised points | 1 | 59, 57, 57, 52, 48, 55, 50 | 48.2 °C |
| Auto from HA only | 0 | **unchanged** from the row above | **25.3 °C** |

User defined matched linear interpolation of 24–30 (plus total curve
offset −2.0 °C). Switching Auto from HA flipped only 22; `cal_heat_temp`
returned to the Auto value **without** rewriting 24–30. Firmware Auto
does not read that table.

### Holding 23 only (Auto, 22 = 0)

Same outdoor 15.5 °C. **24–30 identical** in all three dumps.

| 23 | `cal_heat_temp` |
|---|---|
| 17 | 21.7 °C |
| 20 | 22.4 °C |
| 25 | 23.5 °C |

Monotonic in 23, about +0.2 °C per step at this outdoor temperature
(near min supply 20 °C and stop-heating 15 °C). Interpolating the
cached 24–30 would have given ~25.9 °C in every dump.

### Holding 23 only (User defined, 22 = 1)

Same 24–30 as the Auto-23 test (60, 58, 50, 42, 33, 20, 20). Outdoor
15.7–15.8 °C.

| 23 | BT1 | `cal_heat_temp` |
|---|---|---|
| 15 | 15.8 °C | 23.4 °C |
| 20 | 15.7 °C | 23.5 °C |
| 25 | 15.8 °C | 23.4 °C |

No trend with 23 (15 → 25 is 0.0 °C; the 0.1 °C bump is the 0.1 °C
colder outdoor). Linear interpolation of 24–30 at 15.8 °C, minus total
offset −2.0 °C, is **23.5 °C** — matches `cal_heat_temp`.

In User defined, **23 is ignored**. Contrast Auto at 15.5 °C, where
23 = 25 gave 23.5 °C and 23 = 17 gave 21.7 °C.

### App DUT / supply-at-DUT still rewrite 23 and 24–30

Changing DUT −16 → −18 °C (app Auto): 23 **17 → 16**, and 24–28 dropped
2–3 °C (flatter curve: same supply at a colder DUT).

Changing supply-at-DUT 50 → 55 °C: 23 **17 → 20**, points rose.
Interpolating 24–30 at DUT −16 °C recovered ~49.6 °C then ~54.8 °C, so
the 7-point cache **is** that generated curve. Auto control still uses
**23**; 24–30 is a side dump.

The coldest point often sits on max supply (holding 19, 60 °C here).

## Cloud HTTP

The app `update_settings` payload includes `curve_type_heating`,
`ud_curve_minus30` … `ud_curve_30`, plus Auto-only fields we do **not**
write yet (`guide_tdot` DUT, `guide_sdot` supply-at-DUT, `p_heating`,
`min_supply`, `max_supply`, `guide_he`).

This integration sends **`curve_type_heating` and `ud_curve*`** only.
Those names are in `REQUIRED_METRICS` so cloud `/values` fetches them on
every poll (otherwise platforms never create the entities). Poll aliases
`ud_curve_minus30` → canonical `curve_minus_30` so the same entities work
in cloud and Modbus.

Cloud Auto still cannot set DUT; switching 22 to Auto uses whatever
family/DUT the pump already has. User defined writes the seven points
with the app key names (`ud_curve_minus10`, no underscore before the
number).

## What this integration does

- **Select `curve_type_heating`** — Auto / User defined. Modbus writes
  holding 22; cloud sends `curve_type_heating`.
- **Number 23** (`temp_compensation_curve`) — Auto family 1–50, **Modbus
  only**. Not DUT. Only **available** when 22 is Auto.
- **Numbers 24–30** — named `Heating curve N: T°C` in outdoor-temp order
  matching the app list (+30 °C down to −30 °C). Only **available** when
  22 is User defined. Cloud writes `ud_curve*`. The type select exposes
  `points: [[outdoor, supply], …]` for Lovelace charts.
- DUT and “Framledning vid DUT” are **not** entities.

Writing 23 from HA **does** change Auto `cal_heat_temp` on Modbus. It is
not the same as the app’s DUT fields. HA cannot reproduce the DUT
generator.

## Custom curve control

**Modbus-only** and requires the Modbus write option. The module freezes the
pump's current seven-point table (holding 24–30) as the baseline and computes
**one** shared adjustment in °C, applied to all seven points:

| Term | Source |
|---|---|
| outdoor | Open-Meteo hourly temperature trend over the next hours (never instantaneous BT1), damped |
| night/day | local sunrise/sunset (Home Assistant location); amplitude is a fraction of the forecast's diurnal supply swing (curve slope × outdoor range), bounded |
| solar | self-calibrating `a`/`b`/`trust` model from recorder statistics (`heatingpower`, indoor, outdoor) plus Open-Meteo GHI history; gain in W → °C through the local baseline slope |
| load | one-sided reduction when measured heating power is below the model demand at the target indoor temperature |

Indoor deviation from the target is a **cap** only (a warm house blocks upward
adjustment, a cold house blocks downward), it never drives a term. Points are
clamped to 10–80 °C and fall toward warmer outdoors.

The frozen baseline is the app's seven-point table, but that table is only a
side dump: on Auto the firmware follows holding 23, so it can diverge from what
the pump actually delivers. While the pump is on Auto the coordinator corrects
the baseline from observed `(BT1, cal_heat_temp)` hours (heating hours only,
from recorder statistics), fitting the residual `observed − interpolated` and
keeping the cached curve's shape beyond the observed outdoor range. The
correction self-stabilises: once it matches, the residual falls under the noise
threshold and nothing more is written. This is why `ready` can become true even
when the cached table initially disagrees with Auto.

Entities:

- `sensor.qvantum_custom_curve_01_30` … `sensor.qvantum_custom_curve_07_minus_30` —
  the computed shadow points, numbered 1–7 (+30 … −30) like the pump's own
  curve numbers so they sort together, with `baseline` and `adjustment`
  attributes.
- `sensor.qvantum_custom_curve_adjustment` — the shared adjustment, with
  `outdoor_c`, `night_day_c`, `solar_c`, `load_c` attributes.
- `sensor.qvantum_custom_curve_deviation` — computed supply at the measured
  outdoor minus `cal_heat_temp` (input 35), with `shadow`, `ready`, `blocker`
  and `baseline_auto` (baseline frozen while the pump was on Auto) attributes.
- `sensor.qvantum_custom_curve_solar_model` — model trust in %, with
  `a_w_per_k`, `b_m2`, `b_std_err`, `r2_opaque` and `r2_solar` diagnostics.
- `switch.qvantum_custom_curve_control` — off = shadow, on = active writing.
  `qvantum.set_curve_control` with `mode: shadow|active` does the same from an
  automation.

### Activation and reversion

The switch never turns itself on. `ready` becomes true after at least three
full local days of deviation statistics with ≥ 90 % coverage, a median
absolute deviation ≤ 1.0 °C and no hourly deviation beyond 3.0 °C. The
`blocker` attribute names a missing signal (`baseline`, `forecast`, `history`,
`window`, `median`, `max`). `ready` is a signal, not an activation.

Turning the switch on:

1. writes holding 24–30 while 22 is still Auto (firmware ignores the table, so
   a failed point changes nothing)
2. writes the parallel offset (holding 15) to 0
3. reads back and verifies all seven points
4. only then writes holding 22 to User defined

Active cycles write only points that changed by ≥ 1 °C, one at a time with a
pause. Any failed write, an out-of-range or non-monotone table, or a lost
connection writes holding 22 back to Auto and turns the switch off. If the
pump is instead moved off User defined, the coordinator returns to shadow
without touching holding 22. A revert that cannot be written is latched and
retried, so a lost connection cannot leave the integration claiming control.

**One writer:** disable Home Assistant automations that write the curve offset
(holding 15) while the switch is on, otherwise solar gain is applied twice.

### Solar model

The fit uses recorder long-term statistics (`heatingpower`, outdoor and indoor
temperature, 60 days hourly) plus Open-Meteo GHI history, refreshed daily.
`a` (W/K), `b` (m²) and a continuous trust in [0, 1] from `b`'s t-statistic
are computed from your house's data — nothing is hand-tuned. Weak evidence
keeps the solar term tiny automatically, and GHI is smoothed with an
exponential kernel so one cloud cannot swing the curve.

## Practical notes

- After Auto from HA, 24–30 may still show the last User defined (or
  last app-generated) table. That cache is stale for control until the
  app regenerates it or you edit points in User defined.
- After User defined, switching Auto in HA immediately uses 23, even if
  the point numbers look unchanged.
- Do not map 23 to DUT or to a temperature unit. Datasheet range is 1–50
  with unit “-”.
