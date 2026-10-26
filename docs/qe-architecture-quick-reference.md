# QE quick reference for AI

**Purpose:** This is the short version. It is enough to answer correctly about the QE's basic operation. The full reference is [`qe-architecture.md`](qe-architecture.md).

## The model in one paragraph

The Qvantum QE is an **exhaust-air heat pump**. It takes energy from the house's outgoing ventilation air and delivers heat to a heating system and to domestic hot water. The heat is stored in a **175 L buffer tank of heating water** — not in a domestic-hot-water tank. Domestic hot water (DHW) is produced in **through-flow** in a plate heat exchanger: tank water on the primary side heats cold potable water on the secondary side at the moment it is drawn. The compressor charges the tank; during a draw the tank discharges and can deliver 10–20 kW for a few minutes, even though the compressor is only 1–6 kW.

## Ten facts that must be right

1. **The QE is an exhaust-air heat pump** — not an air-to-water heat pump with an outdoor unit. `[M]`
2. **The 175 L tank is a buffer tank for heating water** — not a DHW tank. `[M]`
3. **DHW is through-flow** in plate heat exchanger EP31 (< 0.8 L); there is no stored potable-water volume and no legionella tank. `[M]`
4. **The compressor charges the tank**; the tank is the heat source for the DHW draw. `[M]`
5. **Draw power can be 10–20 kW**; the compressor is 1–6 kW depending on model and recharges afterwards. `[L]`
6. **`dhwenergy` meters the draw on the secondary side** (BT33 → BT34 × BF1), not the compressor's DHW output. It stays flat during a tank charge with no draw. `[I][L]`
7. **`dhwpower` in Home Assistant is derived from the `dhwenergy` counter's 0.1 kWh steps** and is quantised; it is not instantaneous draw power. `[I]`
8. **Instantaneous draw power** = `4.186/60 · BF1 [l/min] · (BT34 − BT33) [K]` kW. `[L]`
9. **`hp_status` follows the compressor**, not the immersion heater. Element-only DHW can report `Idle` (0). `[I]`
10. **Three independent flows:** ventilation air (l/s), tap water (l/min), heating water (l/s). Do not mix them. `[M][R]`

## The DHW path

```
Buffer tank (hot water) → BT31 → GP2 → EP31 primary side → back to tank
Cold water → BT33 → EP31 secondary side → BT34 → tap
                         BF1 measures tap flow
```

| Sensor | What it is | Trap |
|---|---|---|
| **BT30** | One point in the buffer tank | Not an average; the tank is stratified. |
| **BT31** | Primary inlet to the DHW heat exchanger | Low at standstill (e.g. 42 °C) because the primary line cools; high during a draw (e.g. 60 °C). Not the tank temperature and not the DHW outlet. |
| **BT33** | Cold tap water in | Secondary side. |
| **BT34** | Hot tap water out | Secondary side; varies with flow. |
| **BF1** | Tap flow, l/min | Used in the draw-power formula. |
| **GP2** | Internal primary pump between tank and plate heat exchanger | Not an external hot-water circulation pump. |
| **BT14** | Warm extract air into the evaporator | The manual's label is misleading; not a heating-water return. |
| **BT15** | Cold exhaust air out of the evaporator | Can be close to 0 °C. |

## Three numbers to keep apart

| Number | Meaning | Typical |
|---|---|---|
| `P_draw` | Heat to the tap water right now | 10–20 kW |
| Compressor DHW heat | Heat the compressor adds to the tank | 1–6 kW |
| `compressor_power` | Compressor electrical input (input 93) | 0.3–2 kW |

## Tank energy

```
E_tank [kWh] ≈ 0.2035 · ΔT [K]
```

- 175 L × 4.186 kJ/kg·K ≈ 732 kJ/K ≈ 0.204 kWh/K.
- 16 K ≈ 3.26 kWh.
- 40 K ≈ 8.14 kWh.
- 55 → 40 °C ≈ 3.05 kWh.

This is why a 12–20 kW draw can last only minutes before the tank cools.

## Immersion heater (elpatron)

- **5 kW total**, split as **1+2+2 kW**, switched in 1–5 kW steps.
- It heats the **buffer tank/heating water**, not the tap water directly.
- It can run **in parallel with the compressor** and can be the **only heat source** when the compressor is off or defrosting. `hp_status` then reports `Idle`.
- `additiondhwdemand` is its DHW flag; `additionalenergy` meters its electrical energy. Element-only DHW is capped at COP 1.0.
- Maximum tank temperature **90 °C** with the immersion heater; the safety temperature limiter trips at **95 °C**.

## Common misconceptions (correct them directly)

| Wrong | Right |
|---|---|
| "The 175 L tank is a hot-water tank." | It is a buffer tank for heating water; DHW is through-flow. |
| "The compressor heats the tap water directly." | The compressor charges the tank; the plate heat exchanger transfers the heat to the tap water. |
| "`dhwenergy` is the compressor's DHW production." | It is the draw meter on the secondary side. |
| "`dhwpower` is instantaneous draw power." | It is derived from a 0.1 kWh counter and is quantised. |
| "A 6 kW pump cannot deliver 15 kW DHW." | It can deliver 10–20 kW briefly from the tank. |
| "BT30 is the tap-water temperature." | BT30 is one point in the buffer tank. |
| "BT31 is the tank temperature." | BT31 is the primary inlet at the plate heat exchanger. |
| "BT33/BT34 are tank sensors." | They are cold/hot water on the secondary side. |
| "BT14 is the heating-water return." | BT14 is warm extract air into the evaporator. |
| "`hp_status` says whether the immersion heater is heating." | `hp_status` follows the compressor. |
| "The immersion heater is only for heating." | It can also charge the tank/DHW. |
| "The immersion heater heats the tap water directly." | It heats the tank/heating water; EP31 transfers the heat to the tap water. |
| "The immersion heater's energy is in `dhwenergy`." | `dhwenergy` is the draw meter; the element's input is `additionalenergy`. |
| "Capacity level 1–7 is litres." | It is a start/stop pair; the pair is the truth. |
| "`tap_water_cap` is litres of hot water." | It is a shower estimate from a tank model. |
| "The tank has a single temperature." | The tank is stratified; BT30 is one point. |
| "The DHW outlet temperature is fixed." | It depends on flow, tank temperature and the plate heat exchanger approach. |
| "GP2 is a hot-water circulation pump." | GP2 is the internal primary pump; external HWC is separate. |
| "Ventilation flow and tap flow are related." | Independent: l/s air vs l/min water. |
| "`cop_dhw` is the instantaneous COP of the draw." | It is a charge-segment COP with a tank balance. |
| "Cooling uses the DHW plate heat exchanger." | Cooling uses the heating system and the four-way valve. |
| "Defrost is always a reversed refrigerant circuit." | Passive defrost states exist; the mechanism is not publicly documented. |
| "The QE is an air-to-water heat pump with an outdoor unit." | It is an exhaust-air heat pump with indoor units. |
| "The tank can be used for a legionella cycle." | There is no stored potable-water volume; DHW is through-flow. |
| "`compressor_power` is heating output." | It is electrical input. |
| "`heatingpower` is a direct power sensor." | It is derived from a 0.1 kWh counter. |
| "175 L is the amount of hot water you can draw." | 175 L is the tank volume; the draw volume at 40 °C is 235 L (max 350 L with extra DHW). |

## If you remember only five things

1. **The tank is not DHW.** It is heating water; DHW is through-flow.
2. **The compressor charges the tank; the tank delivers the draw.** This is why draw power can exceed compressor power.
3. **`dhwenergy` = draw, `dhwpower` = quantised derivation, `P_draw` = BF1 × ΔT.**
4. **BT30 = tank, BT31 = primary inlet, BT33/34 = tap water, BT14/15 = air in/out.**
5. **`hp_status` follows the compressor**, not the immersion heater.

## Key figures, QE-4 / QE-6

| | QE-4 | QE-6 |
|---|---:|---:|
| Nominal heating output | 4 kW | 6 kW |
| Heating output range | 1–4 kW | 1.5–6 kW |
| SCOP 35/55 °C | 4.68 / 3.88 | 3.84 / 3.23 |
| Air flow | 25–55 l/s | 40–70 l/s |
| Minimum heating-water flow | 0.08 l/s | 0.15 l/s |
| Buffer tank | 175 L | 175 L |
| Plate heat exchanger | < 0.8 L | < 0.8 L |
| DHW draw at 40 °C (EN16147) | 235 L | 235 L |
| Maximum DHW draw at 40 °C (extra DHW) | 350 L | 350 L |
| Refrigerant | R513A, 1.2 kg | R513A, 1.35 kg |
| Immersion heater | 5 kW (1+2+2) | 5 kW (1+2+2) |

Sources: Qvantum QE Installation and user handbook 1011522-A; Modbus QSG EN 2613-A; live QE-6 fw 1.11.10, 2026-10-25/26. See the full reference for the source tags `[M]`, `[R]`, `[L]`, `[I]` and `[?]`.
