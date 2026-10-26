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
The field list is in the [README](../README.md#qvantumexport_summary); in short:

| block | example content |
|---|---|
| device | manufacturer, model, firmware versions — `model` is empty on local Modbus, which reports serial and firmware only |
| plant | sensor mode, room-compensation factor, curve type, offsets, tap-water start/stop, the pump's supply limits |
| climate | the outdoor span observed, heating hours |
| curve | room gain (configured / applied / fitted, with validity, evidence hours, standard error, leave-one-out swing), readiness median and p95, the solar model |
| fitted | heat loss (W/K), time constant (h), thermal mass (kWh/K), DHW standing loss (kWh/day), cycling, SCOP, auxiliary-heat share, health grade |
| counters | curve reverts, trim updates, days with an actionable clamped or an extrapolated knot, and the date the counters were reset at |

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
2026.10.20 is the newest stable release and carries **v8**, so v9 is the first
number that needs a row of its own (and v9 therefore may not fold further changes
once it ships; before that, edit it in place as usual).

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
