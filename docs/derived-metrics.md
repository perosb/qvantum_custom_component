# Derived metrics — power, COP and DHW

What the integration computes on top of the poll payload, and the traps in each
figure. The physics behind the QE's tank and through-flow DHW is in
[QE architecture](qe-architecture.md) §7; this file is about the numbers Home
Assistant publishes.

Every metric here is computed **after** the poll, in `calculations.py` and the
efficiency coordinator. They are never part of the fetch universe: adding one to
`DEFAULT_ENABLED_*` / `DEFAULT_DISABLED_*` would make the client request a
metric the API does not return. Create the entity explicitly in its platform and
list the key in `special_sensor_keys`, so registry cleanup keeps it.

## Power

`dhwpower` is the instantaneous draw power `4.186/60 · BF1 · (BT34 − BT33)` in
Modbus mode; `heatingpower` is derived from the `heatingenergy` counter delta and
holds its last value between steps. Both are documented with their measurements
in [QE architecture §7.5 and §7.7](qe-architecture.md#75-dhwpower-in-home-assistant)
— including why `dhwpower` must never be integrated for energy.

`powertotal` is an estimate (base load + active relay stages +
`compressor_power`), not a calibrated measurement.

## The electrical split

The counters (`compressorenergy` + `additionalenergy`) are **shared between
modes**, so each poll's delta is attributed by `_split_electrical_input`:

| Pump state | Compressor energy → | Element energy → |
|---|---|---|
| hot water | DHW | DHW |
| heating | space heating | DHW, when its own flag is set (parallel top-up) |
| idle / defrosting | — | DHW (the pump reports `Idle` when only the element runs) |

The **element is attributed by `additiondhwdemand`**, its own DHW flag. Measured
on a live install, 2026-10-21: the counters step late (compressor every ~9 min,
the element a handful of times a day) and in all 1 087 step intervals of a
10-day window both `dhwdemand` and `additiondemand` had already cleared, so a
demand-gated split booked the element's DHW energy nowhere — 3.7 kWh of 4.2 kWh
in that window, 8 % of the DHW electrical — and it also left `charge.element_kwh`
at zero, so the tank-volume identification below could never fire.

All three flags are disabled-by-default binary sensors and therefore sit in
`REQUIRED_METRICS`: a disabled entity is not fetched. Energy that still cannot
be attributed (an element step with no flag, or a compressor step that landed in
an idle interval) is **never dropped silently** — it is returned separately and
counted in `cop.unattributed_kwh`, because a vanishing kWh is how a COP drifts.

## `cop_heating`

A rolling one-hour window of counter deltas over **heating-mode electrical
only**. There is no short-window system COP: `heatingenergy` is space-heating
output while `dhwenergy` meters the DHW *draw* on the secondary side, so the two
cannot be combined into one ratio.

## `cop_dhw`

**One charge segment** (`_update_dhw_charge`), not draw power over compressor
power:

- The segment opens on DHW demand **or on the element's DHW flag**, with the
  previous poll's tank level as its baseline. An element top-up after the demand
  cleared is still DHW production, and without it the segment never carried the
  element energy the volume identification reads.
- It publishes live and finalises on the first poll with neither flag.
- Production = draw since the charge started + tank change against that
  baseline; a segment with no compressor work is element-only and capped at
  COP 1.0.

Two invariants that are easy to break in review:

1. The interval that clears the demand is **not** booked — the closing publish
   uses `charge.last_tank`.
2. Element-only evidence checks the **compressor counter** delta
   (`interval_dhw_compressor` is zero for every status but hot water), so a
   defrost cannot read as element heat.

A counter reset drops the window and the open charge; a charge older than
`DHW_CHARGE_MAX_SECONDS` restarts its baseline.

## `dhw_standing_loss`

An EMA persisted in `qvantum.efficiency.<entry_id>` and restored before the first
refresh, so a restart no longer blanks it. The **open free-cooling window is
deliberately not persisted**: the tank went unsampled across the restart, and
resuming would book an unobserved draw as standing loss. A fresh window starts
on the next idle poll.

What the EMA holds is a **coefficient, not a daily figure**: windows are
normalised by the tank-to-ambient difference and the published kWh/d is the
coefficient quoted at `REFERENCE_DELTA_K` (40 K). Without that normalisation the
number tracked the tank temperature rather than the insulation — one window
after a 69 °C extra-hot-water run moved a live install from 2.5 to 5.1 kWh/d.
The volume is the identified one when present, and the store key is
`dhw_standing_loss_per_k`; a snapshot written under the old key is ignored,
never reinterpreted. The newest window's evidence (hours, drop, tank mean,
ambient and its source, ΔT, volume) travels as sensor attributes.

The ambient is only a BT2 that is a **temperature**. BT2 is a wired room-air
sensor whose room the installer picks, so it proxies the tank's surroundings
rather than measuring them; an install whose tank stands colder than BT2's room
gets a biased figure that no payload can flag. Without a probe the sensor holds
the firmware's placeholder (a QE4 owner reported a fixed −30 °C, 2026-10-23), and
the pump's room value then comes from the configured sensor.

`bt2` is therefore skipped when `BT2 Detected` (input 86) says it is not fitted,
or when it reads at or below `BT2_PLACEHOLDER_MAX_C` (−25 °C — the cloud carries
no bit, so the value has to say it). The bound is deliberately lower than the
curve's room band: a tank in an unheated garage at −5 °C is a valid window, while
averaging a placeholder in would divide by 85 K instead of ~34 K and publish
~40 % of the true coefficient. The window then falls through to
`room_temp_external` / `room_temp_ext`, or is discarded when neither exists.

The statistics picker applies the same rule: `pick_indoor_metric_key` skips a
candidate whose series median is not a room temperature
(`INDOOR_SERIES_MIN_C` / `INDOOR_SERIES_MAX_C`, −10…45 °C — a placeholder records
−30 °C as an ordinary series), and skips BT2 outright when the pump reports no
probe. That matters because the picker's fallback exists for a newly configured
sensor with no history: without the guard it selected the placeholder, and a
−50 K room error puts the curve trims on their clamp and inflates the heat-loss
coefficient, the time constant and the solar aperture. No series beats a
plausible wrong one.

## The DHW heat-meter comparison

`dhw_heat_meter_deviation` is a **poll-resolution integral**, not a statistics
product: `mean(flow) · mean(ΔT)` is not the integral of `flow · ΔT`. On a live
install the product read 0.7 kWh against a 17.3 kWh meter over three days, where
the poll integral landed within 2.8 %.

`plant_analytics.DhwMeterWindow` accumulates it (hourly buckets, a week long, gap
and counter-reset guarded) where the interval is known — the main coordinator's
poll — publishes `dhw_meter_estimated_kwh` / `dhw_meter_metered_kwh` /
`dhw_meter_draw_hours` in `values`, and is persisted under `dhw_meter` in the COP
store. The efficiency coordinator only applies the gates (draw time and metered
energy) and the ±20 % warning, so the figure also works without a recorder.
Diagnostics dump it as `cop.dhw_meter`.

## The identified buffer-tank volume

The volume in that balance is **identified, not assumed**. With the compressor
idle and nothing drawn, the element is the only heat source, so

```
V = Σelement_kWh / (ΣΔbt30 · c)        gates: ≥ 1.5 kWh, ≥ 3 K, 50–500 L
```

An interval only counts when **nothing else** can take heat out of the tank
either: `element_only_tank_balance` also requires the addition shunt closed
(`qn8position` = 0) and **no step of the `heatingenergy` counter** in the
interval. The counter is the signal, not the derived `heatingpower`: that one is
computed after the COP pass and reads zero whenever the compressor is not
heating, which is exactly the state being guarded, so it could never fire.

Both guards were missing once. Measured on a QE-6 (2026-10-21) an
extra-hot-water run sat *idle with the element demanded* while `qn8position` had
been 7 and the heating circuit was taking 2.3 kW — tank water was heating the
house, so the element's energy would have been divided by a rise it did not
fully cause, inflating the volume and, linearly, the DHW COP. A payload that
carries neither metric keeps identifying as before (absence is not heat flow),
and neither reading may enter the COP window's counter-reset check: they are not
energy counters.

The datasheet 175 L (QE/QG/QG-C/QH-175; the QGM wall unit QH100 is smaller and
unpublished) is only the fallback. It is a fair fallback: `bt30` is one point in
the stratified tank, so how much energy one kelvin represents depends on how the
charge mixes it, and measured on a QE-6 (2026-10-21) an element-only charge gave
1.60 kWh over 7.7 K → **179 L**, i.e. the nameplate holds there. It is
EMA-smoothed, persisted as `dhw_tank_liters` with the COP snapshot, used for the
live ratio of its own charge, exposed on `cop_dhw` (`charge_open`,
`tank_liters`) and in the diagnostics `cop` block — a new COP-store field belongs
there in the same PR.

## The two tap-water comfort sensors

Both are **estimates from a tank model**, and their names say what they are:

- `tap_water_minutes` → *Remaining shower minutes*:
  `(V/flow)·ln((tank − cold)/(38 − cold))` — the time until the tap falls to the
  38 °C shower temperature at the EMA of the observed shower flow.
- `tap_water_cap` → *Showers remaining*: those minutes divided by the EMA shower
  duration.

Neither is a volume, an energy or the capacity *level*
(`tap_water_capacity_target`, which is the app's 1–7 — see
[tap-water capacity](tap-water-capacity.md)). The flow and duration only update
while water runs, so the figures hold their last shower's values.
