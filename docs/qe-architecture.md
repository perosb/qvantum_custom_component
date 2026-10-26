# QE architecture and measurement model

**Version:** 2026-10-26  
**Scope:** Qvantum QE-4 and QE-6, firmware 1.11.x, Modbus registers per QSG EN 2613-A.  
**Purpose:** A compact technical reference for how the QE actually works — physically, hydraulically and in its measurements — and for the claims that are easy to get wrong.

**Source tags** used below:

| Tag | Meaning |
|---|---|
| `[M]` | Qvantum installation and user handbook, product leaflet or technical data. |
| `[R]` | Qvantum Modbus Quick-start guide (QSG EN 2613-A, D100041). |
| `[L]` | Live measurement on a QE-6, firmware 1.11.10, 2026-10-25/26. |
| `[I]` | Home Assistant integration code and semantics (`perosb/qvantum_custom_component`). |
| `[?]` | Not documented in these sources. Do not infer. |

> **Short version:** [qe-architecture-quick-reference.md](qe-architecture-quick-reference.md) — about 1,100 words for an AI context window. This file is the full reference.

> **Read §0 and §11 first.** Those two sections are enough to avoid the most common mistakes. The rest is detail.

---

## Short version (30 seconds)

The QE is an **exhaust-air heat pump** with a **175 L buffer tank of heating water**. The tank is **not** a domestic-hot-water tank: DHW is heated **instantaneously** in a plate heat exchanger, with the tank as the heat source. The compressor (1–6 kW depending on model) charges the tank; a draw can take **10–20 kW** for a few minutes because the tank discharges. `dhwenergy` meters the **draw** on the secondary side, not the compressor's DHW output. `dhwpower` in Home Assistant is the **instantaneous** draw power `4.186/60 · BF1 · (BT34 − BT33)`; it is a power, not an energy, and does not integrate to `dhwenergy`. **BT30** = buffer tank, **BT31** = DHW primary inlet, **BT33/BT34** = cold/hot tap water. Read §11 before answering DHW questions.

### How to read this document

1. Read the short version, §0 and §11 first. That is enough to avoid the common errors.
2. Always distinguish `dhwenergy` (draw **energy**, the meter) from `dhwpower`/`P_draw` (draw **power**, `BF1 × ΔT`). They do not integrate to the same figure: the 0.1 kWh meter cannot resolve short draws.
3. Distinguish BT30 (tank), BT31 (primary inlet), BT33/BT34 (tap water) and BT14/BT15 (air in/out).
4. `hp_status` follows the compressor, not the immersion heater. Use `compressor_state`, `additiondhwdemand` and the energy counters to decide what is happening.
5. Mark `[?]` as uncertain in an answer. Do not invent a mechanism the sources do not describe.

### Contents

| Section | Area |
|---|---|
| [0](#0-mental-model) | Mental model and why the design looks like this |
| [1](#1-product-line-and-physical-layout) | Product line, two units, the tank, other Qvantum products |
| [2](#2-the-four-circuits) | Air, refrigerant, heating water, DHW — energy flow |
| [3](#3-air-side-and-ventilation) | Ventilation, flows, BT14/BT15, frost |
| [4](#4-refrigerant-circuit-and-compressor) | Refrigerant, compressor, capacity, states |
| [5](#5-heating-circuit-and-buffer-tank) | Heating circuit, tank, QN8, immersion heater |
| [6](#6-domestic-hot-water-through-flow-not-a-tank) | DHW through-flow, BT31, capacity |
| [7](#7-dhw-power-and-energy-the-measurement-model) | Draw power, dhwenergy, dhwpower, COP |
| [8](#8-control-architecture) | Heating, DHW, priority, other functions |
| [9](#9-energy-counters-and-derived-metrics) | Counters and derived figures |
| [10](#10-sensor-reference) | Sensor table |
| [11](#11-common-misconceptions) | Common misconceptions |
| [12](#12-safe-ai-answers) | Safe answers |
| [13](#13-open-questions) | Open questions |
| [14](#14-sources-and-verification) | Sources |

---

## 0. Mental model

1. **The QE is an exhaust-air heat pump.** It takes energy from the outgoing ventilation air, not from outdoor air through an outdoor unit. The compressor and evaporator sit in a unit on top of the hydronic unit. `[M]`
2. **Two physical units:** a hydronic unit (175 L buffer tank, plate heat exchanger, pumps, immersion heater, expansion vessel, interface) and a compressor unit (fan, evaporator, compressor, refrigerant circuit). `[M]`
3. **The 175 L tank is a buffer tank/thermal battery for heating water — not a DHW tank.** The water in the tank is technical water. There is no stored potable-water volume. `[M]`
4. **DHW is produced in through-flow.** Cold water enters plate heat exchanger EP31 and leaves as hot water immediately; the tank is the heat source on the primary side. The plate heat exchanger volume is < 0.8 L. `[M]`
5. **The compressor charges the tank; the tank delivers the draw.** The compressor's DHW mode heats the buffer tank, not the tap water directly. During a draw, tank water is pushed through the plate heat exchanger by pump GP2. `[M]`
6. **Draw power can be 10–20 kW even though the compressor is 1–6 kW depending on model.** The peak comes from the tank's stored energy; the compressor recharges afterwards. `[L]`
7. **`dhwenergy` meters the draw on the secondary side** (BT33 → BT34 × BF1), not the compressor's DHW output. The counter stays flat during a tank charge with no draw. `[I][L]`
8. **`dhwpower` in Home Assistant is the instantaneous draw power** `4.186/60 · BF1 · (BT34 − BT33)`, computed at poll resolution in Modbus mode. `[I]`
9. **Three independent flows:** ventilation air (l/s), tap water (l/min) and heating water (l/s). Never mix them or convert one into another. `[M][R]`
10. **`hp_status` follows the compressor, not the whole unit.** When only the immersion heater charges the tank, the pump can report `Idle` (0) even though DHW is being produced. `[I]`
11. **BT30 is the buffer tank. BT31 is the DHW primary inlet. BT33/BT34 are cold/hot tap water.** BT31 is low when GP2 is stopped and high during a draw. `[M][L]`
12. **Control is curve-based:** outdoor temperature → heating curve → supply setpoint, with room compensation as a separate term. DHW has its own start/stop control and a time-limited priority. `[M][R]`

### Why it is built this way

- Exhaust air is a limited, low-temperature heat source. The buffer tank decouples compressor production from the instantaneous demand of the house and the tap.
- Through-flow DHW avoids a stored potable-water volume: no legionella tank, no standing hot-water volume and no potable-water corrosion requirements for a large tank. `[M]`
- The tank provides the high short-term DHW power and can also act as a thermal battery to shift electricity use in time. `[M]`
- The immersion heater (1+2+2 kW) and the QN8 shunt provide additional heat when the compressor cannot meet demand; the tank can then be charged by the immersion heater. `[M]`

---

## 1. Product line and physical layout

### 1.1 QE-4 and QE-6 in numbers

| Property | QE-4 | QE-6 |
|---|---:|---:|
| Nominal heating output (Pdesignh) | 4 kW | 6 kW |
| Delivered heating output range | 1–4 kW | 1.5–6 kW |
| SCOP EN14825, average climate 35/55 °C | 4.68 / 3.88 | 3.84 / 3.23 |
| Recommended air flow | 25–55 l/s | 40–70 l/s |
| Minimum air flow | 25 l/s | 40 l/s |
| Minimum heating-water flow | 0.08 l/s (4.8 l/min) | 0.15 l/s (9 l/min) |
| Buffer tank | 175 L | 175 L |
| Plate heat exchanger volume | < 0.8 L | < 0.8 L |
| DHW draw volume at 40 °C, EN16147 | 235 L | 235 L |
| Maximum DHW draw at 40 °C, extra DHW | 350 L | 350 L |
| Refrigerant | R513A, 1.2 kg | R513A, 1.35 kg |
| Immersion heater | 5 kW (1+2+2) | 5 kW (1+2+2) |
| Dimensions (W × D × H) | 600 × 620 × 2,045 mm | 600 × 620 × 2,045 mm |
| Weight empty/filled | 190 / 365 kg | 195 / 370 kg |
| Recommended fuse, 3×400 V / 1×230 V | 16 A / 35 A | 16 A / 35 A |
| Maximum recommended supply temperature | 65 °C | 65 °C |
| Maximum supply with immersion heater | 80 °C | 80 °C |
| Maximum tank temperature with immersion heater | 90 °C | 90 °C |
| Maximum tank temperature without immersion heater | 63 °C | 63 °C |

Sources: `[M]` (QE Installation and user handbook 1011522-A, technical data) and `[R]`.

> Older manual versions may state 75 °C without the immersion heater; the 2026 datasheet states 63 °C. Check the manual for the specific unit.

> **Note:** 235 L is not the tank volume. It is the declared draw volume at 40 °C per EN16147, with 10 °C incoming cold water and 10 l/min. The tank is 175 L and can deliver different amounts of energy depending on temperature and draw flow.

### 1.2 Two units, one water circuit

- **Hydronic unit (lower):** buffer tank, plate heat exchanger EP31, circulation pump GP1, DHW pump GP2, immersion heater EB1, expansion vessel CM10, safety valves, QM10 (diverting valve), QN8 (shunt), connections XL1–XL4 and the display.
- **Compressor unit (upper):** ventilation connections XL20/XL21, filter HQ1, evaporator EP11, compressor GQ11, condenser EP21, fan GQ21, expansion valve QN1, four-way valve QM1, pressure sensors BP1/BP2 and refrigerant sensors BT20–BT23. `[M]`

The hydronic unit and the compressor unit are connected by heating-water and refrigerant lines. The compressor unit cannot operate as a separate outdoor unit; it is part of the indoor unit. `[M]`

### 1.3 What the tank is — and is not

- The tank is **175 L of heating water** and acts as a buffer/thermal battery. `[M]`
- The tank is **not** a potable-water storage tank. Potable water only passes through the secondary side of the plate heat exchanger. `[M]`
- The tank is **stratified**. One sensor (BT30) does not represent the whole tank. During a draw, the top can be significantly hotter than BT30. `[L]`
- The tank can be charged by the compressor, by the immersion heater, or both. It can also deliver heat to the heating system through QN8 when the compressor cannot meet demand. `[M]`

### 1.4 Other Qvantum products (QA+QH, QG)

The integration is **not QE-specific**. It speaks Qvantum's shared cloud API and
Modbus register map, so the **QA+QH** (air-source) and **QG** (ground-source)
families are expected to work where they expose the same metrics and settings.
The integration creates entities only for metrics present in the payload, so an
absent register degrades to no entity instead of failing the poll. `[R][I]`

Do **not** transfer the QE hardware model to those products without checking
their own manuals and hydraulic schematics:

- QA+QH is air-to-water and QG is brine-to-water; their source-side sensors,
  defrost behaviour and refrigerant circuits differ from the QE's exhaust-air
  evaporator.
- DHW may be an external cylinder or a different integrated arrangement. The
  QE's **175 L buffer tank + through-flow plate heat exchanger** is a QE design,
  not a universal Qvantum design.
- The QE-specific draw-power formula and `dhwenergy`/`dhwpower` semantics still
  describe the tap-water side where the same BF1/BT33/BT34 sensors exist, but a
  product with a DHW tank will have a different tank balance and COP model.
- Check the product's manual before applying any QE-specific claim about the
  tank, the immersion heater or the DHW path.

---

## 2. The four circuits

```
AIR       room air  →  BT14  →  filter HQ1  →  evaporator EP11  →  BT15  →  exhaust air
            (warm)                                              (cold)

REFRIG.   evaporator EP11  →  compressor GQ11  →  condenser EP21  →  QN1  →  back
                                                       │
HEATING                                                ▼
          condenser  →  BT13/BT10  →  QM10  →  heating system (BT11/BT12)
                                           └→  buffer tank  →  QN8  →  heating system

DHW       buffer tank  →  BT31  →  GP2  →  EP31 primary side  →  back to tank
          cold water  →  BT33  →  EP31 secondary side  →  BT34  →  tap
```

### 2.1 Energy chain

1. Ventilation air is cooled in the evaporator. Energy moves from the air to the refrigerant.
2. The compressor raises the refrigerant pressure and temperature. Electrical power is added.
3. The condenser releases heat to the heating water. The refrigerant condenses.
4. The heat goes either to the heating system or to the buffer tank (DHW charge), depending on QM10.
5. During a DHW draw, tank water goes through GP2 and the plate heat exchanger, where the heat is transferred to the tap water.
6. The immersion heater can add heat directly to the tank/heating water when the compressor cannot meet demand.

### 2.2 Temperature levels

| Circuit | Typical range | Comment |
|---|---|---|
| Ventilation air in (BT14) | 5–35 °C | Room-temperature extract air; the operating range is 5–35 °C. |
| Ventilation air out (BT15) | Lower than BT14 | Air after the evaporator; can approach 0 °C and frost the evaporator. |
| Heating water | 20–80 °C | Maximum recommended supply 65 °C; 80 °C with the immersion heater. |
| Buffer tank | Up to 90 °C with immersion heater | 63 °C without the immersion heater. |
| Tap water | 1–60 °C | Cold water in (BT33) to hot water out (BT34); plate heat exchanger limit. |

---

## 3. Air side and ventilation

### 3.1 Path

Rooms → extract air devices → duct → filter HQ1 (G3) → evaporator EP11 → fan GQ21 → exhaust duct → outside. Connections are Ø125 mm (XL20/XL21). `[M]`

### 3.2 Flows and fan control

- Recommended air flow: **25–55 l/s** (QE-4) and **40–70 l/s** (QE-6). `[M]`
- Minimum air flow: **25 l/s** (QE-4) and **40 l/s** (QE-6), assuming room air above 16 °C. `[M]`
- Nominal air flow in the EN14825 data: **235 m³/h ≈ 65 l/s** (QE-6). `[M]`
- The fan is variable-speed. Ventilation states are Off/Reduced/Normal/Extra (holdings 68–72), and fan speed is reported in rpm (input 30). `[R]`
- "Boost ventilation" temporarily increases flow; "Close ventilation" stops the fan and leaves only the immersion heater to heat. `[M]`

### 3.3 BT14 and BT15 — a naming trap

- **BT14** is the warm air entering the evaporator (the room extract air).
- **BT15** is the cold air leaving the evaporator (the exhaust air).
- The Modbus guide calls them "Heating source flow" and "Heating source return". `[R]`
- The manual's component table calls BT14 "heating medium return" and BT15 "extract air". This does not match the physical values and should be treated as an inconsistent/misleading label. `[M]`
- Live example: BT14 = 22.5 °C, BT15 = 1.1 °C at an outdoor temperature of 8.4 °C. If BT14 were the heating-water return, a supply of 36 °C and a return of 22.5 °C would imply an unreasonably high heat output at the normal flow; the air interpretation gives about 1.7 kW across the evaporator, consistent with the compressor's 1.2 kW electrical input. `[L]`

> **Rule:** Use BT14/BT15 as air temperatures in/out of the evaporator. Do not use BT14 as a heating-water return without verifying it on the specific unit.

### 3.4 Outdoor-air mixing

- The QE can be fitted with outdoor-air mixing through a manual damper (RN1) to get enough air flow over the evaporator when extract air alone is insufficient. `[M]`
- It is only permitted in climate zones with a design outdoor temperature (DOT) above −10 °C. `[M]`
- During commissioning, close the damper, adjust the flow without outdoor-air mixing, then open the damper and run the fan at 100%; finally throttle the damper to the required flow. `[M]`
- Frost on the evaporator is a known risk; the heat pump must be off during adjustment. `[M]`

### 3.5 Frost and defrost

- The evaporator can frost at low air temperatures. The firmware has separate defrost states: "Defrost DHW passive", "Defrost heating passive" and "Defrost pool passive". `[R]`
- `hp_status = 1` means Defrost; compressor states 9/10/13 are the defrost states. `[R]`
- Normal heating/DHW production pauses during defrost. The exact mechanism (passive use of tank heat, hot gas, reverse cycle) is not documented in the public register guide. `[?]`
- `Time To Defrost` (input 67) is a flag, not a temperature. `[R]`

### 3.6 Filter

- Filter class G3. `[M]`
- `ventilation_filter_time_left` (input 91) is the hours remaining until filter replacement. `[R]`
- The fan must be stopped before replacing the filter. `[M]`

---

## 4. Refrigerant circuit and compressor

### 4.1 Refrigerant and components

- Refrigerant: **R513A**, GWP 631. Charge 1.2 kg (QE-4) / 1.35 kg (QE-6). `[M]`
- Maximum pressure at the high-pressure switch: **2.45 MPa / 24.5 bar**. `[M]`
- Components: evaporator EP11, compressor GQ11, condenser EP21, expansion valve QN1, four-way valve QM1, pressure sensors BP1 (suction) and BP2 (discharge), temperature sensors BT20 (discharge line), BT21 (liquid line), BT22 (evaporator inlet) and BT23 (suction line). `[M][R]`

### 4.2 Inverter-controlled compressor

- The compressor is inverter-controlled and variable-speed. Speed is reported in rpm (input 31). `[M][R]`
- Delivered heating output range: **1–4 kW** (QE-4) and **1.5–6 kW** (QE-6). `[M]`
- Compressor electrical power is measured directly in **input 93** (`compressor_power`, W). It is the only direct power measurement in the register map. `[R]`
- Heating output (`heatingenergy`, `dhwenergy`) is metered by energy counters, not by power sensors. `[I]`

### 4.3 Declared capacity per EN14825

| Outdoor temperature Tj | QE-4 Pdh / COPd | QE-6 Pdh / COPd |
|---|---|---|
| −7 °C | 3.6 kW / 2.7 | 4.8 kW / 2.04 |
| +2 °C | 3.3 kW / 3.8 | 2.9 kW / 3.23 |
| +7 °C | 3.0 kW / 4.3 | 1.9 kW / 4.48 |
| +12 °C | 2.6 kW / 4.6 | 1.6 kW / 5.72 |
| Bivalent temperature | −8 °C | −10 °C |
| TOL (minimum outdoor temperature) | −10 °C | −10 °C |
| Maximum supply at TOL | 58 °C | 58 °C |

Source: `[M]`. These are declared part-load values, not hardware limits. The compressor can modulate within 1–4/1.5–6 kW depending on the operating point.

### 4.4 What the compressor does in each state

| State | Compressor state | `hp_status` | What happens |
|---|---:|---:|---|
| Off | 0 | 0 | No compressor. |
| Idle | 1 | 0 | Compressor stopped; immersion heater may run. |
| Prepare heating | 2 | 3 | Valves/speed are set before heating. |
| Prepare cooling | 3 | 4 | Valves/speed are set before cooling. |
| Prepare DHW 1 | 4 | 2 | First step towards a DHW charge. |
| Prepare DHW 2 | 5 | 2 | Second step towards a DHW charge. |
| Heating | 6 | 3 | The condenser heats the heating system. |
| Cooling | 7 | 4 | The four-way valve reverses; the heating system is cooled. |
| DHW | 8 | 2 | The condenser heats the buffer tank (DHW charge). |
| Defrost DHW passive | 9 | 1 | Defrost in DHW mode. |
| Defrost heating passive | 10 | 1 | Defrost in heating mode. |
| Prepare pool | 11 | — | Pool preparation; not part of normal QE residential operation. |
| Pool | 12 | — | Pool control; not part of normal QE residential operation. |
| Defrost pool passive | 13 | 1 | Defrost in pool mode; the integration maps it to Defrosting (§3.5 counts 9/10/13 as the defrost states). |

Source: `[R]`, with the `hp_status` mapping from `[R]`. The pool states exist in the shared firmware map and should not be assumed active on a QE; when input 41 reports 0, the integration's fallback maps 11/12 to DHW and 13 to Defrosting.

> **Important:** `hp_status` follows the **compressor**. When only the immersion heater charges the tank, the status can be `Idle` (0) even though DHW is being produced. Use `additiondhwdemand`, `compressor_state` and the energy counters to decide what is actually happening. `[I]`

### 4.5 Cooling

- The QE can provide cooling through the heating system (underfloor heating, radiators or fan coils). `[M]`
- The four-way valve QM1 allows the refrigerant circuit to reverse; in cooling mode the heating system is the cold emitter and the ventilation air is the heat sink. The exact cooling strategy is not described in detail in the register guide. `[M][?]`
- Cooling requires dew-point protection (`dew_point_protection`, holding 39), a minimum supply of 7–30 °C (holding 40) and condensation management. `[R][M]`
- Cooling does **not** use the DHW plate heat exchanger. `[M]`

---

## 5. Heating circuit and buffer tank

### 5.1 Path

Condenser (BT13 in → BT10 out) → QM10 diverting valve → either the heating system (BT11 supply, BT12 external supply) or the buffer tank. The return goes back to the condenser. Circulation pump GP1 drives the heating system. `[M]`

- **QM10** is the diverting valve between heating and DHW charging. `[M]`
- **QN8** is the shunt that opens for additional heat from the tank to the heating system when the compressor cannot meet demand. `[M]`
- **GP1** is the heating-system circulation pump, variable-speed. `[R]`
- **BT11** is the supply temperature after additional heat; **BT12** is an optional external supply sensor. `[R]`

### 5.2 The tank as a thermal battery

- Volume: **175 L**. Heat capacity: about **0.204 kWh/K** (175 L × 4.186 kJ/kg·K ≈ 732 kJ/K).
- The tank is stratified. BT30 is one point in the tank, not an average. The top can be 60 °C while BT30 reads 40 °C during a draw. `[L]`
- The tank can be charged by the compressor (QM10 in DHW mode), by the immersion heater, or both. `[M]`
- The tank can deliver heat to the heating system through QN8 when the compressor cannot meet demand; the tank temperature is then maintained by the immersion heater. `[M]`

### 5.3 Flow and volume

- Minimum heating-water flow: **0.08 l/s** (QE-4) / **0.15 l/s** (QE-6). `[M]`
- The expansion vessel is **12 L** with 1 bar pre-pressure; maximum system volume is about **230 L** at that pre-pressure. `[M]`
- Maximum recommended supply: **65 °C**. With the immersion heater the supply can reach **80 °C** and the tank **90 °C**. Without the immersion heater the tank is limited to **63 °C**. `[M]`

### 5.4 Immersion heater and additional heat

The immersion heater (EB1) is the QE's electric backup and peak source. It heats
the buffer tank/heating water, not the tap water directly: tank water reaches
the heating circuit through QN8, or the tap through GP2/EP31. `[M]`

- **Output:** 5 kW total, split as **1+2+2 kW** and switched in 1–5 kW steps.
  The physical stages are 1+2+2 kW; the integration's relay-stage estimate maps
  L1/L2/L3 as 2+2+1 kW. `[M][I]`
- **Parallel operation:** it can run alongside the compressor, e.g. charge the
  tank while the compressor heats the house, or top up DHW after a draw. `[I]`
- **Only source:** with the compressor off or defrosting, the immersion heater
  can be the only heat source. `hp_status` then follows the compressor and can
  read `Idle` (0), so use `additiondemand`/`additiondhwdemand` instead. `[I]`
- **DHW:** `additiondhwdemand` (input 53, scale 0.1) is its own DHW flag;
  `additiondemand` (input 52) is the general flag. Extra DHW can activate it. `[R][M]`
- **Energy:** `additionalenergy` (input 97/98) meters its electrical energy.
  It is shared between heating and DHW; the integration attributes it per poll
  by the flags and counts unattributable energy in `cop.unattributed_kwh`. `[I]`
- **COP:** the immersion heater is resistive, COP 1.0. A DHW charge segment with
  no compressor work is capped at COP 1.0, so element-only DHW cannot look like
  a heat-pump charge. `[I]`
- **Limits:** maximum tank temperature 90 °C with the immersion heater; maximum
  supply 80 °C; the safety temperature limiter trips at 95 °C and needs a manual
  reset. `[M]`
- **Electrical:** 1×230 V (35 A) or 3×400 V (16 A); the element fuses are 10 A
  each (F02–F04); a single-phase installation bridges L1/L2/L3 with the supplied
  busbar. `[M]`

| Signal | Register | Meaning |
|---|---|---|
| `allow_addition` | Holding 4 | Manual-mode permission for additional heat. |
| `additiondemand` | Input 52 | General additional-heat demand. |
| `additiondhwdemand` | Input 53, scale 0.1 | Immersion heater's DHW demand. |
| `additionalenergy` | Input 97/98 | Immersion-heater electrical energy, lifetime. |
| Relay bitmask | Input 33, bits 0–2 | L1/L2/L3 element stages. |
| `qn8position` | Input 76 | Tank-to-heating shunt position; 0 = closed. |
| `powertotal` | HA derived | Base 160 W + relay stages + `compressor_power`; an estimate. |

---

## 6. Domestic hot water: through-flow, not a tank

### 6.1 Principle

The QE has **no domestic-hot-water tank**. Tap water is heated in a plate heat exchanger EP31 with a volume below 0.8 L, at the moment it is drawn. The heat comes from the buffer tank on the primary side. `[M]`

This is the core of the design:

- Potable water is always fresh; there is no standing hot-water volume.
- Legionella problems in a stored DHW tank therefore do not apply to the DHW side.
- The large tank is heating water, not potable water.
- The plate heat exchanger is the only component in contact with potable water (apart from pipes and valves).

### 6.2 Path and components

```
Buffer tank (top, hot) ── BT31 ── GP2 ──► EP31 primary side ──► back to tank
Cold water (BT33) ────────────────────► EP31 secondary side ──► BT34 ──► tap
                                              BF1 measures tap flow
```

| Component | Designation | Meaning |
|---|---|---|
| BT31 | DHW primary inlet | Temperature of the tank water entering the primary side of the plate heat exchanger. |
| BT33 | Cold water | Incoming potable-water temperature. |
| BT34 | Hot water | Outgoing potable-water temperature. |
| BF1 | Tap flow | Flow in l/min on the secondary side. |
| GP2 | DHW pump | Internal primary pump between tank and plate heat exchanger. |
| EP31 | Plate heat exchanger | Transfers heat from tank water to potable water. |

Source: `[M]` system overview and component list; `[R]` registers.

> **Naming trap:** GP2 is called "DHW circulation pump" in the datasheet, but it is **not** a potable-water recirculation pump. It is the internal primary pump that pushes tank water through the plate heat exchanger. An external hot-water circulation (HWC) loop is installed outside the unit and controlled separately. `[M]`

### 6.3 Why BT31 can read 42 °C when the tank is 55 °C

BT31 is at the primary inlet of the plate heat exchanger. When GP2 is stopped, the primary line between the tank and the heat exchanger is stagnant and cools towards ambient. BT31 can therefore read **42.8 °C** while BT30 reads **55.4 °C**. `[L]`

When a draw starts, GP2 flushes tank-top water into the primary line. BT31 rises quickly and can become **higher than BT30**, because the tank is stratified and the top is hotter than BT30's measuring point. Live example: BT31 ≈ **60 °C** and BT30 ≈ **56 °C** during a draw. `[L]`

> **Rule:** BT31 is neither the tank temperature nor the DHW outlet. It is the primary-inlet temperature at the plate heat exchanger. A low BT31 at standstill is normal, not a fault.

| Situation | BF1 | GP2 | BT31 | BT30 | `dhwenergy` | Compressor |
|---|---:|---:|---:|---:|---|---|
| Standstill, tank charged | 0 | 0 | Low (e.g. 42 °C) | ~55 °C | Flat | Heating or idle |
| Draw | >0 | >0 | High (e.g. 60 °C) | Falling | Rising | Often DHW, can be heating |
| Charge after draw | 0 | 0 | Low again | Rising | Flat | DHW |

### 6.4 Capacity, start/stop and extra DHW

- Tank charging is controlled by a **start/stop pair** for normal DHW and one for extra DHW (holdings 56–59). `[R]`
- The app's capacity level 1–7 corresponds to a start/stop pair, not a litre volume. The pair is the truth; the level is a label. `[R][I]`
- Current firmware can use pairs outside the old table. The integration then reports `unknown` instead of guessing a level. `[I]`
- **Extra DHW** raises the stop temperature and can temporarily activate the immersion heater. `[M]`
- **DHW priority** gives hot water precedence for an adjustable time (holding 75, 0–180 min; live 30 min). Heating pauses during that time; `dhw_prioritytimeleft` counts down. `[R]`
- `dhw_prioritytimeleft = 0` does not mean DHW demand is gone; it means the priority window has ended. `[R][L]`

### 6.5 Legionella and water quality

- Because DHW is produced in through-flow, there is no stored potable-water volume to keep legionella-safe. `[M]`
- The tank is heating water and is not subject to potable-water requirements in the same way.
- The plate heat exchanger and the potable-water side have their own water-quality requirements (pH, conductivity, hardness, chloride, ammonia, sulphate, bicarbonate). `[M]`
- An external hot-water circulation loop, if installed, is a separate potable-water loop outside the unit. `[M]`

### 6.6 Why the draw temperature varies with flow

The plate heat exchanger has a limited heat-transfer capacity. At a high tap flow the secondary outlet temperature is lower for the same primary temperature; at a low flow it is higher. Live example:

| Tap flow | BT33 in | BT34 out | ΔT | Instantaneous draw power |
|---:|---:|---:|---:|---:|
| 10.7 l/min | 13.1 °C | 39.5 °C | 26.4 K | 19.7 kW |
| 4.7 l/min | 13.3 °C | 51.4 °C | 38.1 K | 12.5 kW |

`[L]`

The app's "Temperatur ut" setting (Normal / + / ++) affects the outgoing tap-water temperature. It is **not** a Modbus register: in a measurement on 2026-10-19, Normal held 51.7 °C while ++ reached 56.2 °C at 4.9 l/min and 62–65 °C tank, without any holding register changing. `[I]`

---

## 7. DHW power and energy: the measurement model

### 7.1 Three different "DHW powers"

| Concept | What it is | How it is measured | Typical magnitude |
|---|---|---|---|
| **Draw power** `P_draw` | Heat delivered to the tap water | `BF1 × (BT34 − BT33)` | 10–20 kW |
| **Compressor DHW heat** | Heat the compressor adds to the tank | Not measured directly; tank balance | 1–6 kW (model-dependent) |
| **Compressor electrical input** | Electrical input | Input 93 `compressor_power` | 0.3–2 kW |
| **Immersion heater electrical input** | Electrical input to the tank | Relay stages / `additionalenergy` | 1–5 kW |

The first three are not the same thing. The high draw power is possible because the tank discharges; the compressor does not have to deliver it instantaneously.

### 7.2 Instantaneous draw power

Formula:

```
P_draw [kW] = (4.186 / 60) · BF1 [l/min] · (BT34 − BT33) [K]
```

This assumes water density ≈ 1 kg/L and heat capacity 4.186 kJ/(kg·K). The formula gives the heat power on the **secondary side**, i.e. the useful power to the tap water. It says nothing about the compressor's electrical demand.

Examples:

- `BF1 = 10.7 l/min`, `BT33 = 13.1 °C`, `BT34 = 39.5 °C` → `4.186/60 × 10.7 × 26.4 ≈ 19.7 kW`.
- `BF1 = 4.7 l/min`, `BT33 = 13.3 °C`, `BT34 = 51.4 °C` → `4.186/60 × 4.7 × 38.1 ≈ 12.5 kW`.

### 7.3 Tank energy

The tank contains 175 L of water:

```
E_tank [kWh] = 175 · 4.186 / 3600 · ΔT ≈ 0.2035 · ΔT [K]
```

Examples:

- 16 K drop ≈ **3.26 kWh**.
- 40 K drop ≈ **8.14 kWh**.
- 55 → 40 °C ≈ **3.05 kWh**.
- 60 → 20 °C ≈ **8.14 kWh**.

Live example (QE-6, 2026-10-25/26):

| Draw | BF1 | BT33 → BT34 | `P_draw` | Duration | Volume | Draw energy | BT30 drop |
|---|---:|---:|---:|---:|---:|---:|---:|
| Morning shower | 10.7 l/min | 13.1 → 39.5 °C | 19.7 kW | ~4.5 min | ~48 L | ~1.5 kWh | 56.8 → 51.6 °C |
| Long draw | 4.7 l/min | 13.3 → 51.4 °C | 12.5 kW | ~14.5 min | ~68 L | ~3.0 kWh | 55.6 → 39.6 °C |

The tank energy change, estimated from BT30, was about **1.06 kWh** (5.2 K) and **3.26 kWh** (16 K). The difference from the draw energy is due to simultaneous compressor charging, standing loss and stratification; the figures are rounded. `[L]`

This explains why a 12–20 kW draw can last only minutes before the tank cools, and why the compressor must recharge afterwards.

### 7.4 `dhwenergy` — the draw meter

- `dhwenergy` (input 103/104) is a **lifetime counter** in 0.1 kWh steps.
- It measures **tap-water energy on the secondary side** (BT33 → BT34 × BF1), not the compressor's DHW output. `[I]`
- It stays flat during a tank charge with no draw. `[L]`
- Live validation: the integration's poll integral gave **25.154 kWh** against the counter's **25.2 kWh** over 2.33 draw hours, i.e. **−0.2 %**. `[L][I]`

### 7.5 `dhwpower` in Home Assistant

- `dhwpower` is the **instantaneous draw power** of §7.2, `4.186/60 · BF1 · (BT34 − BT33)`, evaluated on every poll in Modbus mode and published in W. `[I]`
- It reads 0 W when `BF1 = 0` or when `BT34 − BT33 ≤ 0` (water moving but not being heated), and the entity is unavailable when `bf1_l_min`/`bt33`/`bt34` are missing. `[I]`
- In cloud mode the entity carries the API's own `dhwpower` value, disabled by default; the integration only computes it over Modbus. `[I]`
- It is **not** derived from the 0.1 kWh `dhwenergy` counter any more. That derivation could not carry a power figure: one step is a staircase whose granularity follows the poll interval (0.1 kWh over 15 s reads 24 kW, over 60 s 6 kW), it drops the trailing partial step of every draw and it cannot resolve a draw shorter than a step (~36 s at 10 kW). Measured on a live QE-6, 2026-10-26 — 36 h, 41 draws, 55 min of draw, 9.60 kWh metered: the counter-derived series read 0 W on 24 % of the polls inside a draw, peaked at 24.2 kW, had a 4.9 kW RMSE against the formula and missed 31 of the 41 draws, while the formula stayed within 2 % of the meter on the long draws. `[L][I]`
- Do **not** integrate `dhwpower` for energy. Over that window the formula read 10.11 kWh against the meter's 9.60 kWh (+5 %), because the meter cannot resolve the sub-0.1 kWh draws the formula sees; `dhwenergy` is the energy figure and the only one to reconcile against. `[L]`

### 7.6 `cop_dhw` — the charge-segment COP

- `cop_dhw` is **not** draw power divided by compressor power.
- The integration measures a **charge segment**: from when DHW demand opens until it closes. Production = draw during the segment + tank change against a baseline. Electrical input = compressor + immersion heater. `[I]`
- A segment with no compressor work (immersion only) is capped at COP 1.0. `[I]`
- A single tank sensor (BT30) cannot capture stratification; `cop_dhw` is therefore an estimate, not a direct measurement of the plate heat exchanger's or compressor's instantaneous COP. `[I][L]`
- Live example 2026-10-26: an open charge segment from 52.4 °C to 57.8 °C reported `cop_dhw ≈ 0.95`. It should not be read as "the plate heat exchanger has COP 0.95"; it is the segment's tank balance from one point measurement. `[L]`

### 7.7 `heatingpower`, `dhwpower` and `powertotal`

- `heatingpower` is **derived** from the `heatingenergy` counter: the value held between steps is the average since the previous one — ~3–4 min at a median 1.6 kW — and it resets to 0 outside heating, so read it as a short average rather than an instantaneous figure. There is no heating-water flow register to replace it with (only the DHW `bf1_l_min`). `dhwpower` is different — it is the instantaneous flow × ΔT measurement of §7.5, not a counter rate. `[I]`
- `compressor_power` (input 93) is the only direct electrical power measurement. `[R]`
- `powertotal` in the integration is an **estimate**: 160 W base load + active relay stages (2+2+1 kW) + `compressor_power`. It is not a calibrated measurement. `[I]`

---

## 8. Control architecture

### 8.1 Heating

1. BT1 measures the outdoor temperature (1-minute filter). `[R]`
2. The heating curve calculates the supply setpoint `cal_heat_temp` (input 35). `[R]`
3. The curve can be **Auto** (holding 23, family 1–50) or **User defined** (holdings 24–30, seven points at −30…+30 °C). `[R]`
4. Room compensation adds a term from the indoor temperature: `cal_heat_temp = curve − factor × (room − target)`. The factor is `room_comp_factor` (holding 13); the sensor source is holding 9 (0 none, 1 BT2, 2 BT3, 3 AUX, 4 external). `[R][I]`
5. The supply is limited by max/min (holdings 19/20) and can be stopped at `stop_heating` (holding 18). `[R]`
6. GP1 is variable-speed; QN8 opens for additional heat from the tank when needed. `[R][M]`

### 8.2 DHW

- The tank charges when BT30 falls below the start temperature and stops at the stop temperature. `[R]`
- Normal and Extra have separate start/stop pairs. `[R]`
- During a draw the tank cools; the compressor can enter DHW mode (QM10) and charge the tank. `[R][L]`
- DHW priority (holding 75) gives DHW precedence for a limited time; afterwards heating can take over. `[R]`
- `time_between_modes` (holding 6) limits how often the mode can change. `[R]`

### 8.3 Priority and state

- `heatingdemand`, `dhwdemand`, `coolingdemand` are **demand flags**, not the current mode. `[R]`
- `compressor_state` and `hp_status` show what the compressor is actually doing. `[R]`
- `*_prioritytimeleft` shows how long each priority applies. `[R]`
- A DHW demand can be active (`dhwdemand = 1`) while the compressor heats the house, if the DHW priority has ended or heating has priority. `[L]`

### 8.4 Other functions

- **Smart DHW:** cloud-based control that can raise/lower the tank temperature according to electricity price; statuses are Unavailable/Standby/Raising/Lowering/Lowering long term/Paused. `[R]`
- **SG Ready:** external tariff signals can block/encourage heating and DHW (inputs 157–159, holding 88). `[R]`
- **Vacation mode:** lowers indoor temperature and DHW production. `[R]`
- **Manual mode:** operation_mode 0 Auto, 1 Manual, 2 Addition only; manual permissions for heating/cooling/addition/DHW. `[R]`
- **Extra DHW:** raises the stop temperature for a selected time; can activate the immersion heater. `[M]`

---

## 9. Energy counters and derived metrics

### 9.1 Registers

| Register | Name | What it meters | Trap |
|---|---|---|---|
| Input 95/96 | `compressorenergy` | Compressor electrical energy, lifetime | Shared between heating and DHW; must be attributed per interval. |
| Input 97/98 | `additionalenergy` | Immersion-heater electrical energy, lifetime | Can be heating or DHW; `additiondhwdemand` distinguishes. |
| Input 99/100 | `heatingenergy` | Space-heating output | Not DHW; 0.1 kWh steps. |
| Input 103/104 | `dhwenergy` | Tap-water energy, secondary side | Not the compressor's DHW output; flat during charging. |
| Input 101/102 | `coolingenergy` | Cooling energy | If present. |
| Input 93 | `compressor_power` | Compressor electrical power | W; direct measurement. |
| Input 88/89 | `compressor_run_time` / `starts` | Run time / starts | Lifetime; never reset. |
| Input 91 | `ventilation_filter_time_left` | Filter hours remaining | Counts down. |

### 9.2 Derived metrics in the integration

| Metric | What it is | Important limitation |
|---|---|---|
| `heatingpower` | `heatingenergy` delta / time | Held between counter steps; 0 outside heating. |
| `dhwpower` | `BF1 × (BT34 − BT33)` in W | Instantaneous draw power; 0 without flow; not an energy. |
| `cop_heating` | Rolling 1 h heating COP | Heating mode only; shares the electrical counters. |
| `cop_dhw` | Charge-segment COP | Tank balance, one sensor; not draw efficiency. |
| `scop_total` | 30-day system COP | Includes DHW, additional heat and real operation. |
| `dhw_standing_loss` | Tank standing loss | Normalised to 40 K tank-to-ambient. |
| `dhw_tank_liters` | Identified tank volume | Element-only balance; live 177.9 L vs 175 L nameplate. |
| `dhw_meter_estimated_kwh` | Poll integral of BF1 × ΔT | Compared with `dhwenergy`; live −0.2 %. |
| `tap_water_cap` / `tap_water_minutes` | Shower estimate | Not a volume and not the capacity level. |

### 9.3 Measurement chain

```
Physics → sensor (BT/BF/GP) → Modbus input → integration value → HA entity → derived metric
```

Each step can lose information:

- Modbus scales values (e.g. 0.1 °C, 0.01 l/min, 0.1 kWh).
- The integration can hold a value between polls.
- Derived metrics can be EMA-filtered or segment-based.
- A HA entity can be disabled, so the value is not updated.

---

## 10. Sensor reference

| ID | Location | Unit | Meaning | Trap |
|---|---|---|---|---|
| BT1 | Outdoor air | °C | Outdoor temperature, 1-minute filter | Used by the heating curve. |
| BT2 | Indoor | °C | Indoor temperature | Optional; can be a firmware placeholder (−30 °C) if not fitted. |
| BT4 | Extra | °C | Extra sensor | Rarely relevant. |
| BT10 | Condenser out | °C | Heating-water temperature out of the condenser | Supply is measured in BT11; BT10 is before the diverting valve/additional heat. |
| BT11 | Supply | °C | Supply after additional heat | The actual supply temperature. |
| BT12 | External supply | °C | Optional external supply sensor | Used if fitted. |
| BT13 | Condenser in | °C | Heating-water temperature into the condenser | Condenser inlet; there is no separate house-return sensor. |
| BT14 | Air in | °C | Warm extract air into the evaporator | The manual's label is misleading; see §3.3. |
| BT15 | Air out | °C | Cold exhaust air out of the evaporator | Can be close to 0 °C. |
| BT20 | Discharge line | °C | Compressor discharge temperature | Diagnostics. |
| BT21 | Liquid line | °C | Refrigerant liquid line | Diagnostics. |
| BT22 | Evaporator inlet | °C | Refrigerant into the evaporator | Diagnostics. |
| BT23 | Suction line | °C | Refrigerant suction line | Diagnostics. |
| BT30 | Buffer tank | °C | One point in the tank | Not an average; stratification. |
| BT31 | DHW primary inlet | °C | Tank water into the plate heat exchanger | Low at standstill; high during a draw. |
| BT33 | Cold water | °C | Incoming potable water | Secondary side. |
| BT34 | Hot water | °C | Outgoing potable water | Secondary side. |
| BF1 | Tap flow | l/min | Flow on the secondary side | BF1 RPM is the flow sensor's speed. |
| GP1 | Heating pump | % | Heating-system circulation pump | Variable-speed. |
| GP2 | DHW primary pump | % | Pump between tank and plate heat exchanger | Misleadingly called a "circulation pump". |
| Fan | Fan | rpm | Ventilation fan speed | Air flow is not measured directly. |
| Compressor | Compressor | rpm | Compressor speed | Inverter-controlled. |
| QN8 | Shunt | % | Additional heat from the tank | 0 = closed. |
| BP1/BP2 | Pressure | bar | Suction/discharge pressure | Refrigerant diagnostics. |

---

## 11. Common misconceptions

| Incorrect claim | Correct picture | Why it matters |
|---|---|---|
| "The 175 L tank is a hot-water tank." | It is a buffer tank for heating water; DHW is through-flow. | Otherwise one assumes the tank can be drained like a DHW tank and that legionella is relevant. |
| "The compressor heats the tap water directly." | The compressor charges the buffer tank; the plate heat exchanger transfers heat to the tap water. | Explains why draw power can exceed compressor power. |
| "`dhwenergy` is the compressor's DHW production." | It is the draw meter on the secondary side. | Gives wrong COP and wrong energy balance if used as production. |
| "`dhwpower` × time is the draw energy." | `dhwpower` is instantaneous power from `BF1 × ΔT`; it reads ~5 % above the meter because the meter cannot resolve short draws. | Use `dhwenergy` for energy, `dhwpower` for power. |
| "A 6 kW heat pump cannot deliver 15 kW DHW." | It can deliver 10–20 kW briefly from the tank; the compressor recharges afterwards. | Explains why a small pump can serve a shower. |
| "BT30 is the tap-water temperature." | BT30 is one point in the buffer tank. | Stratification means the top can be much hotter. |
| "BT31 is the tank temperature." | BT31 is the primary inlet at the plate heat exchanger. | A low BT31 at standstill is normal. |
| "BT33/BT34 are tank sensors." | They are cold/hot water on the secondary side of the plate heat exchanger. | Used for draw power, not tank balance. |
| "BT14 is the heating-water return." | BT14 is warm extract air into the evaporator; the manual's label is misleading. | Otherwise one calculates the heating circuit incorrectly. |
| "`hp_status` says whether the immersion heater is heating." | `hp_status` follows the compressor; element-only DHW can read Idle. | Otherwise the element's DHW work is missed. |
| "The immersion heater is only for heating." | It can also charge the tank/DHW; `additiondhwdemand`. | Affects COP and energy attribution. |
| "The immersion heater heats the tap water directly." | It heats the tank/heating water; EP31 transfers the heat to the tap water. | Wrong hydraulic path and wrong COP model. |
| "The immersion heater's energy is in `dhwenergy`." | `dhwenergy` is the draw meter; the element's input is `additionalenergy`. | Double-counts or misses the element's DHW work. |
| "Capacity level 1–7 is litres." | It is a start/stop pair; the pair is the truth. | The level is not a volume and can be unknown on new firmware. |
| "`tap_water_cap` is litres of hot water." | It is a shower estimate from a tank model. | Not a volume and not the capacity level. |
| "The tank has a single temperature." | The tank is stratified; BT30 is one point. | Affects COP, capacity and control. |
| "The DHW outlet temperature is fixed." | It depends on flow, tank temperature and the plate heat exchanger approach. | Explains why BT34 varies between showers. |
| "GP2 is a hot-water circulation pump." | GP2 is the internal primary pump between tank and plate heat exchanger. | External HWC is a separate loop. |
| "Ventilation flow and tap flow are related." | They are independent: l/s air vs l/min water. | Wrong unit and wrong circuit. |
| "The minimum heating-water flow is the DHW flow." | 0.08/0.15 l/s heating water vs 5–12 l/min tap water. | Wrong sizing. |
| "`cop_dhw` is the instantaneous COP of the draw." | It is a charge-segment COP with a tank balance. | Misused as a shower performance figure. |
| "Lifetime counters give a lifetime COP." | They mix seasons, commissioning and additional heat; use the 30-day SCOP. | Wrong conclusion about pump performance. |
| "Cooling uses the DHW plate heat exchanger." | Cooling uses the heating system and the four-way valve. | Wrong hydraulic circuit. |
| "Defrost is always a reversed refrigerant circuit." | The firmware has passive defrost states; the mechanism is not publicly documented. | Do not infer valve positions. |
| "The QE is an air-to-water heat pump with an outdoor unit." | It is an exhaust-air heat pump with indoor units. | Wrong product category and installation. |
| "The tank can be used for a legionella cycle." | There is no stored potable-water volume; DHW is through-flow. | A legionella cycle does not apply to the DHW side. |
| "`compressor_power` is heating output." | It is electrical input. | COP is wrong if the two are mixed. |
| "`heatingpower` is a direct power sensor." | It is derived from a 0.1 kWh counter and held between steps. | Gives a false instantaneous power. |
| "`dhw_tank_liters` is a setting." | It is an identified volume from an element-only balance. | Live 177.9 L vs 175 L nameplate. |
| "175 L is the amount of hot water you can draw." | 175 L is the tank volume; the draw volume at 40 °C is 235 L (max 350 L with extra DHW). | Wrong capacity expectation. |
| "QE-4 and QE-6 have different tanks." | Both have a 175 L tank; they differ in compressor, air flow, minimum flow and refrigerant charge. | Wrong spare-part/installation assumption. |
| "These QE tank/DHW rules apply to every Qvantum model." | The integration is shared, but the 175 L buffer tank and through-flow DHW model is QE-specific; QA+QH and QG differ. | Applying the QE model to another product gives wrong DHW and COP conclusions. |

---

## 12. Safe AI answers

### "How does DHW work on a QE?"

> The QE has no domestic-hot-water tank. The compressor heats a 175 L buffer tank of heating water. When hot water is drawn, GP2 pushes tank water through plate heat exchanger EP31, where the heat is transferred to the cold potable water. The tap water is therefore through-flow: no stored potable-water volume and no legionella tank. Draw power can be 10–20 kW briefly because the tank discharges; the compressor recharges afterwards.

### "What is dhwpower?"

> `dhwpower` in Home Assistant is the instantaneous draw power, `4.186/60 · BF1 · (BT34 − BT33)`, published in W on every Modbus poll; it reads 0 W when no water flows. `dhwenergy` is the separate **energy** meter on the same secondary side: it counts 0.1 kWh steps and stays flat during a tank charge with no draw. The two do not integrate to the same figure, because the meter cannot resolve short draws — reconcile energy against `dhwenergy`, never by integrating `dhwpower`.

### "Why does BT31 read only 42 °C when the tank is 55 °C?"

> BT31 is at the primary inlet of the plate heat exchanger. When GP2 is stopped, the primary line is stagnant and cools, so BT31 can read 42 °C while BT30 reads 55 °C. When a draw starts, GP2 flushes tank-top water through and BT31 rises, often above BT30, because the tank is stratified. BT31 is neither the tank temperature nor the DHW outlet.

### "Why does hp_status say Idle when DHW is being heated?"

> `hp_status` follows the compressor, not the immersion heater. If only the immersion heater is charging the tank, the compressor can be stopped and `hp_status` can read Idle (0) even though DHW is being produced. Look at `additiondhwdemand`, `compressor_state` and `dhwenergy` instead.

---

## 13. Open questions

- **BT14/BT15:** The manual and the Modbus guide label them differently. The live values and the integration names support air in/out, but verify on the specific unit before using BT14 as a heating-water return. `[?]`
- **BT30's exact height in the tank:** It affects how representative it is of the tank mean and how `cop_dhw`/`dhw_tank_liters` should be read. `[?]`
- **GP2 scale:** The datasheet says scale 1, but the firmware reports tenths of a percent; the integration scales by 0.1. Check which version is active before reading GP2 as a percentage. `[R][I]`
- **Defrost mechanism:** Passive states exist, but the exact valve positions/heat source are not documented. `[?]`
- **Cooling capacity and limits:** The product leaflet mentions cooling, but detailed cooling curves and capacity are missing from the register guide. `[?]`
- **"Temperatur ut" (Normal/+ /++):** It affects the tap-water temperature but is not a Modbus register; the control path is outside the public map. `[I]`
- **Smart DHW:** A cloud function; local Modbus registers show status, but the full decision logic is not documented. `[R]`
- **Pool states:** They exist in the shared firmware map but are not part of normal QE residential operation. `[R]`

---

## 14. Sources and verification

- **`[M]`** Qvantum QE — Installation and user handbook, QCH EN 2515-A, 1011522-A (2026-01). Sections 3 (components), 4 (pipes), 5 (ventilation), 8 (user interface), 11 (technical data).
- **`[M]`** Qvantum QE — product leaflet/technical data (QE-4/QE-6), and the Swedish datasheet.
- **`[R]`** Qvantum — Modbus Communication, Quick-start guide QSG EN 2613-A, D100041. Holding and input registers, scales, status codes.
- **`[R]`** Qvantum — Hydraulic schematics QE, D100020.
- **`[L]`** Live QE-6, firmware 1.11.10, 2026-10-25/26: draw profiles, BT30/BT31/BT33/BT34, BF1, compressor state, `dhwenergy`, `dhw_meter`, `dhw_tank_liters` (177.9 L), `cop_dhw` (0.95 in an open segment).
- **`[I]`** `perosb/qvantum_custom_component`: `calculations.py`, `sensor.py`, `const.py`, `client/modbus/maps.py`, `docs/heating-curve.md`, `AGENTS.md`.

**Verification principle:** When the manual and the measurement disagree, the measurement wins, with its numbers. But a single measurement on one installation is not a general truth; check the firmware, installation and sensors before generalising.

---

## Appendix A — Glossary

| Swedish | English | Meaning in this document |
|---|---|---|
| Frånluft | Extract air | The warm air from the rooms entering the evaporator (BT14). |
| Avluft | Exhaust air | The cold air leaving the evaporator (BT15). |
| Bufferttank | Buffer tank / accumulator | 175 L of heating water; thermal battery. |
| Tappvarmvatten | Domestic hot water (DHW) | Potable water heated in the plate heat exchanger. |
| Genomströmning | Through-flow | Water is heated as it is drawn; no storage. |
| Laddning | Charging | The compressor/immersion heater raises the tank temperature. |
| Uttag | Draw | Tap water flows through the plate heat exchanger. |
| Värmebärare | Heating medium | The water in the heating system/tank. |
| Köldmedium | Refrigerant | R513A in the refrigerant circuit. |
| Elpatron | Immersion heater | 5 kW electric heat in the tank. |
| Tillsats | Additional heat | Immersion/shunt heat when the compressor cannot meet demand. |
| Växelventil | Diverting valve | QM10, selects heating or DHW charging. |
| Shunt | Mixing valve | QN8, mixes tank heat into the heating system. |
| Plattvärmeväxlare | Plate heat exchanger | EP31, the DHW heat exchanger. |
| Skiktning | Stratification | Hotter water at the top of the tank. |
| Framledning | Supply line | Water out to the heating system (BT11). |
| Retur | Return line | Water back from the heating system. |
| Börvärde | Setpoint | Desired temperature. |

---

## Appendix B — Minimum fact set for an AI

| Question | Safe answer |
|---|---|
| Does the integration only support the QE? | No; it targets Qvantum heat pumps and is expected to work with QA+QH and QG where the shared registers exist. The QE hardware model is QE-specific. |
| What is the QE? | An exhaust-air heat pump with a 175 L buffer tank and through-flow DHW. |
| Is the tank DHW? | No. It is heating water; DHW is a plate heat exchanger. |
| Where is DHW heated? | In EP31, with tank water on the primary side and potable water on the secondary side. |
| What drives the primary side? | GP2, an internal pump between tank and plate heat exchanger. |
| What does `dhwenergy` meter? | Tap-water energy on the secondary side, not the compressor's DHW output. |
| What does `dhwpower` meter? | Instantaneous draw power to the tap water, `BF1 × ΔT`; not an energy. |
| How do you calculate draw power? | `4.186/60 · BF1 · (BT34 − BT33)` kW. |
| Why can draw power be 20 kW? | The tank discharges; the compressor is 1–6 kW and recharges afterwards. |
| What is BT30? | One point in the buffer tank, not the whole tank. |
| What is BT31? | The primary inlet to the DHW heat exchanger; low at standstill. |
| What are BT33/BT34? | Cold/hot tap water on the secondary side. |
| What are BT14/BT15? | Air in/out of the evaporator; not a heating-water return. |
| What does `hp_status` mean? | The compressor state, not the immersion heater. |
| Is 175 L the amount of hot water? | No; the draw volume at 40 °C is 235 L (max 350 L with extra DHW). |
| Is capacity level 1–7 litres? | No; it is a start/stop pair. |
| Does the immersion heater heat tap water? | No; it heats the tank/heating water, and EP31 transfers heat to the tap. |
| Is `cop_dhw` the draw COP? | No; it is a charge-segment COP with a tank balance. |
| Is defrost reversed cooling? | Not documented; passive defrost states exist. |
| Can the QE cool? | Yes, through the heating system with dew-point protection; not through the DHW heat exchanger. |
