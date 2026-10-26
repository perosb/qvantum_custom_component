# Tap-water capacity (the 1–7 preset)

The Qvantum app offers a hot-water capacity level 1–7. On the pump that level is
only a **pair of temperatures** — hot-water start and stop — and the integration
treats the pair as the truth. This file records why, what the entity reports when
the pair is not in the table, and what the two repair issues mean.

## The pair is the level

`TAP_WATER_CAPACITY_MAPPINGS` in `client/constants.py` maps a level to a
`(start, stop)` pair; `TAP_WATER_CAPACITY_START_STOP` is its inverse and is what
both transports and the entity use instead of rebuilding it.

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
- `_async_sync_tap_water_capacity_issue` keeps the repair issues in step with the
  mismatch.

`nearest_tap_water_capacity_level` is only the entity's *attribute* hint, never
the state and never part of an issue text.

## The two repair issues

Two **separate** issues, because the situations differ:

| Issue id | When |
|---|---|
| `tap_water_capacity_unmapped` | the pair is not in the table at all — no level exists |
| `tap_water_capacity_level_conflict` | the table maps the pair to one level and the transport reports another |

Both carry the **same** text on purpose. It names the pair and says the preset is
unknown to this integration and was probably set by hand or in the app. Naming a
level told the user to fix the side that was stale, and the conflict is still
identifiable by issue id and by the entity's `pair_mapped` /
`reported_capacity` attributes. Each has its own issue-id suffix, and every
other id is deleted on every sync — **including the legacy unsuffixed
`tap_water_capacity_mismatch_<entry>`**, which releases up to 2026.10.18 raised
and which lives in HA's issue store across restarts. Without that delete an
upgrading installation keeps the old issue next to the new one forever.

**Neither issue is raised on a read-only Modbus link** (`modbus_enabled` and
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
pair; Modbus always writes the pair. The cloud PATCH answers
`{"success": true}`, which is not APPLIED, so a cloud capacity write stays
poll-driven (up to 120 s) while the Modbus pair write applies optimistically.

Because the state is derived from the pair, the optimistic write has to move the
pair too — that is what `extra_updates` is for. Updating only
`tap_water_capacity_target` showed the previous level until the next poll even
though the pump already held the new temperatures.
