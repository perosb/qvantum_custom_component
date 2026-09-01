# Feature gap analysis: HTTP, previous Modbus, current local Modbus

This compares three operating models of the Qvantum Home Assistant integration:

| Label | What it is | Code |
|---|---|---|
| **HTTP (cloud)** | Cloud-only. Email/password, metrics and controls via the Qvantum HTTP API. | Current stack, `modbus_tcp=false` |
| **Previous Modbus** | Hybrid overlay on an HTTP login: faster local reads, most writes still cloud. | `origin/main` `340c86d` (2026.8.4) |
| **Current local Modbus** | Exclusive local TCP. No HTTP session. Optional holding-register writes. | This stack (`feat/modbus-write-controls` / PRs #177–#181) |

Previous Modbus was “read faster locally, still talk to Qvantum Cloud for identity, firmware, extra DHW, SmartControl, and almost every setpoint.” Current local Modbus is “never open an HTTP session.”

---

## 1. Model

| | HTTP | Previous Modbus | Current local Modbus |
|---|---|---|---|
| Transport | Cloud-only | Hybrid overlay on HTTP login | Exclusive local TCP |
| Account | Required | Required (same form as HTTP, plus Modbus options) | Not used |
| If the other transport fails | n/a | Modbus read falls back to HTTP | No HTTP at all |
| Writes | Cloud API + `writeAccessLevel >= 20` | Most writes still HTTP; three holdings if `modbus_write` | Holdings only, and only if `modbus_write` is on |
| Dual client | No | Yes: aiohttp session **and** shared HA Modbus unit | Modbus unit only (`_session = None`) |

Older branches (`modbus`, `modbuswrite`, `localonly`, `gate_modbuswrite`) were overlays on the same hybrid architecture, not a different product.

---

## 2. Setup and identity

| | HTTP | Previous Modbus | Current local Modbus |
|---|---|---|---|
| Setup | Email/password | Same form, plus Modbus host/port/unit/write as options | Separate Cloud vs local menu |
| Unique ID | Serial, else title | **Title** (`Qvantum QE-6 (12345)`) | **Probed serial** (inputs 180–184) |
| Device serial | Cloud `get_primary_device` | Cloud, then cache / device registry if cloud is down | Identity island 180–193 (QAD EN 2609-AXC) |
| Firmware | Cloud metadata + maintenance poll (`display` / `cc` / `inv`) | Same, with timeout warning if cloud is down | One `major.minor.patch` from 191–193 on `DeviceInfo.sw_version` |
| Model | Cloud vendor/model | Cloud vendor/model | Probe sets `vendor="Qvantum"`; **no model** |
| `hpid` / device id | Cloud heat-pump id | Cloud heat-pump id | Local serial used as device id |
| Entity unique IDs | `qvantum_{metric}_{hpid}` | Same, **cloud numeric id** | Same pattern, but `hpid` is the **serial** |
| Internet | Required | Required to add; later start can limp if cache exists | Not required after HA 2026.9 |
| `single_config_entry` | One instance | One instance | One instance; Cloud XOR local |

Identity IP registers 186–189 are decoded and unused in all three.

Current local unique-id is an upgrade vs previous (title collisions / `(None)` titles). Reconfigure can migrate title → serial. There is still no per-board firmware split locally (display / cc / inverter remain cloud-only sensors).

### Migration note

Reinstalling a pump that was on the previous overlay as current local-only **creates new entities**. Previous unique IDs used the cloud heat-pump id; current local uses the probed serial. Config-entry unique id also changes (title → serial). A fresh local install next to an old overlay entry is not possible anyway (`single_config_entry`).

---

## 3. Sensors and telemetry

**Shared live data** (temps, flows, relays, energy, `hp_status`, fan RPM, …) exists in all three.

**Always created:** `totalenergy`, `latency`, `hpid`.

### HTTP-only (not on the Modbus input map)

Gone in current local. Previous Modbus could still show some of these via HTTP fallback:

- `bp1_pressure`, `bp1_temp`, `bp2_pressure`, `bp2_temp`, `fan0_10v`
- Cloud extra-DHW countdown (HTTP `tap_stop` epoch from the pump/cloud)
- Firmware sensors: `display_fw_version`, `cc_fw_version`, `inv_fw_version`, `firmware_last_check`
- Access: `expiresAt`
- Default-disabled HTTP metrics with no register, including `calc_suppy_cpr`, `dhw_outl_temp_*`, `guide_*`, `price_region`, `heatingreleased` / `coolingreleased` / `compressorreleased` / `additionreleased`, `inputcurrent1–3`, …

Previous Modbus **still created** cloud `tap_stop`, firmware, and access sensors even when Modbus was on (often stale or empty if the cloud was down). Current local **hides** firmware/access and leftover cloud sensors, and cleans those leftovers out of the entity registry. Extra-DHW `tap_stop` is still created locally from the persisted restore epoch (`_extra_dhw_restore_at`), not the cloud countdown.

### Modbus-only (HTTP never had these as first-class live sensors)

Previous and current local both have them; HTTP does not at the default poll:

- Derived: `heatingpower`, `dhwpower`, `tap_water_cap`, `tap_water_minutes`
- `compressor_state`, `smart_dhw_control_status`, `picpin_relay_pump` (see below)
- Holding as sensor: `start_cooling_temp`
- Binary: `additiondemand`, `additiondhwdemand`, `cooling_prioritytimeleft`
- Default poll ~15 s vs HTTP ~120 s

### `tap_water_cap` is not the same metric

On HTTP it is a cloud value (disabled by default). On Modbus it is a local shower-capacity estimate from `bt30` / `bt33` / `bf1_l_min`. Same entity name, different meaning.

### Polled but never shown

`picpin_relay_pump` is in the Modbus-only metric list, but `picpin_` is in `EXCLUDED_METRIC_PATTERNS`, so there is no sensor or binary sensor. Same for `gp10`, `qn8_*`, `gp3`, `ha12`.

---

## 4. Controls

Previous Modbus **did not** route general setpoints to holdings. `update_setting`, climate, fan, extra DHW, tap-water start/stop, indoor target/offset all still hit the cloud. Only three things wrote locally (entity-gated, not API-gated):

- `dhw_stop_extra` (holding 59)
- `room_temp_external` (holding 14) — only when `use_operation_sensor == 4`
- `use_operation_sensor` (holding 9)

Current local routes every mapped setting through holdings when `modbus_write` is on. Cloud-only controls are denied, not sent to HTTP. `_ensure_modbus_write_allowed()` refuses writes if the option is off. Write access no longer needs cloud `writeAccessLevel >= 20` except for those cloud-only controls.

If `modbus_write` is off, current local is **read-only**. Previous Modbus with write off could still change almost everything via cloud (if access ≥ 20).

### Writable on current local (needs `modbus_write`)

| Control | Holding | HTTP equivalent |
|---|---|---|
| Extra DHW on/off / 60-min button / service | 53 `dhw_mode` Extra(2) / Normal(1) | `set_additional_hot_water` with `stopTime` |
| Indoor target (climate) | 12 `desired_indoor_temp` | HTTP settings |
| Indoor offset | 15 `heating_offset` | HTTP settings |
| DHW start / stop | 56 / 57 | HTTP settings |
| Capacity 1–7 | start/stop pair (always; no capacity register) | HTTP capacity except custom 1/6/7 |
| Fan preset off/normal/extra | 68 `ventilation_state` | Timed `set_fan_mode` command |
| `op_mode`, `man_mode`, `op_man_dhw`, `op_man_addition` | 1 / 2 / 5 / 4 | HTTP `update_settings` |
| `room_comp_factor`, `fan_normal`, `fan_speed_2` | 13 / 70 / 69 | HTTP |
| `dhw_stop_extra`, `room_temp_external`, sensor mode | 59 / 14 / 9 | Same as previous local writes |

`start_cooling_temp` (38) is **readable** as a sensor; there is no number entity to write it.

Climate is heat-only. `async_set_hvac_mode` is a no-op in **all** modes. Target-temperature is only offered when `sensor_mode` is `bt2` or `1`.

### Not writable locally (HTTP / previous hybrid still can)

| Control | Why |
|---|---|
| **SmartControl** (`use_adaptive`, `smart_sh_mode`, `smart_dhw_mode`) | Constraint: never write SmartControl locally. Select stays cloud-only. |
| **`enable_sc_sh` / `enable_sc_dhw`** | Readable as inputs 163–164; no holding in the write map. Switches exist but are unavailable. |
| **`elevate_access`** | Cloud grant flow. The button is not created in local Modbus mode. |
| **Timed extra DHW** | Cloud has pump-side `stopTime` / indefinite / cancel. Local writes Extra/Normal and Home Assistant restores Normal from a restore timer with a persisted deadline (`tap_stop` from that epoch). |
| **Timed fan boost** | Cloud extra fan uses a ~120 minute stop timestamp. Local writes 0/1/2 with no restore timer (sticky). |

SmartControl **status** can still be **read** on Modbus (`smart_dhw_mode` 161, `smart_dhw_control_status` 162, `enable_sc_dhw` 163, `enable_sc_sh` 164). `use_adaptive` is derived (`smart_dhw_mode != -1`).

---

## 5. Extra DHW

| | HTTP / previous Modbus | Current local |
|---|---|---|
| Mechanism | Cloud command with end epoch | Holding 53 Extra vs Normal; HA restore timer for timed extra |
| 60-min button | Pump/cloud enforces the window | Restore timer with persisted deadline (60 min) |
| Extra switch ON | HTTP `minutes=-1` (indefinite) | Extra mode, **no timer** |
| Service `qvantum.extra_hot_water` | Always (uses HTTP); 0–480 min, default 120 | Registered; writes holding if `modbus_write`; persisted restore timer for `minutes > 0` |
| `tap_stop` sensor | Yes (cloud countdown) | Yes, from `_extra_dhw_restore_at` |
| HA restart during the hour | Cloud still ends extra | Restore deadline is persisted; Normal is written when it expires |

Turning `modbus_write` off cancels a pending restore timer.

---

## 6. Holdings present in the datasheet but unused

The holding map includes many registers that are **not** in `MODBUS_HOLDING_TO_SETTINGS_MAP`, so they are neither entities nor writes. Same on previous and current; they were never productized:

- `unit_on_off` (0), `allow_cooling` (3)
- `time_between_modes` (6), `allow_addition_temp` (7), `filtertime_outdoor` (8)
- Heating: outdoor stop, min/max supply, curve type, temp compensation (18–23)
- Cooling: offset, dew point, min supply (36, 39, 40)
- DHW: `dhw_start_extra` (58), `dhw_outlet_temp` (60), `dhw_uninterrupted_cooling` (61)
- Pump speeds (63–66)
- `fan_speed_extra` (71), `compressor_fan_speed` (72)
- Priority times (73–75)
- `bt12_mounted` (83), `qs_unit_connected` (84), `sg_enabled` (88)

---

## 7. Gain / lose

### Current local vs HTTP

- **Gain:** no cloud, faster poll, derived power / DHW capacity, extra relays/state, local writes for the mapped setpoints.
- **Lose:** SmartControl, elevate access, compressor/inverter/display firmware sensors, bp1/bp2, `fan0_10v`, timed fan boost, any HTTP-only disabled metric, vendor/model from inventory. Extra-DHW `tap_stop` remains (local restore epoch, not the cloud countdown).

### Current local vs previous Modbus

- **Gain:** works with no account and no internet; serial unique id; no refused dual-span HTTP; extra DHW / climate / fan / DHW start-stop / op-mode actually write locally; cloud-only entities are hidden instead of sitting unavailable; write access does not need cloud elevation.
- **Lose:** HTTP fallback if Modbus drops; pump/cloud-enforced extra DHW `stopTime` (local `tap_stop` is HA’s persisted restore epoch); SmartControl and elevate-access while “on Modbus”; bp1/bp2/`fan0_10v` via fallback; write access via cloud when `modbus_write` is off; stable cloud `hpid` in entity unique IDs.

### Previous Modbus vs HTTP

Was mostly “HTTP plus a faster read path.” Controls did not become local. Offline was “start from cache,” not “run forever.”

**Unchanged read surface** across previous and current Modbus: heating/DHW power, tap-water cap (local meaning), demand binary sensors, relay sensors, 15 s poll, shared HA Modbus unit, derived `hp_status` from `compressor_state`.

---

## 8. Docs vs code

`README.md` still describes the **previous** model:

- Setup: sign in with a Qvantum account (no local-only path)
- “Stable backup path for metrics when REST data is unavailable”
- “Other configuration/set commands continue to use the existing HTTP API”

That is no longer true on this stack. Local is XOR cloud; writes never fall back to HTTP.

---

## 9. Highest-value remaining local gaps

1. Decide whether unused holdings (on/off, cooling allow, curves, pump speeds) should become entities.
2. Confirm SmartControl stays permanently cloud-only (current constraint).
3. Update README for exclusive Cloud vs Modbus setup, identity probe, and holding writes.
4. Entity unique-id migration for users who already ran the hybrid overlay (cloud `hpid` → serial).

Done in this persist-across-restart work: extra-DHW restore deadline is stored and resumed after a Home Assistant restart; local `tap_stop` is the restore epoch.
