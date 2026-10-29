# Per-compressor-unit registers (QG ground source)

**Version:** 2026-10-29  
**Scope:** QG ground-source models with two compressor units (QG-14 and
family), firmware 1.11.9/1.11.10, Modbus input registers 119-146.  
**Purpose:** Record the documented per-compressor-unit register block, what the
legacy registers mean on these pumps, and why the block is polled as an
optional component.

**Source tags** used below:

| Tag | Meaning |
|---|---|
| `[R]` | Qvantum Modbus Quick-start guide, QSG EN 2613-A (`docs/modbus-1.pdf`), "QGM1/QGM2" input rows. |
| `[M]` | Qvantum QG Installation and user handbook (1014923-A, qvantum.com/documentation): component designations and the compressor-unit sensor view. |
| `[L]` | Live measurement on a QG-14, firmware 1.11.9/1.11.10: read-only scan 2026-10-11 and field test 2026-10-21/29. |
| `[I]` | Home Assistant integration code and semantics (`perosb/qvantum_custom_component`). |
| `[?]` | Not documented in these sources. Do not infer. |

---

## 1. Why a second block exists

A QG-14 has **two compressor units**; QG-7 has one. `[M]` Each unit has its own
compressor, evaporator, condenser, brine pump (GP4) and its own temperature and
pressure sensors. The handbook labels them **EP10** (compressor unit 1) and
**EP11** (compressor unit 2); those labels are the QG compressor-unit
designations and have nothing to do with the QE's EP11 evaporator or EP21
condenser. `[M][L]` The Modbus guide calls the same two units **QGM1** and
**QGM2**. `[R]`

The Modbus register map itself is **not** published on
qvantum.com/documentation: Qvantum support provides it on request. `[R]` The
copy this repository maps against is QSG EN 2613-A in
[`modbus-1.pdf`](modbus-1.pdf). The QG handbook on the public site confirms the
component designations and that the compressor-unit view shows exactly BT10,
BT13, BT14, BT15, BT20, BT23, BP1, BP2 and GP4, but not the register numbers. `[M]`

The legacy metrics (`bt10`, `bt13`, `bt14`, `bt15`, `bt20`, `bt23`, inputs
5/8/9/10/11/14) report the **mean of the two units** on this pump. `[L][I]`
That mean hides a unit that is locked out or standing still: on the measured
pump EP10 sat locked out on alarm 252 for 1 h 48 min while the averaged BT15
still looked normal, and EP11 stopped six times on low source temperature at
BT15 −2.1/−2.2 °C — visible only per unit. `[L]`

Input 31 (`compressormeasuredspeed`) follows the **highest** speed of the two
units, not the sum or the mean. `[L]`

## 2. Register map

One 15-word block per unit, QGM1/EP10 at 119 and QGM2/EP11 at 134. All words
are input registers (read-only), and all are **S16** in the guide. `[R]`

| Address QGM1 / QGM2 | Value | Type | Scale | Unit |
|---|---|---|---|---|
| 119 / 134 | BT10 outlet water temperature | S16 | 10 | °C |
| 120 / 135 | BT13 inlet water temperature | S16 | 10 | °C |
| 121 / 136 | BT14 ground-source return water temperature | S16 | 10 | °C |
| 122 / 137 | BT15 ground-source outlet water temperature | S16 | 10 | °C |
| 123 / 138 | BT20 discharge line temperature | S16 | 10 | °C |
| 124 / 139 | BT23 suction temperature | S16 | 10 | °C |
| 125 / 140 | BP1 low pressure | S16 | 100 | bar |
| 126 / 141 | BP2 high pressure | S16 | 100 | bar |
| 128 / 143 | Compressor actual speed | S16 | 10 | rps |
| 131 / 146 | GP4 ground-source-side water pump | S16 | 1 | 0/1 |

The guide's `[L]`-verified correspondence: `[L]`

- **Which block is which unit:** the pump's display, compressor-unit view,
  2026-10-11 showed EP10 at 0 rpm while EP11 ran, and the 119-131 block
  matched EP10.
- **The legacy registers are means:** the legacy temperature registers matched
  the mean of the two blocks in 34-36 of 36 samples.
- **`+12` is GP4:** it rises to 1 about 90 s before the compressor starts and
  stayed 1 with the compressor at 0 rps during a lockout (alarm 252).

The integration exposes the documented speed as **rpm**, not rps: the map
scales the 0.1-rps register by 6.0, so 25.0 rps reads 1500 rpm, the same unit
as input 31 and the existing `compressormeasuredspeed` entity. `[I]`

### Pressures are gauge pressure, not the display's saturation temperature

BP1 and BP2 are **pressure transmitters**, and the registers carry **gauge
pressure in bar** (scale 100, S16) — that is what the `ep*_bp1_pressure` and
`ep*_bp2_pressure` sensors show. The pump's display shows the same BP1/BP2
circles as the **saturation temperature** of the refrigerant (R290/propane),
so the two numbers must not be compared directly. `[L]`

| Unit state | HA BP1 / BP2 (bar gauge) | Display BP1 / BP2 (°C saturation, R290) |
|---|---|---|
| EP11, stopped | 7.25 / 7.25 | 19.6 / 19.6 |
| EP10, running | 3.55 / 9.57 | −2 / 29 |

Check with the R290 saturation curve and the ambient pressure (absolute =
gauge + ~1.01 bar): 3.55 bar gauge ≈ 4.56 bar absolute ≈ −2 °C, 9.57 ≈ 10.58
bar ≈ 29 °C, 7.25 ≈ 8.26 bar ≈ 19.6 °C. The values are consistent; the
difference is the unit, not a mapping error. `[L]`

A field test 2026-10-21 on a QG-14, firmware 1.11.10, measured EP10 at
1500 rpm while EP11 stood still, and the pressure pairs above. `[L]`

The guide also documents **117 BBR Locked** and, inside each block,
**129/144 4-way valve** and **130/145 flow switch state**. Those are outside
this change and are not decoded; the readable range stops at 146. `[R][I]`

## 3. Optional component, never a poll failure

Most Qvantum pumps do not have two units and do not answer this span (the QE
raises exception 0x04 on 119-146, which is why `QvantumInputs` declares
0-104 and 150-170 only). `[I]` The block is therefore read as its own
component, `QvantumCompressorUnits`, and never as part of the main input poll:
`[I]`

- A refusal is caught and counted; the main poll and its metrics are unaffected.
- After **three consecutive refusals** the block is not polled again until the
  device object is recreated (integration reload or restart); the first
  refusal logs at debug, the third at info.
- Values enter the metrics payload only while the last read **succeeded**, so a
  failed block degrades to no entity/unknown rather than to stale values.
- A success resets the refusal counter, so a transient error during startup
  recovers on the next poll.

A **QG-7** has one compressor unit, so its legacy registers are not averages
and there is no second unit whose fault could hide. If such a pump refuses the
two-unit span, nothing is lost: the sensors simply do not appear. `[M][I]`

## 4. Entities

Enabled by default, created only when the block answers (the integration
creates enabled-by-default sensors only for metrics present in the payload): `[I]`

- **Sensors per unit** (`ep10_*` / `ep11_*`): BT10, BT13, BT14, BT15, BT20,
  BT23, BP1, BP2, compressor rpm. All are `EntityCategory.DIAGNOSTIC` —
  machine-internal refrigerant-circuit telemetry.
- **Binary sensors per unit:** `ep10_brine_pump` / `ep11_brine_pump`
  (`RUNNING`, diagnostic).

On a pump without the block nothing is created, so the default-enabled Modbus
metric list costs no permanently unavailable entities.

## 5. Alarm codes seen in the app but missing from the public list

The Qvantum app shows these texts; the public Q-Alarms-A list does not
document them. `[L]`

| Code | App text |
|---|---|
| 252 | "source water low temperature protection" (locking, must be acknowledged) |
| 279 | "QGM1 fault of water flow switch on the ground source side" |
| 391 | "QGM2 fault of water flow switch on the ground source side" |

---

*The register map is the QSG EN 2613-A QGM1/QGM2 block. A later firmware or a
contradicting measurement wins, and this document then carries the new
numbers.*
