# Tap-water capacity (the 1–7 preset)

The Qvantum app offers a hot-water capacity level 1–7. On the pump that level is
only a **pair of temperatures** — hot-water start and stop — and the integration
treats the pair as the truth. This file records why, what the entity reports when
the pair is not in the table, and what the one repair issue means.

## The pair is the level

`TAP_WATER_CAPACITY_MAPPINGS` in `client/constants.py` maps a level to a
`(start, stop)` pair; `TAP_WATER_CAPACITY_START_STOP` is its inverse and is what
both transports and the entity use instead of rebuilding it. The write path goes
through `tap_water_capacity_pair()`, which raises rather than rounding: a level
the table does not carry is a programming error here, not a value to snap to the
nearest one.

There is **no holding register that carries the level**. In Modbus mode the level
is therefore *derived* from the pair, and the pump can move the pair on its own:
measured on a QE-4, 2026-10-22, 55/71 → 55/65 with no user action, while the app
kept showing level 5.

A pair outside the table is **not** rounded to a nearest level — that once
reported 55/65 as 2. Instead:

- `_derive_tap_water_capacity` leaves `tap_water_capacity_target` at the
  transport's own label (cloud) or `None` (Modbus);
- `native_value` answers only from the pair and reports `unknown`;
- the entity stays **available** (its own key may be `None` — that is the one
  exemption in the number platform's availability check), with
  `start_stop_pair`, `pair_mapped`, `nearest_capacity` and `reported_capacity`
  attributes;
- `_async_sync_tap_water_capacity_issue` keeps the one repair issue in step with
  the mismatch, and deletes every other issue id on each sync.

`nearest_tap_water_capacity_level` is only the entity's *attribute* hint, never
the state and never part of an issue text.

## The one repair issue

`tap_water_capacity_unmapped`, raised when the pair is **not in the table at
all** — no level exists, and the entity reports `unknown` with the pair and
`nearest_capacity` as attributes.

A transport label that disagrees with a *mapped* pair is deliberately **not** an
issue. The level is our own label derived from the pair, the cloud's is the
app's, and the entity already reports both: the state comes from the pair, and
`reported_capacity` carries the app's number. Answering a mapped pair with an
unfixable warning asked the user to reconcile two numbers the integration had
already decided between, and the text could only name the pair — "an unknown
preset" — which is false when the pair *is* level 4 in our table.

Releases up to 2026.11.9 also raised `tap_water_capacity_level_conflict`. Its id
is deleted on every sync along with the legacy unsuffixed
`tap_water_capacity_mismatch_<entry>` (raised up to 2026.10.18), because both
live in HA's issue store across restarts and an upgrading installation would
otherwise keep them forever.

**The issue is never raised on a read-only Modbus link** (`modbus_enabled` and
`client.writable is False`): the pair was then moved by the app or the cloud, the
integration has no write path back, and a current-firmware pump holds a pair
outside the table anyway — so the issue would ask for something nobody can do
from Home Assistant and would nag forever. Turning writes off clears an issue
that was already raised; the entity keeps reporting `unknown` with its
`start_stop_pair` / `pair_mapped` / `reported_capacity` attributes.

## The table is firmware-specific

The table has been unchanged since #270 and predates a Qvantum firmware change
that is now in the field — not only on newer pumps, and Qvantum installs that
firmware **automatically and mandatorily**, so no pump stays behind. The levels
now sit about 1 °C apart: a reporting QE4 shows **55/65 = level 5**, and its app
no longer writes 55/71. No current pump is expected to match the table.

The old table is therefore on a countdown, and the pair is the only stable
control surface. Re-pointing the table silently retargets every level write for
every user, so it waits for evidence — a per-firmware mapping accumulated from
cloud rows (level and pair arrive together there; Modbus has no level) or a
table published by Qvantum. The open work is exactly that; it is **not** a guess
to be filled in.

Note that the level is **not** a capacity figure: 175 L ≈ 0.20 kWh/K while a
6-minute shower needs ≈ 1.76 kWh, i.e. ≈ 8.6 K per shower, so a 1 °C step cannot
mean one shower and no mapping follows from physics.

## Writes

Cloud writes levels 2–5 as the level setting and only 1/6/7 as a start/stop
pair; Modbus always writes the pair. Both confirm — Modbus with `{"status":
"APPLIED"}`, the cloud PATCH with `{"status": "APPLIED", "res": {...}}` — so both
apply optimistically.

Because the state is derived from the pair, the optimistic write has to move the
pair too — that is what `extra_updates` is for. Updating only
`tap_water_capacity_target` showed the previous level until the next poll even
though the pump already held the new temperatures.
