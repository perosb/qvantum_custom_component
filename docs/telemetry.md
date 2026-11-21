# Anonymous summary sharing (opt-in)

The adaptive curve raises questions that a single installation cannot answer:

* does the pump's own `room_comp_factor` equal the room-gain slope fitted on the
  house, across pumps and building types?
* how often does the cold end of the learned curve actually bite — clamped, or
  extrapolated beyond the outdoor range the baseline was observed in?
* is a warm-end knot pinned at the pump's minimum supply a general problem or one
  house's quirk?

Half a year of one installation's data can suggest an answer; only several
installations can settle it. This page describes exactly what is shared, how it
is stored, and how to take it back.

## Consent

Nothing is sent unless you answered **"Bidra"**. The question is a repair issue
raised once, after a week of use, with two one-click answers.

* Declining is remembered; the question is not asked again.
* The answer can be changed at any time in **Settings → Devices & services →
  Qvantum → Configure → "Share anonymous operating data"**. Only a *changed*
  value is recorded, so opening the options for another setting never decides
  for you.
* The adaptive curve never depends on it: refusing changes nothing about how the
  integration works.
* The consent lives in `qvantum.telemetry.<entry_id>` in `.storage`, together
  with the installation token. Removing the config entry removes both
  (`async_forget`).
* The question is the fixable issue `telemetry_consent`, raised once after
  `PROMPT_AFTER_DAYS` (7) of use — existing installs are covered because the
  clock starts on the first start with this version.

## What is sent

One POST per week (at most), containing the same payload as
`qvantum.export_summary` plus the installation token in the request body only.
The complete field list:

| section | fields |
|---|---|
| device | manufacturer, model, firmware (**the display component only** — the cloud also reports two component builds, `display/cc/inv`, and Modbus has no register for them, so the export keeps the part both transports produce; device metadata, never user data). `model` is empty on local Modbus: the pump reports serial and firmware only |
| plant | `sensor_mode` (the register integer on both transports — the cloud's `bt2` / `ext_room_sensor` names are normalised in the export), `room_comp_factor`, curve type, `indoor_temperature_offset`, tap-water start/stop, `stop_heating`, and the pump's own supply limits (read on both transports; the cloud's curated `/settings` list does not carry them, so the client fetches `min_supply`/`max_supply` with the metrics and aliases them) |
| climate | the outdoor span the installation has been observed in, heating hours |
| curve | the trim layer (`trim_max_abs_c` — how close the largest trim is to the cap — and `trim_cap_days`, how long the oldest capped one has been capped, a duration rather than the diagnostics' date), room gain grouped as `configured` / `applied` / `fitted`, with `window`, `valid`, `notes`, evidence hours, standard error (with `t`) and leave-one-out swing; readiness `median_abs_c` **and** the signed `median_c`, `p95_abs_c`, `window_hours` and the room error over the same window (`room_error_median_abs_c`, `room_error_p90_abs_c`) — plus the `blocker` that says whether the gate failed or was never assessed (while the curve writes the readiness figures are `null` and `blocker` is `active`, because the deviation is then our own trims); the solar model (`a`, `b`, trust, days — the hourly `b` keeps its value while `trust` is 0 when it misses the t ≥ 2.5 gate, so the pair is not a bug), the day-block model's evidence (`daily_solar_t_stat`, `daily_solar_r2`, `daily_solar_valid`, `daily_solar_notes`: which gate refused it) and the wind column of the same day balance (identified `w` in W/K per m/s, its `t`, the evidence hours *and days* behind it and whether its gates let it act) |
| fitted | heat-loss coefficient (W/K), time constant (h), thermal mass (kWh/K), DHW standing loss (**kWh/day at a 40 K tank-to-ambient difference**, not the raw figure, with `reference_delta_k`, `loss_w_per_k`, the newest window's evidence and the count it rests on — `dhw_standing_episodes`, `dhw_standing_hours`), cycling, SCOP over the 30-day window **and** over the 90-day one (`scop_total_90d` — the health grade reads the 30-day figure, so the longer window is what separates a shoulder-season row from a poor installation), auxiliary-heat share, health grade, and the **coverage** behind each window (`scop_coverage_days`, `scop_coverage_days_90d`, `building_coverage_days`) — without them a SCOP over seven scattered days reads exactly like one over thirty |
| counters | curve reverts **and their per-code split** (`revert_reasons`: `switched_off`, `write_failed`, `write_not_applied`, `points_unverified`, `point_not_stuck`, `writes_disabled`, `no_device_id`, `table_invalid`, `other` — the total says how often, the code says why), trim updates, days with an actionable clamped or an extrapolated knot, and the date the counters were reset at — populated on every transport that can
run the curve, since the coordinator exists wherever the pump's settings can be
written |
| verdicts | reserved for derived analysis results (currently empty) |

It **never** contains: entity ids or names, coordinates, energy totals,
schedules, sensor identities, raw series, or a timestamp finer than a date. The
payload is built by whitelist in `summary.py`, and `tests/test_summary.py`
asserts the result carries no identifiers.

The `counters` block is counts, never a log of when: curve reverts, trim updates
and the days a clamped or extrapolated knot was lived with, plus the date the
counters were reset at.

## `SUMMARY_SCHEMA_VERSION`

This number is the pool's grouping key, so it moves for **a new field, a removed
field, and a field whose meaning changed** — the third is the one that gets
forgotten. Re-normalizing an existing figure is exactly that: v6 marks
`dhw_standing_loss` as a coefficient quoted at a fixed tank-to-ambient difference
(identified volume) instead of the raw daily figure at whatever tank temperature
its window saw, because two such readings pool side by side and neither is
comparable to the other. When a derived metric changes semantics, bump the
version **in the same PR** and say in the constant's comment what changed and
why; a test pins the floor so a silent revert cannot pass.

**Only a version that has shipped needs superseding.** The number exists to keep
two meanings out of one pool key, so a version that no release has carried has no
rows to protect: further schema-shaped changes on `main` before the next release
edit the current number in place and append to its comment instead of bumping
again. Check the released `manifest.json` (or the latest tag) before deciding —
2026.11.2 is the newest stable release and carries **v10**, so **v11** is the first
number that needs a row of its own (and v11 therefore may not fold further changes
once it ships; before that, edit it in place as usual). v9 added
`daily_wind_evidence_days` beside `daily_wind_cold_hours`: the wind gate wants
cold+windy hours **and** the days carrying them, and a row reporting only the
hours cannot separate "200 h on four days" (refused) from a window with no
evidence at all.

v10 adds the **evidence** behind the two readiness questions, because v9 could
report that a model was invalid but not why: the day-block model's `t_stat`,
R², `valid` and `notes` (an invalid model zeroes `b` and `trust`, so "the gate
is too strict" and "there is nothing to identify yet" looked identical); the
readiness `blocker` and `window_hours` (a median failure, a window that never
had the coverage to be judged, and `active` — the gate not assessed because the
curve writes — are three different answers); the signed `median_c`
beside the absolute one (a constant offset and noise around zero share a median
absolute value and need opposite answers); and the room error over the same
window (readiness compares against the pump's target, which carries the room
term, so a deviation cannot be weighed without it).

v11 adds `scop_total_90d` beside `scop_total`. The health grade is built from
the 30-day figure while `SCOP_WORST`/`SCOP_BEST` in `plant_analytics.py` are
documented as a *seasonal* range, so a shoulder-season row and a genuinely poor
installation produce the same letter. The pool could not tell them apart without
the longer window in the same row; the number already existed as an attribute on
the SCOP sensor, and the export is what makes it poolable. The longer window is
there to be **read beside** the letter, not to compute it: on the first 15 pooled
pairs it is lower in 11, and switching the grade to it would lag a fix by three
months and leave every installation younger than that without a letter
(`docs/derived-metrics.md`).

v11 also adds `counters.revert_reasons`, the per-code split of `reverts`. The
total already said how often an installation was kicked out of active control;
it could not say whether that was a transport failure, a pump rejecting a write,
a table we computed wrong or a user switching control off — four different
fixes, and the reason only ever existed in the installation's own log. The codes
are a fixed set (`REVERT_REASONS` in `const.py`), so the block stays counts and
never a log, and an absent code is exported as zero rather than omitted.

v18 adds `fitted.eta_s_percent` / `fitted.eu_class` and their `_90d` twins: the
measured SCOP expressed in the **EU energy label's** unit
(`ηs = SCOP / 2.5 · 100`, Regulation 811/2013) and the class that follows from it,
so a row can be compared with the class the product was declared with. Additive,
and deliberately separate from `efficiency_health_grade` — that letter is a
composite on our own 2.0–5.0 range, and the same window reads D there and A+ here.

v17 adds `fitted.scop_coverage_days_90d`, the observed days behind the 90-day
SCOP. That figure has been exported since v11 with no coverage beside it, so a
90-day SCOP over nine observed days read exactly like one over ninety — the
mistake `scop_coverage_days` already prevents for the 30-day window. Additive:
no existing field changes meaning, and the health grade still reads the 30-day
figure, so a consumer that learned the old shape keeps working. What it unlocks
is the comparison the pool could not make: both windows, each with its own
evidence, in the same row.

v16 does two things. `plant.max_heating_supply` stops being null on a cloud row:
the pump's own supply limits are not in the curated `/settings` list, so the
client now reads `min_supply`/`max_supply` from the internal `/values` endpoint
(the names the app itself uses) and aliases them. A consumer that learned "the
supply limits are null on cloud" is the reader this version exists for — the
same shape as v15's `counters`, and the reason it is a version rather than a
silent fill.

It also adds `fitted.dhw_standing_episodes` and `fitted.dhw_standing_hours`, the
evidence behind the standing-loss coefficient. That figure is an **EMA**, so the
published number is the same whether it rests on one tank-cooling night or
twenty — a two-day-old entry that had never been idle overnight and a mature
installation exported the identical field. `thermal_episodes` already does this
for the building fit; the standing loss was the one fitted figure in the row with
no window beside it. Both counts are `null` until one is accepted: an
installation upgraded from an earlier version carries a coefficient it learnt
before the counters, and `0` beside a non-null coefficient would read as
"measured, nothing there".

v15 moves what `counters` means on a cloud row. The block was documented as
*Modbus only* because the curve was, and now that the curve runs on an elevated
cloud account a cloud row carries reverts, trim updates and the days a knot was
clamped. A consumer that learned "`counters` is always zero on cloud" is exactly
the reader this version exists for; nothing in the block's shape changed.

v14 adds `curve.trim_max_abs_c` and `curve.trim_cap_days`. The trim layer was
not in the export at all, so the pool could not see that one of them was sitting
on `TRIM_MAX_C` — `counters.trim_updates` says how often the table changed, never
that the integrator had run out of room. Without the pair the question "is the
cap binding, and for how long?" is a reading of one installation's diagnostics,
and measured once two analyses of the same install reconstructed the age from
indirect series and landed two days apart. The age is a **duration**, not the
date `trim_cap_since` carries in the diagnostics: the counters block is counts
only, and a date would leave the installation.

v13 does two things to what a row can be weighed with. `device.firmware` becomes
the **display** component only: the device registry holds `display/cc/inv` when
the cloud wrote it and the display version alone when the Modbus probe did,
because cc/inv exist only in the cloud metadata — so the same pump could appear
in the pool twice, and the firmware column already listed both spellings. And
`fitted` gains `scop_coverage_days` and `building_coverage_days`, the observed
days behind the SCOP window and the heat-loss fit. Without them a SCOP over seven
scattered days reads exactly like one over thirty, which is what an installation
that only runs while its owner tests produces: measured on such an instance,
`heat_loss_w_per_k`, `thermal_time_constant`, `thermal_mass` and
`dhw_standing_loss` were all null while `heating_degree_hours` still came out,
because the fits need continuous windows and the aligned one skips gaps.

v12 fills `plant.sensor_mode` on cloud installations. Cloud/HTTP reports the
indoor sensor source as a **name** (`bt2`, `ext_room_sensor`) while Modbus
reports holding 9 as an integer, and the export coerces every plant setting
through a numeric check — so the name was dropped and the field read null. It
was the only plant field null on a cloud row: of 31 cloud rows in the pool,
`room_comp_factor`, `curve type`, `indoor_temperature_offset`, the tap-water
pair and `stop_heating` all arrived as numbers, and only the supply limits were
null, because the curated `/settings` list does not carry them. The export now
maps the two names to `SensorMode`'s integers, so a cloud row and a Modbus row
can be compared, and an unrecognised name still exports null rather than a
number.

### The installation token

A 128-bit random value generated locally when sharing is granted — never derived
from the serial number or anything else about the pump. It exists so the pool can
group rows from one installation and delete them on request. It is **not** part of
the exported file or the downloadable summary; only the uploader adds it, to the
request.

## Where it goes

A Cloudflare Worker (`telemetry-worker/` in this repository) stores the body in
KV under `v<schema_version>/<install_token>/<date>`. It is deployed by
`.github/workflows/telemetry-worker.yml` and deliberately **not** by
`release.yml`: a Cloudflare outage must never block a HACS release, and the
release zip only takes `custom_components/qvantum/`. The deploy skips itself when
the Cloudflare secrets or the KV namespace id are missing.

The uploader is a six-hour check that posts the summary at most once a week, and
only while consent is granted.

* **Retention:** two years per row, then it expires on its own.
* **No logs:** the Worker stores no IP address and does no request logging.
* **No analytics:** no cookies, no third-party calls.
* **Validation:** bodies over 16 KiB, unknown schema versions, non-ISO dates and
  identifier-shaped keys are rejected.

## Taking it back

```yaml
service: qvantum.telemetry_delete
```

Erases every row for this installation, using the token above — the token *is*
the credential, so only this installation can erase its own rows. Uploads stop
when sharing is turned off; the token is kept so that deletion still works after
a refusal, and is forgotten only when the config entry is removed.

```yaml
service: qvantum.telemetry_upload
```

Sends now instead of waiting for the week. Counters reset only **after** the
intake accepted the row, so a failed week retries rather than losing evidence.

## Seeing it before it leaves

`qvantum.export_summary` writes the identical payload locally (and
`/api/qvantum/summary` serves it as a download). That file *is* what would be
uploaded, minus the token — so the honest answer to "what do you send?" is a
command you can run yourself.
