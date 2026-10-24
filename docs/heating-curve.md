# Heating curve

How Qvantum Auto vs User defined heating curves map to Modbus holdings and
the cloud HTTP `update_settings` command, what the official app does, and
what this integration exposes.

Source for register names and ranges: *Modbus Communication* quick-start
guide QSG EN 2613-A (D100041). Behaviour below is from live dumps on one
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

## Adaptive curve control

**Modbus-only** and requires the Modbus write option. The module freezes the
pump's current seven-point table (holding 24–30) as the baseline and computes
**one** shared adjustment in °C, applied to all seven points:

| Term | Source |
|---|---|
| outdoor | Open-Meteo hourly temperature trend over the next hours (never instantaneous BT1), damped |
| night/day | local sunrise/sunset (Home Assistant location); amplitude is a fraction of the forecast's diurnal supply swing (curve slope × outdoor range), bounded |
| solar | self-calibrating `a`/`b`/`trust` model from recorder statistics (`heatingpower`, indoor, outdoor) plus Open-Meteo GHI history, identified on **day blocks** (see [Solar model](#solar-model)); gain in W → °C through the local baseline slope |
| load | one-sided reduction when measured heating power is below the model demand at the target indoor temperature |
| optional | COP feedback and/or cold-snap pre-charge, **off by default** — see [Optional terms](#optional-terms) |

Indoor deviation from the target is a **cap** only (a warm house blocks upward
adjustment, a cold house blocks downward), it never drives a term. Points are
clamped to 10–80 °C and fall toward warmer outdoors.

The frozen baseline is the app's seven-point table, but that table is only a
side dump: on Auto the firmware follows holding 23, so it can diverge from what
the pump actually delivers. While the pump is on Auto the coordinator corrects
the baseline from observed `(BT1, cal_heat_temp)` hours (heating hours only,
after the last time active control ended, from recorder statistics), fitting
the residual `observed − interpolated` and keeping the cached curve's shape
beyond the observed outdoor range. The
correction self-stabilises: once it matches, the residual falls under the noise
threshold and nothing more is written. This is why `ready` can become true even
when the cached table initially disagrees with Auto.

Room compensation is a second, separate loop the shadow curve does not model:
with BT2 or an external room sensor the firmware adds a room term to
`cal_heat_temp`, so at a constant outdoor temperature the supply moves with the
indoor error (`cal_heat_temp = table − factor × (room − target)`). The
coordinator subtracts `gain × (pump room − target)` from the deviation before
it feeds the readiness window; without that, the shadow curve could never match
a compensating pump closely enough to become `ready`.

**Which room value.** In the external-sensor mode (`sensor_mode` = 4) the
firmware does not use the external value on its own: it averages it with its own
BT2. Measured on a QE-6, 2026-10-21, the implicit room value behind
`cal_heat_temp` matched that mean with a median deviation of 0.10 °C, against
0.32 °C for the external sensor alone and 0.30 °C for BT2. `pump_room_temperature`
reproduces it (mean in the external mode, the configured sensor everywhere else —
the regime it was measured in), and `pump_room_c` publishes the value used.
Removing a term built from one sensor while the pump built it from the mean
leaves a constant `gain · (BT2 − configured) / 2` bias: 1.7 °C on that install,
more than the readiness median gate, which is why the median sat at 2.17 °C with
a raw 1.43 °C underneath. The trims, the indoor cap and `room_error` stay on the
configured sensor — those describe the house, this describes the pump's input.

The blend is only used while BT2 is a probe the pump can read. An installation
without an interior sensor keeps the firmware's placeholder there — a QE4 owner
reported a fixed −30 °C (2026-10-23) — and the pump's own room value then comes
from the external sensor alone. Averaging that placeholder in would put the
removed term about 26 K off (`gain · 26 K` on the deviation, which is 13 K even
at the minimum gain), so readiness could never pass. Three gates apply: the
datasheet's `BT2 Detected` register (input 86: `0=No`, `1=Yes`,
`2=Detecting` — read over Modbus only) must report `1`; the value must be a room
temperature and within 10 K of the configured reading (measured on a live QE-6
over 30 days, the two sensors were 0.54 K apart at the median and 0.85 K at
most, so the bound only catches a placeholder or a stuck probe); and the
resulting value is checked again, because in the modes without an indoor sensor
the configured reading falls back to BT2. Without a usable BT2 the configured
sensor is used alone, or nothing at all — `pump_room_source` says which
(`blended`, `configured`, `bt2` or `none`).

**The gain is the pump's own setting.** `room_comp_factor` (holding 13, cloud
setting of the same name) *is* that factor, so it is read rather than fitted:
measured on a live pump, 5.0 configured against 5.08 fitted from active hours.
A +1.1 K step on the indoor target moved `cal_heat_temp` by 5.5 K, which is the
same 5.0 seen from the other side.
An absent, zero, non-finite or out-of-range factor, or a `sensor_mode` with no
indoor reading at all, means **no** correction — never a guessed number. A
factor that *becomes* unusable (the user zeroes it) drops the gain it set
rather than leaving it applied: while the curve is active the fit never runs,
so a stale number would stay wrong for as long as the curve does. Otherwise the
gain falls back to a fit over observed Auto hours (residual against both
outdoor and the room error), which is what an installation without a usable
factor still gets. That fallback is **not** a re-learning step you have to
schedule: the applied gain is the setting as last seen, compared on every poll,
so changing `room_comp_factor` from 5 to 3 takes effect within one cycle and
needs no re-identification. The fit's window (the curve off, holding 22 on Auto,
24 heating hours since active control ended, room error moved ≥ 0.3 °C) gates the
fallback and the diagnostics-only `fitted` slope — nothing that is applied while
a factor exists. Because the *current* gain is applied retroactively to every
hour of the readiness window, a factor change moves the reported `deviation` and
`median_abs_c`; that is the correction following the pump, not a reset.
Whether configured or fitted, the gain **travels with the indoor sensor it
belongs to**: if the payload reports a different sensor the correction is
skipped rather than subtracting one sensor's room term from another's error. It
is persisted and exposed as `room_gain_c_per_c` on the deviation sensor, with
the pump's raw setting beside it as `room_gain_factor` in the diagnostics.

**How that relation was measured.** Three reversible steps on the live pump
(2026-10-21), each read back within one poll cycle:

| Step | Change | `cal_heat_temp` | Says |
|---|---|---|---|
| `curve_10` 27 → 29 | +2 K on the written table | 27.7 → 29.6 (+1.9) | the table we write is followed 1:1 |
| target 20.3 → 21.4 | +1.1 K room error | 27.0 → 32.6 (+5.5) | gain 5.0/K, the pump's own factor |
| target back to 20.3 | −1.1 K | 32.6 → 27.1 | reversible, no hysteresis |

Two things this rules out, both worth not re-investigating: the curve is
**not** evaluated on a filtered outdoor temperature (a moving average of BT1 over
1–12 h was fitted as the pump's input; 1 h fit best with a residual of 0.49 °C
against 1.26 °C at 4 h), and the write path is not dropping or clamping points
(holdings 24–30 read back exactly what was written, and the `number` entities
mirror them). A gap between the table and `cal_heat_temp` is therefore a
room-term or settings question, not a curve one.

When fitting anything against `cal_heat_temp`, **include only hours after
`active_since_ts`**: on Auto hours the pump runs its own curve, which mixes two
different control laws into one regression and flattened the room gain to 3.2
instead of 5.0.

**It only applies to hours the pump followed our table.** The correction is
valid where `cal_heat_temp` came from the table we wrote — from the start of the
last active period (`active_since_ts`) until it ended (`last_active_ts`). On
Auto hours the pump's own control already holds the room term and the learned
baseline was fitted to it, so subtracting the gain again double-counts: on the
live pump, applying it to every readiness hour moved the median from 1.17 °C to
2.67 °C (failing the gate) while the active hours alone gave 1.08 °C (passing).
Readiness therefore no longer inherits the term from Auto hours, and a pump that
has never been activated is corrected nowhere — which is exactly its behaviour
today. A store written before `active_since_ts` existed (an upgrade, or an
activation this process never saw) stamps the moment of that load instead of
guessing when control began: the hours before it stay uncorrected and the
three-day window rolls clean.

### Active trims

While the curve is active the module also learns slow **per-point trims** from
the indoor error: hourly `(BT1, indoor − target)` from recorder statistics is
bucketed by outdoor temperature and converted to supply degrees through the
local curve slope, with recency weighting. Only heating hours enter, like the
baseline fit — while the pump makes hot water or idles the rooms cool without
the curve being wrong, and those hours would push every trim up. `indoor` is the sensor the pump is
configured to use — BT2, or the fed external room temperature. In Modbus mode
the fed value (holding 14) is also exposed as a diagnostic sensor, because
long-term statistics exist only for sensors; that is what the trims and the
room-gain fit read when the pump runs an external room sensor, not BT2. A
freshly configured sensor has no recorder history for its first hours, so the
indoor resolver falls back to the next candidate (BT2 last) until it has
≈ a day of hourly rows — otherwise the trims, room-gain fit, solar calibration
and the building metrics would all go dormant on a sensor change. A candidate
only qualifies while its series is a record of a room: BT2 is skipped when the
pump reports no probe, and any series whose hourly median lies outside
−10…45 °C is skipped too. Without that, an installation with no interior sensor
would fall back to a BT2 series that records −30 °C — a −50 K room error puts
the trims on their clamp and inflates the heat-loss coefficient, the time
constant and the solar aperture. No series beats a plausible wrong one. A reference
point with no observed hours within 7.5 °C stays at 0 — no evidence, no
correction — each trim is capped at ±2 °C and updated with a damped step, so it
converges and stops when the error is gone. Updates are at most once a day once
any trim exists; until then a shorter cadence applies, because the hours that
justify the first correction are already in the window. Trims
are only applied while active (in shadow the baseline learning owns the curve
shape) and are cleared
on reversion or when the baseline is re-learned. This is what keeps a
mid-autumn activation working through winter: the part of the curve October
could not observe is corrected by the weather the house actually sees.

Computed points are clamped to the pump's own min/max heating supply (holding
19/20) when those are known, not just the register range 10–80 °C, so a point
is never written above what the firmware will actually use. A knot the baseline
already sits on at that floor — the warm end, after the floor lift — cannot
express a request below it; that is reported as `floor_pinned` (structural,
nothing to adjust) rather than `clamped` (a demand the baseline point should
move to answer).

Entities:

- `sensor.qvantum_adaptive_curve_01_30` … `sensor.qvantum_adaptive_curve_07_minus_30` —
  the computed shadow points, numbered 1–7 (+30 … −30) like the pump's own
  curve numbers so they sort together, with `baseline`, `adjustment`, `trim`
  and `evidence_hours` attributes. `evidence_hours` is how many hours the trim
  window holds near that outdoor temperature: **0 means the trim is 0 on
  purpose** — no evidence, no correction — rather than because the curve is
  already right.
- `sensor.qvantum_adaptive_curve_adjustment` — the shared adjustment, with
  `outdoor_c`, `night_day_c`, `solar_c`, `load_c`, `cop_c`, `precharge_c`,
  `trims`, `clamped`, `clamped_points` and `floor_pinned_points` attributes.
- `sensor.qvantum_adaptive_curve_deviation` — computed supply at the measured
  outdoor minus `cal_heat_temp` (input 35), with `shadow`, `ready`, `blocker`
  and `baseline_auto` (baseline frozen while the pump was on Auto) attributes,
  plus `median_abs_c`, `p95_abs_c`, `max_abs_c` and `window_hours` from the
  readiness window, `baseline_learned_hours`, `baseline_outdoor_min_c`,
  `baseline_outdoor_max_c` from the last baseline fit and `room_gain_c_per_c`
  (the pump's room-compensation gain — its own `room_comp_factor` when it
  reports one — removed from the deviation while the pump follows our table).
  `extrapolated_points` lists the curve points outside that observed range:
  their supply comes from the cached table rather than from anything the pump
  was watched doing, so readiness says nothing about them yet. `pump_room_c` is
  the room value the pump's own compensation term was computed from — in the
  external-sensor mode the firmware averages the external value with BT2, so
  that is not the same as the configured sensor's reading and the deviation
  would carry a constant bias if the term were removed from ours — and
  `pump_room_source` says which input produced it (`blended`, `configured`,
  `bt2` or `none`), so a figure built without the blend (an unconnected BT2
  reports −30 °C) is recognisable in the same attributes.
  The `ready`/`blocker` attributes describe the baseline-vs-pump window, not
  this sensor's own value. Diagnostic entity, enabled by default.
  A failed forecast refetch does not immediately turn `blocker` into
  `forecast`: the previous hourly payload stands while it still covers the
  current hour and is under `FORECAST_REUSE_HOURS` old, because Open-Meteo
  answers a small fraction of requests with a 503 well inside its quota and the
  payload barely moves between two 15-minute cycles. `forecast_reused` in the
  config-entry diagnostics says whether the last cycle served that cached
  payload.
- `sensor.qvantum_adaptive_curve_room_error` — the **outcome**: the median
  `|indoor − target|` over the readiness window, with the signed `median_c`,
  `p90_abs_c`, `samples` and the `source` indoor metric as attributes. Readiness
  compares our baseline with the pump, which says nothing about the house once
  the curve is active; this measures whether the rooms actually follow the
  target, on whichever indoor sensor the trims are reading. Diagnostic entity.
- `sensor.qvantum_adaptive_curve_solar_model` — the **applied** trust in %
  (the day-block model's when it is valid, otherwise the hourly one), with the
  hourly coefficients (`a_w_per_k`, `b_m2`, `b_std_err`, `r2_opaque`,
  `r2_solar`) and a `model_daily` block carrying the day-block `a`, `b`, `t`,
  `r2`, `n_days`, the two window halves, the jackknife range and the gate
  `notes` that explain why a term is missing. Diagnostic entity.
- `switch.qvantum_adaptive_curve_control` — off = shadow, on = active writing.
  `qvantum.set_curve_control` with `mode: shadow|active` does the same from an
  automation.

### Activation and reversion

The switch never turns itself on. `ready` becomes true when the **learned
baseline** — not the adjusted shadow — reproduces the pump over at least three
full local days: a median absolute deviation ≤ 1.5 °C and a 95th percentile
≤ 3.0 °C, over heating hours only (`heatingpower` above the same floor the
baseline fit uses). Idle hours sit at the min-supply clamp and no longer
describe the curve, and the module's own outdoor / night-day / solar / load
terms are deliberate — judging them against Auto would block activation
forever. The room-compensation term the firmware adds is removed first, on the
hours the pump followed our table, using the pump's own `room_comp_factor`.

The gates are calibrated to what a ten-degree seven-point table can
represent: even a converged baseline leaves roughly 1 °C of scatter across the
observed outdoor range, so a tighter median gate is unreachable by
construction. Readiness says nothing about the house once the curve is active,
where the pump follows our own table; `sensor.qvantum_adaptive_curve_room_error`
is the outcome signal to watch instead. It also only covers the outdoor range
the baseline was observed at — on a mild autumn that can be a handful of points
around 0–15 °C, while the cold end is extrapolated from the cached table. The
trims correct those points once the weather gets there, which is why a winter
curve should be watched rather than assumed. The 95th percentile replaces an absolute maximum because single
mode-switch hours are transients, not a curve error. The
`blocker` attribute names a missing signal (`baseline`, `forecast`, `history`,
`window`, `median`, `max`). A transient forecast failure counts as missing only
when no payload covering the current hour is still cached, so a sporadic
Open-Meteo 503 does not collapse the window. `ready` is a signal, not an
activation. `ready` needs the recorder running with ≥ 90 % window coverage and
at least 24 heating hours: without hourly statistics the window stays
incomplete and `blocker` is `window`. The deviation sensor is a Diagnostic entity and is enabled by
default; it is informational and no longer feeds the readiness window.

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
Optional terms (`qvantum.set_curve_terms`) are persisted with the rest of the
curve state and default to off.

**One writer:** disable Home Assistant automations that write the curve offset
(holding 15) while the switch is on, otherwise solar gain is applied twice.
Reversion (active → shadow) writes holding 22 back to Auto but does **not**
restore the parallel offset; leave the offset-writing automations off until
they are deliberately re-enabled.

**Removing the integration** reverts holding 22 to Auto, so the pump is not
left on a User-defined table with nothing to supervise it (the same guard that
runs when Modbus writes are turned off). This happens only on removal — a
reload, and Home Assistant shutting down, deliberately leave the written table
in place so the pump keeps heating with the last curve while nothing is
running.

### Optional terms

Control-loop terms beyond the four forecast terms are **off by default** and
only act while the curve is active. Enable them with
`qvantum.set_curve_terms` (persisted in the curve Store):

- **COP feedback** (`cop_feedback: true`) reduces the shared adjustment, by
  at most 1 °C, when the instantaneous heating COP falls below a learned
  reference for the current outdoor temperature bucket (5 °C buckets, EMA).
  The reference needs at least 12 samples in the bucket before the term may
  act; it is learned from `cop_heating` whenever the term is not correcting
  and frozen during a correction so the loop has a stable target. The term is
  one-sided downward, applies only while the pump is heating, and never
  fights the indoor cap or the total clamp. Freezing the reference only
  affects the COP loop; the baseline shape learning is untouched.
- **Cold-snap pre-charge** (`precharge: true`) raises the shared adjustment,
  by at most 1.5 °C, when the forecast drops at least 4 K within the next 8
  hours (the minimum over that horizon, not the last hour). It is one-sided
  upward and skipped when the previous cycle was already capped or at the
  positive total clamp; the indoor cap remains the final veto for a warm
  house and the pump's max supply bounds every point. The cap needs a **fresh
  indoor reading** to act at all: it is fed from the room error and keeps 24 h
  of samples, so without an indoor sensor (`sensor_mode` DISABLED / BT3 / AUX)
  it is inert and nothing brakes the curve in a warm house. The diagnostics
  report `indoor_cap_armed` (and `indoor_margin_c`) so that state is visible
  rather than inferred from a warm room.

### Solar model

The fit uses recorder long-term statistics (`heatingpower`, outdoor and indoor
temperature, 60 days hourly) plus Open-Meteo GHI history, refreshed daily. The
same requests also carry 10 m wind (`wind_speed_10m`, in m/s rather than the
API's default km/h), and the day blocks hold it as `Σ(v·ΔT)` — the form wind
takes physically, since it multiplies the envelope loss instead of shifting it.
The column is fitted as a **fourth regressor** of the same daily balance —
`Q = a·ΣΔT + w·Σ(v·ΔT) + 24c − b·ΣGHI`, because wind multiplies the envelope
loss rather than shifting it — and only where the three-column balance is
already valid. A payload without wind produces the same days with
`wind_hours = 0`, because "not measured" is not "calm".

`w` is reported with `wind_valid`, which is false whenever a gate fails: fewer
than 200 hours of wind evidence, `t < 2.5`, a negative coefficient, a
coefficient that would more than double the loss at 5 m/s, an aperture that
moved outside the three-column jackknife band (the wind column absorbing the
solar signal), or a solution resting on a single day. **No wind term is applied
to the curve yet** — `w` is identified and published so a support dump (and the
summary pool) can answer whether an installation can identify one at all; the
term itself is the next change. The config-entry diagnostics report
`forecast_wind_ms` (the 10 m grid value for the current hour, i.e. the site
proxy any wind figure would rest on), `forecast_wind_available`, and the whole
wind column under `model_daily`.

Note what the input is: 10 m wind on a weather-grid cell, not the wind at the
house. Shelter is a site property no payload carries, so the coefficient is
per-installation and biased low where the grid over-reports — "no identifiable
term" is a normal, correct outcome for a sheltered location.

An attempt that cannot fetch its inputs retries within the hour instead of
waiting the full day, so one network hiccup does not freeze the model; a
completed attempt — fit accepted or rejected — waits the full interval, because
the inputs cannot change in an hour.
`a` (W/K), `b` (m²) and a continuous trust in [0, 1] from `b`'s t-statistic
are computed from your house's data — nothing is hand-tuned. Weak evidence
keeps the solar term tiny automatically, and GHI is smoothed with an
exponential kernel so one cloud cannot swing the curve.

The aperture is identified from the **daily energy balance**:

```
Q_day ≈ a · ΣΔT + 24c − b · ΣGHI        (Σ over every hour of the UTC day)
```

Idle hours contribute `q = 0` instead of being dropped. That matters because
the heating hours are a *selected* sample: on a sunny day the pump's own room
term has already stopped the circuit, so exactly the hours that carry the
solar signal are the ones a heating-hour fit never sees. Identified per hour
the same data even flips the sign (the pump's recovery runs cluster in the
low-sun hours), which is why the day block is the smallest window used.

The day-block model is **gated**: at least 20 usable days (≥ 20 h of data and
≥ 5 heating hours each), a positive loss coefficient and aperture, `b`'s
t-statistic ≥ 2.5, R² ≥ 0.85, and a leave-one-day-out check that no single day
moves `b` by more than ±50 %. When a gate fails the model is *invalid* and the
solar term is simply 0 — the curve never guesses, and an unidentifiable window
switches the term off rather than preserving an older aperture. Because the
evidence is measured in heating days, a fresh install needs a few weeks of
heating season before the term appears.

When the day-block model is valid it is the one the term uses (its `b` and its
`a` are converted with each other); the hourly fit remains as the fallback and
still feeds the load term's demand estimate. Both are visible in diagnostics
(`curve.model` and `curve.model_daily`) and on the solar-model sensor.

## Seasonality

The baseline is frozen when the curve is activated; while active it never
re-learns shape from Auto (there is no Auto to compare against). What moves
with the season is the shared adjustment and the trims:

- **Autumn activation:** the cold end of the baseline is partly extrapolated
  from mild weather. The trims converge as soon as cold weather is observed.
- **Winter:** the solar term shrinks with the low sun, the night/day amplitude
  grows with the larger diurnal swing, the outdoor term catches cold snaps,
  and the trims correct any remaining end-of-curve error from the indoor
  response.
- **Spring and summer:** the solar term grows again, night/day shrinks, and
  the load term holds the curve back when the house is already warm.

Once the switch is on, no user action is needed; the module adapts by itself.
`baseline_learned_hours` and the observed outdoor span on the deviation sensor
show how much of the curve the baseline fit has actually seen.

## Practical notes

- After Auto from HA, 24–30 may still show the last User defined (or
  last app-generated) table. That cache is stale for control until the
  app regenerates it or you edit points in User defined.
- After User defined, switching Auto in HA immediately uses 23, even if
  the point numbers look unchanged.
- Do not map 23 to DUT or to a temperature unit. Datasheet range is 1–50
  with unit “-”.

## Lovelace card

The integration ships a dependency-free Lovelace card
(`qvantum-curve-card`) that draws the whole adaptive curve in one place:
the frozen baseline, the computed shadow curve, the seven points actually
written to the pump, the ±write-threshold band, the optional operating
point and the shared adjustment term breakdown.

### Enabling the card

The integration serves the module at `/qvantum/qvantum-curve-card.js`.
Add it once as a dashboard resource:

1. **Settings → Dashboards → ⋮ → Resources → Add resource**
2. URL: `/qvantum/qvantum-curve-card.js`
3. Resource type: **JavaScript module**
4. Save and reload the dashboard.

Then add the card (visual picker: "Qvantum Curve Card", or manually):

```yaml
type: custom:qvantum-curve-card
entity: sensor.qvantum_adaptive_curve_adjustment   # any curve entity; optional
title: Värmekurva
min_outdoor: -30
max_outdoor: 30
show_baseline: true
show_shadow: true
show_pump: true
show_band: true
operating_point:
  outdoor: sensor.qvantum_bt1
  supply: sensor.qvantum_cal_heat_temp
language: auto        # auto | sv | en
```

`entity` may be any entity of the pump device; the card resolves the other
curve entities (and the pump's written table via `curve_type_heating`) from
the entity registry. Omitting `entity` auto-discovers the adjustment sensor.

Fallback if static serving is unavailable: copy
`custom_components/qvantum/www/qvantum-curve-card.js` to `config/www/` and
use `/local/qvantum-curve-card.js` as the resource URL.

### What the card shows

- **Baseline (frozen)** — dashed; the seven-point table frozen at activation.
- **Computed (shadow)** — solid with dots; what the module would write now.
- **Pump table** — diamonds; holdings 24–30 as currently written (markers
  only appear while the select is available, i.e. Modbus writes enabled).
- **±band** — shaded strip of `band` °C around the shadow curve; this is the
  ≥1 °C write-threshold visualized.
- **Operating point** — ring, only when `operating_point` is configured.
- **Status chips** — Active/Shadow (switch), Ready/blocker, Pump
  User-defined/Auto, clamped to supply limits, indoor cap, solar-model trust.
- **Term chips** — outdoor, night/day, solar, load, trims and total, all in K
  (they are temperature *differences*, not absolute temperatures).

In cloud mode, with the Modbus write option off, or before the first curve
snapshot the card degrades to a message instead of failing.

