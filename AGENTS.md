# Qvantum Home Assistant — agent notes

HA integration for Qvantum heat pumps. One config entry, two exclusive transports:
**cloud HTTP** XOR **local Modbus TCP**. Requires HA **2026.9+**. HACS zip:
`custom_components/qvantum/`.

## Layout

```
custom_components/qvantum/
  __init__.py                 # setup/unload, RuntimeData, migrations
  coordinator.py              # poll, derived metrics, extra-DHW helper
  maintenance_coordinator.py  # firmware / elevate-access (cloud)
  curve_coordinator.py        # adaptive curve: forecast, readiness, trims (Modbus only)
  efficiency_coordinator.py   # rolling SCOP / aux / building / DHW / cycling
  extra_dhw.py                # ExtraDhwTimer (HA Store + async_call_later)
  calculations.py             # power / tap-water / instantaneous COP
  statistics.py               # shared recorder long-term-statistics helpers
  entity.py                   # QvantumEntity, icons, write-access mixin
  diagnostics.py              # config-entry support dump
  summary.py                  # identifier-free summary export + download view
  telemetry.py                # opt-in consent, token, uploader
  repairs.py                  # telemetry consent fix flow
  config_flow.py
  const.py                    # HA keys; re-exports client constants
  sensor.py, binary_sensor.py, climate.py, number.py, switch.py,
  button.py, select.py, fan.py, water_heater.py, calendar.py
  device_trigger.py, device_condition.py, device_automation_helpers.py
  services.py / services.yaml
  # pure math, no HA imports:
  efficiency.py, heating_curve.py, solar_gain.py, building.py,
  dhw_loss.py, plant_analytics.py
  open_meteo.py               # forecast/history fetch (temperature, GHI, wind)
  client/                     # vendored comms — no Home Assistant imports
    protocol.py, exceptions.py, constants.py, models.py
    cloud/  modbus/
  www/                        # Lovelace card (qvantum-curve-card.js)
  translations/               # cs da de en es fi fr hu nl pl sv
  test_data/
tests/
docs/heating-curve.md
docs/releases/<tag>.md       # generated per-release notes + README.md index
```

`config_entry.runtime_data` (`RuntimeData`): `coordinator`,
`maintenance_coordinator`, `device`, `client`, `extra_dhw` (Modbus only),
`curve_coordinator` (Modbus only) and `efficiency_coordinator` (both
transports), Modbus host/port/unit. Platforms read `runtime_data.coordinator`
and `device`.

Writes: shared ones go through `QvantumClient` on `coordinator.client`;
cloud-only / Modbus-only ones through coordinator helpers
(`async_set_extra_tap_water`, `async_write_metric`, `async_set_smartcontrol`,
`async_elevate_access`). No `isinstance` on transport.

## Client vs Home Assistant

`client/` is a future standalone library. Enforced by `tests/test_client_package.py`.

- Never import `homeassistant*` or `custom_components*` from `client/`;
  relative imports stay inside it. `client/__init__.py` must not import `cloud`
  or `modbus` (would pull `aiohttp` / `modbus_connection` on every load). HA
  `const.py` imports `client.constants`; import transports from `client.cloud` /
  `client.modbus`.
- Canonical names only. Both transports return HTTP-shaped payloads
  (`{"metrics"}`, `{"settings"}`); a successful write is `{"status": "APPLIED"}`
  (`heatpump_status` counts too) — check with `client.models.result_applied`.
  Cloud writable entities (`switch`, `number`, …) stay in `REQUIRED_METRICS`.
- Extra-DHW **duration** and its derived calc stay in HA. Cloud encodes minutes;
  Modbus writes Extra/Normal and `ExtraDhwTimer` restores Normal — construct the
  timer only when Modbus is enabled.
- Sessions: cloud uses `async_get_clientsession(hass)` (never own one); Modbus
  uses HA 2026.9 `async_get_unit` and the client never opens/closes TCP. Poll
  `latency` is coordinator telemetry, not a register. Prefer named setters:
  `write_metric` is Modbus, cloud goes through the `set_smartcontrol` /
  `update_settings` coordinator helpers.
- A holding register only reaches the coordinator when
  `MODBUS_HOLDING_TO_SETTINGS_MAP` gives it a canonical name (`get_settings`
  filters by that map). A register in `MODBUS_HOLDING_REGISTER_MAP` without a
  settings entry is polled and then dropped, so no entity can exist for it
  (holding 74 is deliberate: the app has no cooling-priority setting).
  `compressor_power` (input 93) must stay in the modbus metrics payload even
  though `powertotal` folds it in: its diagnostic sensor records the long-term
  statistics `compressor_power_avg` reads, and a popped key left that average
  permanently `None`.
- Modbus client: every operation is bounded by an operation timeout and maps
  failures to `TransportError`; holding writes are validated for signedness and
  the datasheet MIN/MAX (`MODBUS_HOLDING_RANGE`, `client/modbus/model.py`)
  before encoding. Never invent registers or ranges — `client/modbus/maps.py` is
  the datasheet.
- Exceptions: `AuthError`, `TransportError`, `RateLimitError` (aliases
  `APIAuthError`, `APIConnectionError`, `APIRateLimitError`). Never name a class
  `ConnectionError`.
- Device-registry lookups are **scoped to the owning config entry**:
  `async_get_device_by_identifier((DOMAIN, ident), entry.entry_id)`, or
  `async_get_device_by_connection` / `async_get_devices`. Identifiers and
  connections are only unique *per config entry* since HA 2026.8, so
  `async_get_device(identifiers=…)` is ambiguous, logs a
  `homeassistant.helpers.frame` deprecation warning on **every call** (silent
  until the summary export runs, so a clean startup log proves nothing) and stops
  working in HA 2027.8. Same for `via_device` in `async_get_or_create` /
  `DeviceInfo` — pass `via_device_id`. Do not create or look up devices inside
  `client/`.

## Git

**Never push to `main`** (no force). Land only via PR; squash-merge only when
asked. `main` takes squash merges only and requires **every review thread
resolved**: after pushing a fix, reply in the thread and resolve it, or
`mergeStateStatus` stays `BLOCKED` with green CI. Every push re-runs the `review`
workflow, so batch documentation with the code change.

1. New feature branch from latest `main`. Never commit on `main`.
2. Conventional commit (table below).
3. `git push -u origin HEAD` — that branch only.
4. `gh pr create` against the default branch. Stay on the feature branch.
5. Keep the PR `## Summary` current as the diff evolves — it feeds the
   per-release changelog.

Do not ask permission for these steps. Do not merge unless asked.

**One concern per PR.** A follow-up — even in a file an open PR touches — gets
its own branch from the latest `main`, never an append to that PR's branch.
Before pushing to a branch, check `gh pr view <n> --json state`: a merged PR's
branch is gone, so branch from `main` again.

**This file is part of the change.** A PR that alters architecture,
derived-metric semantics, persistence or this workflow updates `AGENTS.md` in
the same PR — it is injected as the authoritative note for the next session, so
a version describing the old behaviour is worse than no note at all.

`/root/.gitconfig` does not survive a container restart, so set identity and the
credential helper **per repo**:

```bash
git config --local user.name "Per Osbäck"
git config --local user.email "982752+perosb@users.noreply.github.com"
git config --local credential.helper '!gh auth git-credential'   # or push fails on username
```

**Worktrees live in `${TMPDIR:-/tmp}`.** Never create `<repo>-pr-<n>` next to the
checkout, and never switch the primary checkout to inspect a PR (another session
may own it). Exception: a fresh single-session clone (CI) — use that checkout.

```bash
git fetch origin <branch>
git worktree add --detach "${TMPDIR:-/tmp}/qvantum-review-<n>" origin/<branch>
# inspect / pytest there
git worktree remove "${TMPDIR:-/tmp}/qvantum-review-<n>"
```

### Stacked work

Official `gh stack` (`gh extension install github/gh-stack`), one concern per
layer, landing on `main` in order.

- `gh stack init <b1> <b2> …` (bottom → top) or `gh stack add` / `link`;
  `gh stack sync` (atomic `--force-with-lease`, prefer over `gh stack push`),
  then `gh stack merge --yes --squash` (or `gh stack merge <pr>`).
- **`gh stack merge` refuses a stack that was never submitted**, so a local
  `init` alone buys nothing. `submit --auto` rewrites each PR body from its
  commit message and throws away the hand-written `## Summary` — write the
  bodies yourself or edit them afterwards.

A child PR based on the parent branch (with `Depends on #N`) is not
hand-stacking: it keeps the child diff clean and is the path to use when the
stack is not submitted. After the parent's squash merge, rebase once with
`git rebase --onto origin/main <old-parent-tip> <child>` (no conflicts when the
parent's content is identical), push `--force-with-lease`, then
`gh pr edit <child> --base main`. Prefer **one PR with two commits** when the
second rewrites the first one's code — two review rounds over the same function
cost more than one.

## Commits and PRs

Title: `type: Imperative subject`. Body = *why* when the subject is not enough.

| Prefix | Use |
|---|---|
| `feat:` | New / user-visible behavior |
| `fix:` | Bug |
| `refactor:` | Structure, no intended behavior change |
| `test:` | Tests only |
| `docs:` | Docs only |
| `chore:` | Tooling, CI, deps |

Release automation owns `Update for new version <tag>` — leave it to CI.

Title and body are written in **English**, always — the `## Summary` is expanded
into the per-release changelog, which is English, and a Swedish body leaves the
release notes untranslatable. Chat and analysis can be Swedish; anything posted
to GitHub is not.

PR body:

```markdown
## Summary
- What changed. Call out cloud vs Modbus and user-visible behavior.

## Test plan
- [x] `pytest` — N passed, coverage %
- [ ] In HA, … (only if a unit test cannot prove it)
```

The `## Summary` feeds the per-release changelog (bullets expanded one by one
into `docs/releases/<tag>.md`), so write it in user-facing language; `## Test
plan` is ignored. End the body with `Closes #<n>` when the PR resolves an issue.
Labels `bug` / `enhancement` / `chore` feed release-drafter.

### Documentation is part of the change (and is checked)

The `docs` job in `test.yml` fails a pull request that touches
`custom_components/qvantum/**` without also touching `AGENTS.md`, `README.md` or
`docs/**`. The rule is deliberately coarse — it only asks whether *any* prose
moved, because "does this need a sentence?" is the author's judgement — and a
PR that genuinely needs none opts out with the **`docs-not-needed`** label,
which the job re-reads on `labeled`/`unlabeled` so no empty commit is needed.
Translations, tests, `docs/releases/**` and the workflows do not count as
behaviour, so a test-only or tooling PR passes on its own.

Passing the job is not the bar: the automated reviewer (`pi-review.yml`,
prompt in `.github/pi/review-prompt.md`) also checks that the *sentence which is
now wrong* is the one that changed. What has to move, when:

| Change | Documentation |
|---|---|
| Derived metric, gate, threshold, persisted field, `SUMMARY_SCHEMA_VERSION` meaning | `AGENTS.md` and the metric's section in `docs/` |
| New snapshot / diagnostics field | the field lists in `AGENTS.md` and `docs/` |
| New entity, option, service, user-visible behaviour | `docs/` (and `README.md` for the summary export's field list) |
| A measured constant or protocol behaviour (a probe, a verified register) | the place that documents the measurement, with its numbers |
| Workflow, tooling, release automation | `AGENTS.md` |

### Release automation

`release.yml` is the only writer of `manifest.json` / `const.py` (both hold the
version) and rebuilds the zip asset; `docs/releases/<tag>.md` comes from
`.github/scripts/generate_release_changelog.py`. Both files document their own
details — the rules below are what is easy to break:

- Every release event — stable and pre-release, never drafts. Triggers are
  `published`, `released` **and** `edited`, and stability comes from the API, not
  the payload: this repo publishes a draft as a **pre-release** and promotes it
  later, and a promotion arrives with `prerelease: true`. Only a stable release
  bumps `manifest.json`/`const.py`; a pre-release commits the notes only. The
  bump also applies when there is nothing to commit, and the checkout is stamped
  with the tag right before zipping on **every** run. `update_hacs_manifest.py`
  re-reads both files and fails the run on a mismatch.
- The commit is built on the **branch tip**, not the tag (a promotion
  regenerates notes the pre-release already committed, and a tag-based commit
  conflicts on `docs/releases/<tag>.md`), while the **asset** re-checks out the
  tag after pushing, so a recovery run cannot ship branch-tip code under the tag
  version. Pushes use an SSH deploy key that is a ruleset bypass actor and never
  force-push: build on `origin/main`, fast-forward.
- Theme grouping: the script reads the release-drafter body for the PR list and a
  model returns `theme → PR numbers + prose`; the grouping is stored in a hidden
  comment and reused (`--retheme` forces a new one), with `New features` /
  `Bug fixes` as the deterministic fallback. Other labels collapse into one
  internal-changes footer line, which themes never include.
- The overview is rewritten for end users once via OpenRouter
  (`~openai/gpt-luna-latest`) and preserved on regeneration; without
  `OPENROUTER_API_KEY` (or on any API error) a deterministic title-based one is
  used. Only the newest pre-release keeps a file — older ones are pruned with
  their release-body link, since a pre-release and its stable share PRs.
- **Actions → Release → Run workflow** with the tag is for a failed or missing
  run (bumps the version on `main`, rebuilds the zip). **Actions → Release
  highlights → Run workflow** on the draft writes the overview, and nothing else,
  so the publish email carries it.

Regenerate one release with:

```bash
python3 .github/scripts/generate_release_changelog.py --tag <tag> --polish --themed --update-link
```

## Code reviews

When asked to review, **post on GitHub** (`event: COMMENT`). Chat summary is not
enough. Verify in a `/tmp` worktree (see *Git*).

- Inline on changed lines (diff RIGHT). One or two sentences of *why*, then a
  concrete fix. English.
- If the fix is a drop-in replacement, end the comment with a `suggestion` fence
  only (no +/- , exact whitespace, range = replaced lines). Skip the fence when
  the fix spans files or needs more than the highlighted lines.
- Max substance: correctness. Do not invent nits. Skip pre-existing issues on
  untouched lines unless the diff newly depends on them.
- Publish immediately. Do not approve or merge unless asked. You cannot approve
  your own PR.
- After pushing a fix, resolve the GitHub thread (`resolveReviewThread`).
- Clean diff: still publish a short overview with empty `comments`.
- Confirm the PR `## Summary` still matches the final diff — a stale description
  ships wrong release notes.

**Flag**

- Wrong Modbus register or cloud endpoint; holding vs input (input 88 =
  `compressor_run_time` — the runtime counter lives in the input space).
- Cloud-only entities in Modbus mode, or the reverse.
- `client/` importing HA, or HA `isinstance` on transport.
- An unscoped device-registry lookup (`async_get_device(identifiers=…)`) or a
  deprecated `via_device` in `DeviceInfo` — both are per-entry now, see
  *Client vs Home Assistant*.
- Missing translations/tests, or coverage below **92%**.

## Tests

```bash
./run-tests.sh          # venv + requirements-test.txt + pytest
python -m pytest        # repo root; pytest.ini sets coverage + timeout
```

- Coverage floor **92%**. Timeout **30s** per test.
- Modbus tests inject `modbus_connection.mock.MockModbusConnection` — no TCP.
- `tests/test_api.py` `QvantumAPI` is test-only. Do not add a production facade.
- Cancel leftover asyncio tasks in `finally`.
- Dual-mode tests (HTTP and Modbus on one object) are obsolete.

## Entities and translations

Edit all `translations/*.json` together and validate the JSON. Unique IDs and
config-entry migrations live in `__init__.py` — bump `CONFIG_VERSION` and add a
migration test when the entry schema changes.

Icons on `QvantumEntity`. Cloud-only entities (SmartControl, firmware boards,
access expiry, elevate-access) must not be created in Modbus mode.

Entity category: machine-internal telemetry and settings read-backs
(refrigerant/air-side BT sensors, refrigerant pressures, compressor/pump/fan
speeds, lifetime counters, valve position, derived diagnostics such as the
heat-loss or heat-meter figures) go under `EntityCategory.DIAGNOSTIC`. Primary
temperatures (outdoor/indoor/tank/heating flow), energy and power counters,
tap-water comfort, solar exposure and the headline efficiency figures
(COP/SCOP/aux share/health) stay in the main UI. Controls are never diagnostics;
`alarm_active` is a PROBLEM.

Availability: `available` ANDs its data check with `_coordinator_available` (the
coordinator's `last_update_success`), because HA keeps stale data after a failed
poll. Availability is not a write guard — write methods call
`_require_write_access()` (`QvantumAccessMixin`) first, because services bypass
availability (`vacation_mode` is exempt). Readability must not depend on write
access for state-reporting entities (climate, water_heater): they stay available
and gate their write features (`supported_features`). Control-only entities
(fan, switch, number, select, button) gate availability on write access, so no
control is offered when a write would fail.

Optimistic writes (`handle_setting_update_response` / `async_apply_setting`) land
in `values` before the next poll, so a setting whose *state* is derived from
another key must move that key too via `extra_updates`. DHW capacity is the
example: the write lands as the start/stop pair and `native_value` derives the
level from that pair, so updating only `tap_water_capacity_target` showed the
previous level until the next poll even though the pump already held the new
temperatures. `TAP_WATER_CAPACITY_START_STOP` in `client/constants.py` is the
inverse of `TAP_WATER_CAPACITY_MAPPINGS`; both transports and the entity use it
instead of rebuilding the inverse. Cloud writes levels 2-5 as the level setting
and only 1/6/7 as a start/stop pair, but that PATCH answers
`{"success": true}`, which is not APPLIED, so a cloud capacity write stays
poll-driven (up to 120 s) and only the Modbus pair write applies optimistically;
the same is true of every other settings-PATCH write, and
`client.models.result_applied` is why.

The pair is also the **truth about the level**, not a key to look one up with:
no holding register carries the capacity target, so Modbus derives it, and the
pump can move the pair on its own (measured on a QE-4, 2026-10-22: 55/71 → 55/65
with no user action) while the app keeps showing level 5. A pair outside
`TAP_WATER_CAPACITY_MAPPINGS` is therefore **not** rounded to a nearest level
(that reported 55/65 as 2): `_derive_tap_water_capacity` leaves
`tap_water_capacity_target` at the transport's own label (cloud) or `None`
(Modbus), `native_value` answers only from the pair and reports `unknown`, the
entity stays available (its own key may be `None` — that is the one exemption in
the number platform's availability check) with
`start_stop_pair`/`pair_mapped`/`nearest_capacity`/`reported_capacity`
attributes, and `_async_sync_tap_water_capacity_issue` keeps a
`tap_water_capacity_mismatch` repair issue (WARNING, not fixable) in step with
the mismatch — including a cloud level that disagrees with a mapped pair.
`nearest_tap_water_capacity_level` is only the issue's hint, never the state.

## Telemetry

**Opt-in and never implied.** `telemetry.py` stores only the decision and a
locally generated random token; it never sends anything. The question is a
fixable **repair issue** (`telemetry_consent`, raised once after
`PROMPT_AFTER_DAYS` of use — existing installs are covered because the clock
starts on the first start with this version) with a two-choice fix flow in
`repairs.py`. Consent must never be a condition for a feature: enabling the
adaptive curve does not imply it, and a README notice is not consent. A grant can
be revoked and the state removed; removing the entry calls `async_forget`.

The intake lives in `telemetry-worker/` (Cloudflare Worker + KV), deployed by
`.github/workflows/telemetry-worker.yml` and deliberately not by `release.yml`: a
Cloudflare outage must never block a HACS release, and the zip only takes
`custom_components/qvantum/`. The deploy skips itself when the Cloudflare secrets
or the KV namespace id are missing.

`telemetry.py` also owns the uploader: a six-hour check that posts the summary at
most once a week, only while consent is granted. Counters reset **after** the
worker accepted the row, so a failed week retries instead of losing its evidence;
the installation token travels in the request only, never in the exported file.
`qvantum.telemetry_upload` forces a send and `qvantum.telemetry_delete` erases
every row for the installation with that token. The endpoint is a constant, not
an option — an URL setting would invite sending this data elsewhere without the
prompt.

The summary export (`qvantum.export_summary`, `summary.py`) is
**whitelist-built** and must stay identifier-free, including the `counters` block
(curve reverts, trim updates, days a clamped or extrapolated knot was lived with,
and the date they were reset at — counts, never a log of when): fields are picked
explicitly and coerced to numbers, booleans or short enumerated strings, never
free text from the values payload. `tests/test_summary.py` asserts the payload
carries no entity ids or names. It writes a file and returns it — it never sends
anything and never opens an issue.

`SUMMARY_SCHEMA_VERSION` is the pool's grouping key, so it moves for **a new
field, a removed field, and a field whose meaning changed** — the third is the
one that gets forgotten. Re-normalizing an existing figure is exactly that: v6
marks `dhw_standing_loss` as a coefficient quoted at a fixed tank-to-ambient
difference (identified volume) instead of the raw daily figure at whatever tank
temperature its window saw, because two such readings pool side by side and
neither is comparable to the other. When a derived metric changes semantics,
bump the version **in the same PR** and say in the constant's comment what
changed and why; a test pins the floor so a silent revert cannot pass.

**Only a version that has shipped needs superseding.** The number exists to keep
two meanings out of one pool key, so a version that no release has carried has
no rows to protect: further schema-shaped changes on `main` before the next
release edit the current number in place and append to its comment instead of
bumping again. Check the released `manifest.json` (or the latest tag) before
deciding — 2026.10.13 is the newest stable release, it carries **v7**, so v8 is
the first number that needs a row of its own (and v8 therefore may not fold
further changes once it ships; before that, edit it in place as usual).

## Derived metrics — power, COP, DHW

- `heatingenergy` is space-heating output, but `dhwenergy` meters the DHW
  **draw** (secondary side), not the compressor's DHW output. There is no
  short-window system COP.
- The electrical counters (`compressorenergy` + `additionalenergy`) are shared
  between modes and split per poll by `_split_electrical_input`: hot water → DHW;
  space heating → compressor heating with the element DHW when its own flag is
  set (parallel top-up); compressor idle/defrosting → element DHW (the pump
  reports `Idle` when only the element runs, because `hp_status` follows the
  compressor). The **element is attributed by `additiondhwdemand`**, its own
  DHW flag: measured 2026-10-21, the counters step late (compressor every ~9 min,
  the element a handful of times a day) and in all 1 087 step intervals of a
  10-day window both `dhwdemand` and `additiondemand` had already cleared, so a
  demand-gated split booked the element's DHW energy nowhere — 3.7 kWh of 4.2 kWh
  in that window, 8 % of the DHW electrical, and it also left `charge.element_kwh`
  at zero so the volume identification could never fire. All three flags are
  disabled-by-default binary sensors and therefore sit in `REQUIRED_METRICS` — a
  disabled entity is not fetched. Energy that still cannot be attributed (an
  element step with no flag, or a compressor step that landed in an idle
  interval) is **never dropped silently**: it is returned separately and counted
  in `cop.unattributed_kwh`, because a vanishing kWh is how a COP drifts.
- `cop_heating`: rolling one-hour window of counter deltas over heating-mode
  electrical only. `cop_dhw`: **one charge segment** (`_update_dhw_charge`) —
  opens on DHW demand **or on the element's DHW flag** with the previous poll's
  tank level (an element top-up after the demand cleared is still DHW
  production, and without it the segment never carried the element energy the
  volume identification reads), publishes live, finalises on the first poll
  without either; production = draw since the charge started + tank change
  against that baseline, and a segment with no compressor work is element-only,
  capped at COP 1.0. Two review invariants: the interval
  that clears the demand is **not** booked (closing publish uses
  `charge.last_tank`), and element-only evidence checks the **compressor counter**
  delta (`interval_dhw_compressor` is zero for every status but hot water, so a
  defrost would read as element heat). A counter reset drops the window and the
  open charge; a charge older than `DHW_CHARGE_MAX_SECONDS` restarts its baseline.
- `dhw_standing_loss` is an EMA persisted in `qvantum.efficiency.<entry_id>` and
  restored before the first refresh, so a restart no longer blanks it. The **open
  free-cooling window is deliberately not persisted**: the tank went unsampled
  across the restart, and resuming would book an unobserved draw as standing
  loss. A fresh window starts on the next idle poll.
- What that EMA holds is a **coefficient, not a daily figure**: windows are
  normalized by the tank-to-ambient difference (`bt2` first, the configured room
  sensor as fallback) and the published kWh/d is the coefficient quoted at
  `REFERENCE_DELTA_K` (40 K). Without it the number tracked the tank temperature
  rather than the insulation — one window after a 69 °C extra-hot-water run
  moved a live install from 2.5 to 5.1 kWh/d. The volume is the identified one
  when present, and the store key is `dhw_standing_loss_per_k`; a snapshot
  written under the old key is ignored, never reinterpreted. The newest window's
  evidence (hours, drop, tank mean, ambient and its source, ΔT, volume) travels
  as sensor attributes.
- The ambient is only a BT2 that is a **temperature** — and BT2 is a wired
  room-air sensor whose room the installer picks (ours hangs in a hallway, not
  beside the unit), so it proxies the tank's surroundings rather than measuring
  them; an install whose tank stands colder than BT2's room gets a biased
  figure that no payload can flag. Without a probe the sensor holds the
  firmware's placeholder (a QE4 owner reported a fixed −30 °C, 2026-10-23), and
  the pump's room value then comes from the configured sensor.
  `bt2` is skipped when `BT2 Detected` (input 86) says it is not fitted, or when
  it reads at or below `BT2_PLACEHOLDER_MAX_C` (−25 °C — the cloud carries no
  bit, so the value has to say it). The bound is deliberately lower than the
  curve's room band: a tank in an unheated garage at −5 °C is a valid window,
  while averaging a placeholder in would divide by 85 K instead of ~34 K and
  publish ~40 % of the true coefficient. The window then falls through to
  `room_temp_external`/`room_temp_ext`, or is discarded when neither exists.
  The statistics picker applies the same rule: `pick_indoor_metric_key` skips a
  candidate whose series median is not a room temperature
  (`INDOOR_SERIES_MIN_C`/`INDOOR_SERIES_MAX_C`, −10…45 °C — a placeholder
  records −30 °C as an ordinary series), and skips BT2 outright when the pump
  reports no probe. That matters because the picker's fallback exists for a
  newly configured sensor with no history: without the guard it selected the
  placeholder, and a −50 K room error puts the trims on their clamp and inflates
  the heat-loss coefficient, the time constant and the solar aperture. No series
  beats a plausible wrong one.
- The DHW heat-meter comparison (`dhw_heat_meter_deviation`) is a
  **poll-resolution integral**, not a statistics product: `mean(flow) · mean(ΔT)`
  is not the integral of `flow · ΔT`, and on a live install it read 0.7 kWh
  against a 17.3 kWh meter over three days where the poll integral landed within
  2.8 %. `plant_analytics.DhwMeterWindow` accumulates it (hourly buckets, a week
  long, gap and counter-reset guarded) where the interval is known — the main
  coordinator's poll — publishes `dhw_meter_estimated_kwh` /
  `dhw_meter_metered_kwh` / `dhw_meter_draw_hours` in `values`, and is persisted
  under `dhw_meter` in the COP store. The efficiency coordinator only applies the
  gates (draw time and metered energy) and the ±20 % warning, so the figure also
  works without a recorder. Diagnostics dump it as `cop.dhw_meter`.
- The tank volume in that balance is **identified, not assumed**: with the
  compressor idle and nothing drawn, the element is the only heat source, so
  `V = Σelement_kWh / (ΣΔbt30 · c)` (gates: ≥ 1.5 kWh, ≥ 3 K, 50–500 L). An
  interval only counts when **nothing else** can take heat out of the tank
  either: `element_only_tank_balance` also requires the addition shunt closed
  (`qn8position` = 0) and **no step of the `heatingenergy` counter** in the
  interval. The counter is the signal, not the derived `heatingpower`: that one
  is computed after the COP pass and reads zero whenever the compressor is not
  heating, which is exactly the state being guarded, so it could never fire.
  Both guards were missing, and measured on a QE-6 (2026-10-21) an extra-hot-water
  run sat *idle with the element demanded* while `qn8position` had been 7 and the
  heating circuit was taking 2.3 kW — tank water was heating the house, so the element's
  energy would have been divided by a rise it did not fully cause, inflating the
  volume and, linearly, the DHW COP. A payload that carries neither metric keeps
  identifying as before (absence is not heat flow), and neither reading may enter
  the COP window's counter-reset check: they are not energy counters. The
  datasheet 175 L (QE/QG/QG-C/QH-175; the QGM wall unit QH100 is smaller and
  unpublished) is only the fallback. It is a fair fallback: `bt30` reads the top
  of a stratified store, so how much energy one kelvin represents depends on how
  the charge mixes it, and measured on a QE-6 (2026-10-21) an element-only charge
  gave 1.60 kWh over 7.7 K → **179 L**, i.e. the nameplate holds there. An earlier
  note here claimed the top reading over-counts by a wide margin (123 L); that was
  never measured and is not what the data shows. It is EMA-smoothed, persisted as
  `dhw_tank_liters` with the COP snapshot, used for the live ratio of its own
  charge, exposed on `cop_dhw`
  (`charge_open`, `tank_liters`) and in the diagnostics `cop` block — a new
  COP-store field belongs there in the same PR.
- Derived metrics (`cop_*`, `scop_total`, `aux_heat_share`, …) are computed in HA
  after the poll. Never add them to `DEFAULT_ENABLED_*` / `DEFAULT_DISABLED_*`:
  those lists are also the fetch universe, so the client would request a metric
  the API does not return. Create the entity explicitly in its platform and list
  the key in `special_sensor_keys` so registry cleanup keeps it.

## Adaptive curve

- Optional terms (COP feedback, cold-snap pre-charge) are off by default,
  persisted in the curve Store and enabled via `qvantum.set_curve_terms`; they
  apply only while the curve is active and never override the indoor cap or the
  total clamp. That cap needs a **fresh indoor reading**: `_push_indoor_margin`
  feeds it from the room error and keeps 24 h of samples, so with `sensor_mode`
  DISABLED / BT3 / AUX (or a sensor unavailable for a day) it is inert and
  nothing brakes a warm house. Diagnostics and the deviation sensor report
  `indoor_cap_armed` / `indoor_margin_c` so that state is visible — do not lean
  on the cap as a guarantee without checking it.
- `ready` compares the *learned baseline* (not the adjusted shadow) to
  `cal_heat_temp` on heating hours, with the pump's room term removed. Gates:
  median ≤ 1.5 °C and p95 ≤ 3.0 °C over ≥ 3 days and ≥ 24 heating hours —
  calibrated to what a 10° seven-point table can represent; a tighter median
  gate is unreachable.
- The room term is removed **only on hours the pump followed our table**: the
  gain is the pump's own `room_comp_factor` (never fitted over it), applied
  inside the last active segment, `active_since_ts` → `last_active_ts` (a store
  without a start stamps the current load, so older hours stay uncorrected rather
  than guessed). On Auto hours the pump already holds the term and the baseline
  was fitted to it, so correcting there double-counts — measured, it moved the
  readiness median 1.17 °C → 2.67 °C (gate failure) against 1.08 °C on active
  hours alone; the Auto-hour fit survives as the fallback for a missing factor.
  That fit is **not** how the applied gain is refreshed: the gain is the factor
  as last seen, re-read every poll by `_async_sync_room_gain`, so a user changing
  `room_comp_factor` (5 → 3) has the new gain within one cycle and needs no
  re-identification. The `ROOM_GAIN_MIN_SAMPLES`/Auto-only window gates the
  fallback and the diagnostics-only `fitted` slope, nothing else — and because
  the current gain is applied retroactively to the whole readiness window, a
  factor change legitimately moves `median_abs_c` (it is a better correction,
  not a new measurement).
  The *fitted* slope (`room_gain_from_active_hours`, fixed effects per 2 °C
  outdoor bucket) is measured on active hours only, because our written table is
  exogenous there; it is diagnostics-only, travels with its gates
  (`valid`/`notes`, hours, buckets, R², leave-one-out) and never becomes the
  applied gain while the pump reports a factor. It lags a factor change until
  the next Auto segment — the pooled `configured` vs `fitted` comparison is the
  only thing that notices, which is why it is a cross-check and not a control
  input.
- The room **value** in that removal is the pump's own input, not our configured
  sensor's: in the external mode (`sensor_mode` 4) the firmware blends the
  external value with BT2. Measured on a QE-6, 2026-10-21: `cal_heat_temp`
  follows `curve(bt1) + 5.0 · (desired − room)` with `room` the **mean** of the
  two (median |deviation| 0.10 °C, against 0.32 °C for the external sensor alone
  and 0.30 °C for BT2), the 5.0 gain confirmed by a +1.1 K target step that moved
  `cal_heat_temp` by 5.5 K, and a +2 K step on `curve_10` that moved it by 1.9 K
  (the table is followed 1:1). Removing a term built from one sensor while the
  pump built it from the mean leaves a constant `gain · (bt2 − configured) / 2`
  bias — 1.7 °C on that install, i.e. more than the readiness median gate, which
  is why the median sat at 2.17 °C with a raw 1.43 °C underneath. Hence
  `pump_room_reading` (mean blend, guarded to the
  external mode — the only regime it was measured in) feeds the readiness window
  and the live `deviation`, while the trims, the indoor cap and `room_error`
  stay on the configured sensor because those describe the house. That
  measurement was taken with a **fitted** BT2: without a probe the sensor holds
  the firmware's placeholder (a QE4 owner reported a fixed −30 °C, 2026-10-23),
  and the pump's room value then comes from the external sensor alone. Blending
  the placeholder puts `room` ~26 K off, i.e. `gain · 26 K` on the deviation
  (13 K at gain 0.5, 130 K at 5) — the readiness gate cannot pass at any gain.
  The blend is therefore gated three times: the datasheet's `BT2 Detected`
  input register 86 (0=No, 1=Yes, 2=Detecting — Modbus only; the cloud reports
  no such bit) must say 1; the value must be a room temperature
  (`BT2_MIN_PLAUSIBLE_C`/`BT2_MAX_PLAUSIBLE_C`, −10…45 °C) and within
  `BT2_MAX_DIVERGENCE_C` (10 K — |bt2 − external| on a live QE-6 was median
  0.54 °C, p95 0.77 °C, max 0.85 °C over 30 days) of the configured reading;
  and the **resulting** value is gated again, because in `DISABLED`/`BT3`/`AUX`
  modes `_indoor_live_key` falls back to `bt2`, so "configured" *is* the
  placeholder there. Degrading to the configured sensor costs the ~0.3 °C
  single-sensor bias; no reading is better than a 26 K error. `bt2_detected` is
  in `REQUIRED_MODBUS_METRICS` because the payload is filtered by enabled
  metrics and no entity exposes it, and it sits inside the 0–104 input block the
  client already reads (no extra I/O). `pump_room_c` and `pump_room_source`
  (`blended`/`configured`/`bt2`/`none`) travel in the snapshot, the deviation
  sensor and the diagnostics, next to `bt2_detected`. The DHW standing-loss
  ambient applies the same rule separately (see below).
- While the curve is active readiness turns self-referential;
  `sensor.qvantum_adaptive_curve_room_error` (median `|indoor − target|` over the
  same window) is the outcome signal instead. Readiness also only covers the
  outdoor range the baseline was observed at (`baseline_outdoor_min_c`/`max_c`);
  points outside it are extrapolated from the cached table, listed in
  `extrapolated_points`, and only the trims correct them once the weather gets
  there.
- The solar aperture is identified from the **day-block energy balance**
  (`Q_day ≈ a·ΣΔT + 24c − b·ΣGHI`, idle hours contributing `q = 0`), not from
  heating hours: the sunny hours are exactly the ones the pump's room term has
  switched off, so such a fit is censored — and at hour resolution the
  control response flips its sign. `fit_daily_solar_model` gates on day count,
  `t`, R² and a leave-one-day-out band; an ungated window means **no** term,
  never a preserved older aperture. When valid, `effective_solar_model` uses its `b`
  *and* its `a`; the hourly fit stays the fallback and owns the load term's
  demand estimate, which must subtract the same applied gain. Calibration retries
  within the hour when it cannot fetch its inputs (`CALIBRATION_RETRY_HOURS`) and
  waits `CALIBRATION_REFRESH_HOURS` after a completed attempt; the flag is
  persisted and exposed as `calibration_ok`. `qvantum.recalibrate`
  (`async_recalibrate`) runs an attempt immediately — it calls `_async_calibrate`
  directly, bypassing the throttle — and answers with the model state, because a
  sensor change or a fixed data gap makes waiting a day pointless. It writes
  nothing to the pump.
- Wind is **identified but not applied** (#427). `open_meteo` requests
  `wind_speed_10m` with `wind_speed_unit=ms` (the API default is km/h, and a
  silent unit mismatch would read as a calibration result), `HourlyWeather.wind_ms`
  is `None` for an hour without a reading (never 0.0 — "not measured" is not
  "calm"), and `SolarDay` carries `sum_wind_delta_t_k_m_s`/`sum_wind_ms_h`
  /`wind_hours`. `aggregate_days(wind_by_hour=None)` keeps its old behaviour, so
  any caller that does not pass wind is unaffected. `diagnostics.curve` reports
  `forecast_wind_ms`/`forecast_wind_available` (the site proxy) and, in
  `model_daily`, the wind column's state.
- The coefficient comes from a **fourth column** of the same day balance:
  `Q = a·ΣΔT + w·Σ(v·ΔT) + 24c − b·ΣGHI`. Wind multiplies the envelope loss, so
  the interaction form is the physical one; a plain `+ w·Σv` offset is not
  fitted. `_fit_wind_coefficient` runs **only where the three-column balance is
  already valid**, and `wind_valid` is False whenever a gate fails — the term is
  then not applied at all, exactly like an invalid aperture. Gates, in order:
  at least `DAILY_MIN_COLD_WIND_HOURS` (200) hours that are **cold and windy**
  (`ΔT ≥ WIND_EVIDENCE_MIN_DELTA_T_K` 15 K, `v ≥ WIND_EVIDENCE_MIN_MS` 4 m/s) —
  a windy mild hour is a reading, not evidence, and the probe measured a mild
  September–October window holding 156 h at ΔT ≥ 10 K but only 37 at the shipped
  threshold; `w ≥ 0`; `w ≤ a / DAILY_WIND_REFERENCE_MS` at
  `DAILY_MAX_WIND_FRACTION` (wind may at most double the loss at 5 m/s);
  `t_w ≥ 2.5`; the four-column aperture inside the three-column jackknife band
  (otherwise the column is absorbing the solar signal); **both halves of the
  window agreeing on `w`** within `DAILY_WIND_HALF_BAND` (±50 %) and both
  positive; and a wind jackknife inside its own band. The half-split is the gate
  that rejects a *drift*: the same probe found a mild 30-day window fitting
  4.50 W/K per m/s with halves of 8.66/4.44 and a 60-day window fitting
  −1.06/5.93 — opposite signs — while every older gate passed. The VIF of the
  interaction column against `{ΣΔT, ΣGHI, const}` is **reported, not gated**
  (measured 1.6–2.2 on real windows: collinearity is not what breaks it).
  With evidence the estimator is unbiased: on a synthetic winter at this
  install's noise level (≈4 kWh/day) it returns 0.11 ± 0.25 with no term and
  3.11 ± 0.25 for a true 3.0 W/K per m/s. See
  `~/.pi/agent/plans/qvantum-wind-term.md`.
- Wind is a **site proxy**: Open-Meteo returns 10 m wind on a grid cell, and
  shelter from trees, neighbours and terrain is invisible in the payload. The
  coefficient is therefore identified per installation and is biased low where
  the grid over-reports; "no term" is the expected answer for a sheltered site.
- `SolarDailyModel` reports `w_per_ms`/`w_std_err`/`w_t_stat`/`wind_hours`
  /`wind_cold_hours`/`wind_vif`/`wind_valid`/`wind_notes`, the half-split `w` and
  the four-column `a_w_per_k_4col`/`b_m2_4col`, all in
  `diagnostics.curve.model_daily`. They are **persisted with the model**
  (`_daily_to_state`/`_daily_from_state`): the calibration runs at most once a
  day, so a column left out of the store would silently switch the term off
  after every restart until the next attempt — and the dump would report zeros
  instead of "not measured". An older payload without the keys restores as "no
  wind column", which is the correct reading. `SUMMARY_SCHEMA_VERSION` is **8**: the
  four wind fields plus the applied `wind_c` are new, and a schema without them
  cannot express "this installation has no identifiable wind term".
- The **wind term** is optional and off by default: `"wind"` in the curve
  store's `terms` dict. All four terms (`solar`, `cop_feedback`, `precharge`,
  `wind`) now have a switch in the **Configure** dialog as well as in
  `qvantum.set_curve_terms`; the dialog reads the flags from the coordinator and
  writes them back through `async_set_terms`, so the options payload never holds
  a second copy. Only `solar` defaults to on: it is identified and measured
  (t ≈ 3, R² 0.92, jackknife gate) while the other three are heuristic or
  unproven, and the rule to keep is **identified/measured terms on, heuristic
  ones off**. A switch can only silence a valid model, never force an invalid
  one to act. `compute_curve(wind_by_hour=…, wind_enabled=…)` adds
  `wind_adjustment_c` — `−slope · (w · v̄ · ΔT) / a`, one-sided, `0…WIND_MAX_C`
  (2 °C), with `v̄` the mean of the *available* hours in the next
  `WIND_SMOOTH_HOURS` (3) — a missing hour is skipped, never counted as calm
  and `a` from the same day-block model — to the shared adjustment, still under
  the indoor cap and the total clamp. It needs `wind_valid`, a wind reading for
  the current hour (missing is *not* calm) and `indoor_target > outdoor`,
  otherwise 0. `CurveSnapshot.wind_c` travels to `diagnostics.curve`, the
  adjustment sensor's attributes and the summary.
- A failed **forecast** refetch keeps the previous payload while it still covers
  the current hour and is under `FORECAST_REUSE_HOURS` old (both coordinators,
  `WeatherForecast.covers`): Open-Meteo 503s sporadically well inside its quota,
  and dropping a usable payload on one error flapped `forecast_ok` and took the
  readiness window down with it. `forecast_reused` in the diagnostics
  distinguishes a served-cached cycle from a fresh fetch.
- Statistics-based curve features (trims, room gain, solar calibration) and the
  efficiency building metrics resolve the indoor sensor through
  `statistics.pick_indoor_metric_key`: the configured sensor wins once it has
  enough hourly rows, otherwise the next candidate (BT2 last). Never hard-code
  BT2 or the external sensor; degrade to "no series" rather than a wrong one.
  Both external-room metrics must stay enabled, for different reasons:
  `room_temp_ext` (HTTP) sits in `DEFAULT_ENABLED_HTTP_ONLY_METRICS`, while
  `room_temp_external` is created explicitly enabled in `sensor.py` and listed in
  `special_sensor_keys` — it is in **no** `DEFAULT_ENABLED_*` list, and adding it
  to one would create it twice under the same unique id. A disabled entity
  records no statistics, so moving either into a `DEFAULT_DISABLED_*` list
  silently sends those features back to BT2. Those lists are the source of truth
  both ways: `disable_entities_by_default` clears a stale
  `disabled_by=INTEGRATION`, but never a `disabled_by=USER`.
- Point sensors are the per-point surface: each carries `baseline`, `trim`,
  `evidence_hours`, `extrapolated`, `clamped` and `floor_pinned`, because which
  knot the pump's limits bit (or which one the baseline never observed) decides
  whether the baseline point has to move. `clamped` on `CurveResult` and the
  snapshot stays a bool for the card; `clamped_points` names the actionable ones
  and `floor_pinned_points` the warm end the floor holds down.
  `adaptive_curve_coverage` is the aggregate (state = number of extrapolated
  points, attributes = observed span, `clamped_points`, `floor_pinned_points`,
  `trim_evidence`) and is deliberately **disabled by default**: the repairs
  issue, when it exists, is the notification and this is the detail.
- Baseline floor: the frozen baseline is lifted to the pump's **minimum** heating
  supply every cycle (`lift_baseline_floor` / `_lift_baseline_to_floor`) — a knot
  under the floor is clamped on every computation, so it never shows its trim
  feedback and the clamp is structural rather than weather-driven. Such a point
  is reported in `floor_pinned_points`, **not** in `clamped_points`: there is
  nothing to act on (the floor is the answer), whereas `clamped`/`clamped_points`
  must keep meaning "adjust the baseline point" — that is what the
  `days_clamped` counter pools, and a floor pin would otherwise fire at every
  installation on every mild day. Only the pump's floor is applied; a point
  above the pump's own maximum is left alone (a real limit the user should see)
  and stays `clamped`, while the register range and the non-decreasing shape
  normalize as for any baseline. A lift clears the trims (the
  shape changed, same rule as a learned correction) and makes the two warmest
  knots **equal**, so the independently fitted trims can invert them by a tenth
  of a degree while a positive shared adjustment holds both above the floor.
  `compute_curve` therefore projects the final table onto the non-decreasing shape
  (raising the warmer knot) instead of letting the write guard revert the whole
  curve; the guard's message says "limits or shape" because it cannot tell which.
- When the pump stops following our table (the app or a cloud profile moved the
  curve type), the coordinator drops to shadow, clears the trims, logs a warning
  and opens **`curve_left_user_defined`**, deleted again as soon as we are
  active and the pump follows us. That path does **not** write Auto;
  `_async_revert(reason)` does, and it logs the reason at WARNING (INFO when the
  user switched control off), because a reversion we caused is otherwise
  indistinguishable from an external one in the log. It deliberately does **not**
  reassert the curve type: that would fight a user who chose the pump's own
  curve in the app. Detection lands on the 15-minute cycle boundary, which is
  why two events an hour apart look like a schedule.
- `diagnostics.py` is the support-dump surface and must stay in step with the
  state it mirrors: a new `CurveSnapshot` or persisted curve field (readiness
  gate, room gain and its source, trims and their evidence, room error,
  extrapolated points, clamped points, …) goes into `_curve_diagnostics` in the
  same PR. A field missing from the dump cannot be read from a user's report, and
  a missing readiness gate hides why activation is blocked.
- Removing the config entry reverts holding 22 to Auto (`async_remove_entry`),
  because nothing would be left to supervise the User-defined table we wrote.
  Reload and HA shutdown deliberately keep it: a reload re-activates from the
  curve Store, and while HA is down the pump should still heat on the last curve.
  A removal-time write must borrow a *temporary* Modbus unit
  (`_async_temporary_modbus_unit`): `_async_modbus_unit` ties its release to
  `entry.async_on_unload`, which cannot run once the entry is unloaded.

## Product constraints

- `single_config_entry`: one instance; switch cloud ↔ Modbus via reconfigure.
- Modbus writes opt-in (`CONF_MODBUS_WRITE`) — never default on.
- Interval-only option changes apply in place; host/port/unit/enablement reloads.
- Do not invent endpoints or registers. Maps: `client/modbus/maps.py`.
  HTTP paths: `client/cloud/endpoints.py`.

---

*Keep this file accurate when architecture or workflow changes.*

## Learnings
Add a bullet here only after the same mistake happens twice. One line: the correction, not the story. Remove a bullet as soon as it is fixed, obsolete, or contradicted. Do not add one-off notes.
