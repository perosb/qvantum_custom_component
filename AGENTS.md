# Qvantum Home Assistant — agent notes

HA integration for Qvantum heat pumps. One config entry, two exclusive transports:
**cloud HTTP** XOR **local Modbus TCP**. Requires HA **2026.9+**. HACS zip:
`custom_components/qvantum/`.

## Layout

```
custom_components/qvantum/
  __init__.py                 # setup/unload, RuntimeData
  coordinator.py              # poll, derived metrics, extra-DHW helper
  maintenance_coordinator.py  # firmware / elevate-access (cloud)
  curve_coordinator.py        # adaptive curve: forecast, readiness, trims (Modbus only)
  extra_dhw.py                # ExtraDhwTimer (HA Store + async_call_later)
  calculations.py             # power / tap-water / instantaneous COP
  statistics.py               # shared recorder long-term-statistics helpers
  efficiency.py               # pure COP/SCOP/aux-share math (no HA imports)
  heating_curve.py            # pure curve math: baseline, trims, room gain (no HA imports)
  solar_gain.py               # pure hourly + day-block solar fits (no HA imports)
  open_meteo.py               # forecast fetch, WeatherForecast.covers
  building.py                 # pure degree-hours / heat-loss math (no HA imports)
  dhw_loss.py                 # pure DHW standing-loss math (no HA imports)
  plant_analytics.py          # pure cycling / health-grade math (no HA imports)
  efficiency_coordinator.py   # rolling SCOP / aux / building / DHW / cycling
  entity.py                   # QvantumEntity, icons, write-access mixin
  diagnostics.py              # config-entry support dump
  summary.py                  # identifier-free summary export + download view
  config_flow.py
  const.py                    # HA keys; re-exports client constants
  sensor.py, binary_sensor.py, climate.py, number.py, switch.py,
  button.py, select.py, fan.py, water_heater.py, calendar.py
  device_trigger.py, device_condition.py, device_automation_helpers.py
  services.py / services.yaml
  client/                     # vendored comms — no Home Assistant imports
    protocol.py, exceptions.py, constants.py, models.py
    cloud/  modbus/
  www/                        # Lovelace card (qvantum-curve-card.js)
  translations/               # cs da de en es fi fr hu nl pl sv
  test_data/
tests/
docs/                       # heating-curve.md
docs/releases/<tag>.md      # generated per-release notes + README.md index
```

`config_entry.runtime_data` (`RuntimeData`): `coordinator`,
`maintenance_coordinator`, `device`, `client`, `extra_dhw` (Modbus only),
`curve_coordinator` (Modbus only) and `efficiency_coordinator` (both
transports), Modbus host/port/unit. Platforms read `runtime_data.coordinator`
and `device`.
Shared writes: `QvantumClient` on `coordinator.client`. Cloud-only / Modbus-only
writes: coordinator helpers (`async_set_extra_tap_water`, `async_write_metric`,
`async_set_smartcontrol`, `async_elevate_access`) — no `isinstance` on transport.

## Client vs Home Assistant

`client/` is a future standalone library. Enforced by `tests/test_client_package.py`.

- Never import `homeassistant*` or `custom_components*` from `client/`.
  Relative imports stay inside `client/`.
- `client/__init__.py` must not import `cloud` or `modbus` (would pull `aiohttp` /
  `modbus_connection` on every load). HA `const.py` imports `client.constants`.
- Import transports from `client.cloud` / `client.modbus`.
- Coordinators and entities use **canonical metric/setting names**. Both
  transports return HTTP-shaped payloads (`{"metrics"}`, `{"settings"}`).
  Successful writes: `{"status": "APPLIED"}` (`heatpump_status` counts too);
  check with `client.models.result_applied`.
- Cloud writable entities (`switch`, `number`, …) stay in `REQUIRED_METRICS`.
- Extra-DHW **duration** and derived calc stay in HA. Cloud encodes minutes;
  Modbus writes Extra/Normal — `ExtraDhwTimer` restores Normal. Construct the
  timer only when Modbus is enabled.
- Poll `latency` is coordinator telemetry, not a register.
- Cloud: `async_get_clientsession(hass)` (do not own a session). Modbus: HA
  2026.9 `async_get_unit`; client never opens/closes TCP.
- Prefer named setters. `write_metric` is Modbus. Cloud: `set_smartcontrol` /
  `update_settings` via coordinator helpers.
- Import maps/types from `client.modbus`. A holding register only reaches the
  coordinator when `MODBUS_HOLDING_TO_SETTINGS_MAP` gives it a canonical name:
  `get_settings` filters the payload by that map. A register in
  `MODBUS_HOLDING_REGISTER_MAP` without a settings-map entry is polled and then
  dropped, so no entity can ever exist for it (holding 74 is deliberate: the
  app has no cooling-priority setting; 73/75 were that way until they were
  named).
  `compressor_power` (input 93) must stay in the modbus metrics payload even
  though `powertotal` already folds it in: the diagnostic sensor records the
  long-term statistics `compressor_power_avg` reads, and a popped key left that
  average permanently `None`.
- Exceptions: `AuthError`, `TransportError`, `RateLimitError`. Aliases in HA/tests:
  `APIAuthError`, `APIConnectionError`, `APIRateLimitError`. Do not name a class
  `ConnectionError`.
- Modbus client: every operation is bounded by an operation timeout and maps
  failures to `TransportError`; holding writes are validated for signedness and
  the datasheet MIN/MAX (`MODBUS_HOLDING_RANGE`) in `client/modbus/model.py`
  before encoding. Never invent registers or ranges — the maps under
  `client/modbus/maps.py` are the datasheet.

## Git

**Never push to `main`** (no force). Land only via PR; squash-merge only when asked.

1. New feature branch from latest `main`. Never commit on `main`.
2. Conventional commit (table below).
3. `git push -u origin HEAD` — that branch only.
4. `gh pr create` against default branch. Stay on the feature branch.
5. Keep the PR `## Summary` current as the diff evolves — it feeds the
   per-release changelog (below).

Do not ask permission for these steps. Do not merge unless asked.

`main` takes **squash merges only** and requires **every review thread to be
resolved**: after pushing a fix, reply in the thread and resolve it, or
`mergeStateStatus` stays `BLOCKED` with green CI. Every push re-runs the `review`
workflow, so batch documentation with the code change instead of pushing doc
fixes afterwards.

**One concern per PR.** A follow-up change — even in a file an open PR already
touches — gets its own branch from the latest `main`; never append it to that
PR's branch. Before pushing to a PR branch, check `gh pr view <n> --json state`:
a merged PR's branch is gone, so branch from `main` again.

**This file is part of the change, not a follow-up.** A PR that alters
architecture, derived-metric semantics, persistence or this workflow updates
`AGENTS.md` in the same PR. The one-concern rule does not defer it: the file is
injected as the authoritative note for the next session, so a version that still
describes the old behaviour is worse than no note at all.

`/root/.gitconfig` does not survive a container restart, so both the identity and
the credential helper have to be set **per repo**:

```bash
git config --local user.name "Per Osbäck"
git config --local user.email "982752+perosb@users.noreply.github.com"
git config --local credential.helper '!gh auth git-credential'   # or push fails on username
```

**Worktrees live in `${TMPDIR:-/tmp}`.** Never create `<repo>-pr-<n>` next to the
checkout. Never switch the primary checkout to inspect a PR (another session may
own it). Exception: a fresh single-session clone (CI) — use that checkout, no
worktree.

```bash
git fetch origin <branch>
git worktree add --detach "${TMPDIR:-/tmp}/qvantum-review-<n>" origin/<branch>
# inspect / pytest there
git worktree remove "${TMPDIR:-/tmp}/qvantum-review-<n>"
```

### Stacked work

Use official `gh stack` (`gh extension install github/gh-stack`). One concern
per layer; land on `main` in order.

- Create: `gh stack init <b1> <b2> …` (bottom → top) or `gh stack add` / `link`.
- Submit: `gh stack submit`. **`gh stack merge` refuses a stack that was never
  submitted** — a local `init` alone buys nothing.
- Sync: `gh stack sync` (atomic `--force-with-lease`). Prefer over `gh stack push`.
- Merge: `gh stack merge --yes --squash` or `gh stack merge <pr>`.

`gh stack submit --auto` writes each PR body from its commit message, which
throws away the hand-written `## Summary` that feeds the release changelog —
write the bodies yourself and submit interactively, or edit them afterwards.

A child PR whose base is the parent branch (with `Depends on #N` in the body) is
not hand-stacking: it keeps the child diff clean and is the path to use when the
stack is not submitted. After the parent's squash merge, rebase once with
`git rebase --onto origin/main <old-parent-tip> <child>` (no conflicts when the
parent's content is identical), push `--force-with-lease`, then
`gh pr edit <child> --base main`. Prefer **one PR with two commits** when the
second change rewrites the first one's code — two review rounds over the same
function cost more than one.

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

Per-release notes are generated by `.github/scripts/generate_release_changelog.py`
into `docs/releases/<tag>.md` (plus the `README.md` index). They are produced for
every release event — stable and pre-release, never drafts — committed and
inserted into the release body (overview paragraph + file link) by the `Release`
workflow. The workflow listens to `published`, `released` **and** `edited`, and
takes the stability decision from the API rather than from the event payload:
this repository publishes the draft as a **pre-release** and promotes it later,
and a promotion arrives with the payload still saying `prerelease: true`. Only a
release that is stable at that moment commits `manifest.json`/`const.py`; a
pre-release commits the notes only. A stable bump also applies when there is
nothing to commit, so the zip is still rebuilt with the right version, and the
checkout is stamped with the tag right before zipping on **every** run — a
pre-release zip would otherwise advertise the previous stable version.
`update_hacs_manifest.py` re-reads both files and fails the run when the written
version is not the tag, so a release cannot ship a green run with a stale
manifest. The workflow pushes over an SSH deploy key that is a ruleset
bypass actor and never force-pushes — it builds the commit on `origin/main` and
fast-forwards.
The script
reads the release-drafter body for the PR list, so `enhancement`/`bug` labels
drive what is described; other labels are collapsed into one internal-changes
line. The release is then grouped into a handful of end-user themes: the model
returns `theme → PR numbers + prose`, the generator renders the `##` sections and
their `_Related:_` links, and the grouping is stored in a hidden comment and reused
on regeneration (`--retheme` forces a new grouping). If grouping is unavailable the
deterministic `New features`/`Bug fixes` structure is used. Themes never include the
internal-changes line, which stays as a separate footer. The overview paragraph is
rewritten for end users once via the OpenRouter API
(`~openai/gpt-luna-latest`) and then preserved on regeneration; it is also copied
into the release body. Without `OPENROUTER_API_KEY` (or on any API error) the
deterministic title-based overview is used. Only the newest pre-release keeps a file — older ones are pruned (and their
release-body link removed) once a newer release exists, because a pre-release and
the stable that follows it share the same PRs. Regenerate a single release with:

```bash
python3 .github/scripts/generate_release_changelog.py --tag <tag> --polish --themed --update-link
```

A pre-release that is promoted to a stable release is bumped by that promotion
(`released`). **Actions → Release → Run workflow** with the tag is for a run that
failed or never happened: it bumps `manifest.json`/`const.py` on `main` and
rebuilds the zip asset.

To include the overview in the release email GitHub sends on publish, run
**Actions → Release highlights → Run workflow** on the draft first: it writes the
overview (and nothing else) into the draft body, and the publish run then reuses
that text for the file and the body.

PR body:

```markdown
## Summary
- What changed. Call out cloud vs Modbus and user-visible behavior.

## Test plan
- [x] `pytest` — N passed, coverage %
- [ ] In HA, … (only if a unit test cannot prove it)
```

The `## Summary` feeds the per-release changelog: its bullets are expanded
bullet-by-bullet into `docs/releases/<tag>.md`. Write it in user-facing language
and keep it current as the PR changes, so the generated notes describe what
actually shipped. `## Test plan` is ignored by the changelog.

When the PR resolves an issue, end the body with `Closes #<n>` so GitHub links
the issue and closes it on merge.

Labels `bug` / `enhancement` / `chore` feed release-drafter. Version lives in
`manifest.json` and `const.py`; release workflow rewrites from the tag.

## Code reviews

When asked to review, **post on GitHub** (`event: COMMENT`). Chat summary is not
enough. Verify in a `/tmp` worktree — never by switching the primary checkout
(unless CI clone).

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
- Confirm the PR `## Summary` still matches the final diff — it becomes the
  per-release changelog, so a stale description ships wrong notes.

**Flag**

- Wrong Modbus register or cloud endpoint; holding vs input (input 88 =
  `compressor_run_time` — the runtime counter lives in the input space).
- Cloud-only entities in Modbus mode, or the reverse.
- `client/` importing HA, or HA `isinstance` on transport.
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

## Translations and entities

Edit all `translations/*.json` together. Validate JSON. Unique IDs and
config-entry migrations live in `__init__.py` — bump `CONFIG_VERSION` and add a
migration test when the entry schema changes.

Icons on `QvantumEntity`. Cloud-only (SmartControl, firmware boards, access
expiry, elevate-access) must not be created in Modbus mode.

Entity category: machine-internal telemetry and settings read-backs
(refrigerant/air-side BT sensors, refrigerant pressures, compressor/pump/fan
speeds, lifetime counters, valve position, derived diagnostics such as the
heat-loss or heat-meter figures) belongs under `EntityCategory.DIAGNOSTIC`.
Primary temperatures (outdoor/indoor/tank/heating flow), energy and power
counters, tap-water comfort, solar exposure and the headline efficiency
figures (COP/SCOP/aux share/health) stay in the main UI. Controls are never
diagnostics; `alarm_active` is a PROBLEM, not a diagnostic.

The summary export (`qvantum.export_summary`, `summary.py`) is **whitelist-built**
and must stay identifier-free: fields are picked explicitly and coerced to
numbers, booleans or short enumerated strings, never free text from the values
payload. A new field goes into the module's whitelists **and** bumps
`SUMMARY_SCHEMA_VERSION` (the pool groups by it); `tests/test_summary.py` asserts
the payload carries no entity ids or names. It writes a file and returns it — it
never sends anything and never opens an issue.

Derived metrics (`cop_*`, `scop_total`, `aux_heat_share`, …) are computed in HA
after the poll. Never add them to `DEFAULT_ENABLED_*` / `DEFAULT_DISABLED_*`:
those lists are also the fetch universe, so the client would request a metric
the API does not return. Create derived entities explicitly in the platform and
list their keys in `special_sensor_keys` so registry cleanup keeps them.

A curve point's own sensor is the per-point surface: it carries `baseline`,
`trim`, `evidence_hours`, `extrapolated` and `clamped`, because which knot the
pump's limits bit (or which one the baseline never observed) is what decides
whether the baseline point has to move. `clamped` on `CurveResult` and the
snapshot stays a bool for the card; `clamped_points` names them.
`adaptive_curve_coverage` is the aggregate (state = number of extrapolated
points, attributes = observed span, `clamped_points`, `trim_evidence`) and is
deliberately **disabled by default**: the repairs issue, when it exists, is the
notification and this is the detail.

The frozen baseline is lifted to the pump's **minimum** heating supply every
cycle (`lift_baseline_floor` / `_lift_baseline_to_floor`): a knot under the
floor is clamped on every computation, so it never shows its trim feedback and
the clamp is structural rather than weather-driven. Only the pump's floor is
applied — the register range and the non-decreasing shape still normalize as
for any baseline, but a point above the pump's own maximum is left alone: that
is a real limit the user should see, not something to hide by lowering the
baseline. A lift clears the trims, because the shape changed (same rule as a
learned correction).

`diagnostics.py` is the support-dump surface and must stay in step with the
state it mirrors: a new `CurveSnapshot` or persisted curve field (readiness
gate, room gain and its source, trims and their evidence, room error,
extrapolated points, which points the supply limits clamped, …) is added to
`_curve_diagnostics` in the same PR. A field missing from the dump cannot be
read from a user's report, and a missing readiness gate hides why activation is
blocked.

Entity `available` must AND its data check with `_coordinator_available` (the
coordinator's `last_update_success`); HA keeps stale data after a failed poll.
Availability is not a write guard: write methods call `_require_write_access()`
(`QvantumAccessMixin`) first, because services bypass availability
(`vacation_mode` is exempt). Readability must not depend on write access for
state-reporting entities (climate, water_heater) — they stay available and gate
their write features (`supported_features`) on write access. Control-only
entities (fan, switch, number, select, button) gate availability on write
access, so no control is offered when a write would fail.

## Product constraints

- `single_config_entry`: one instance; switch cloud ↔ Modbus via reconfigure.
- Modbus writes opt-in (`CONF_MODBUS_WRITE`) — never default on.
- Interval-only option changes apply in place; host/port/unit/enablement reloads.
- Do not invent endpoints or registers. Maps: `client/modbus/maps.py`.
  HTTP paths: `client/cloud/endpoints.py`.
- Energy counters: `heatingenergy` is space-heating output, but `dhwenergy`
  meters the DHW **draw** (secondary side), not the compressor's DHW output.
  There is no short-window system COP. The electrical counters
  (`compressorenergy` + `additionalenergy`) are shared between modes and are
  split per poll by `_split_electrical_input`: hot water → DHW; space heating
  with `dhwdemand` and no `additiondemand` → compressor heating, element DHW
  (parallel top-up); compressor idle with `dhwdemand` → element DHW (the pump
  reports `Idle` when only the element runs, because `hp_status` follows the
  compressor). Both flags are disabled-by-default binary sensors, so they sit
  in `REQUIRED_METRICS` — a disabled entity is not fetched — and without them
  the split falls back to `hp_status` alone.
- `cop_heating` is a rolling one-hour window of counter deltas over heating-mode
  electrical only. `cop_dhw` is measured over **one charge segment**
  (`_update_dhw_charge`): it opens on DHW demand (or hot water) with the tank
  level of the poll before it, publishes live, and finalises on the first poll
  without demand. Production is the draw since the charge started plus the tank
  change against that baseline; a segment holding no compressor work is
  element-only and capped at COP 1.0. Two invariants from review, keep them:
  the interval that clears the demand is **not** booked to the charge, so the
  closing publish uses `charge.last_tank` (not the current tank), and the
  element-only evidence must check the **compressor counter** delta —
  `interval_dhw_compressor` is zero for every status but hot water, so a defrost
  would otherwise be measured as element heat. A counter reset drops the window
  and the open charge; a charge older than `DHW_CHARGE_MAX_SECONDS` restarts its
  baseline.
- `dhw_standing_loss` is an EMA persisted in `qvantum.efficiency.<entry_id>`
  and restored before the first refresh, so a restart no longer blanks it. The
  **open free-cooling window is deliberately not persisted**: the tank went
  unsampled across the restart, and resuming would book an unobserved draw as
  standing loss. A fresh window starts on the next idle poll.
- The DHW tank volume in the balance is **identified, not assumed**. On
  intervals where the compressor is idle and nothing is drawn the element is the
  only heat source, so `V = Σelement_kWh / (ΣΔbt30 · c)` (gates: ≥ 1.5 kWh,
  ≥ 3 K, 50–500 L). The datasheet 175 L (QE/QG/QG-C/QH-175; the QGM wall unit
  QH100 is smaller and unpublished) is only the fallback: `bt30` reads the top
  of a stratified store that hot water is drawn from through a plate heat
  exchanger, so `bt30 × 175 L` over-counts (123 L effective on a QE-6). The
  value is EMA-smoothed, persisted as `dhw_tank_liters` with the COP snapshot,
  used for the live ratio of the charge it was measured in, exposed on the
  `cop_dhw` sensor (`charge_open`, `tank_liters`) and in the diagnostics `cop`
  block — a new COP-store field belongs there in the same PR.
- Adaptive-curve optional terms (COP feedback, cold-snap pre-charge) are off
  by default, persisted in the curve Store, and enabled via
  `qvantum.set_curve_terms`. They only apply while the curve is active and
  never override the indoor cap or the total clamp.
  That veto needs a **fresh indoor reading**: `_push_indoor_margin` feeds it from
  the room error and keeps 24 h of samples, so with `sensor_mode` DISABLED / BT3
  / AUX (or a sensor that has been unavailable for a day) the cap is inert and
  nothing brakes a warm house. The diagnostics and the deviation sensor report
  `indoor_cap_armed` / `indoor_margin_c` so that state is visible rather than
  inferred from a warm room — do not lean on the cap as a guarantee without
  checking it.
- Adaptive-curve `ready` compares the *learned baseline* (not the adjusted
  shadow) to `cal_heat_temp` on heating hours, with the pump's room term
  removed. Gates: median ≤ 1.5 °C and p95 ≤ 3.0 °C over ≥ 3 days and ≥ 24
  heating hours — calibrated to what a 10° seven-point table can represent; a
  tighter median gate is unreachable.
  The room term is removed **only on hours the pump followed our table**: the
  gain is the pump's own `room_comp_factor` (never fitted over it) and it is
  applied inside the last active segment, `active_since_ts` → `last_active_ts`
  (a store without a start stamps the current load, so older hours stay
  uncorrected rather than guessed).
  On Auto hours the pump already holds the term and the baseline was fitted to
  it, so correcting there double-counts — measured, it moved the readiness
  median 1.17 °C → 2.67 °C (gate failure) against 1.08 °C on active hours
  alone. The Auto-hour fit survives as the fallback for a missing factor.
  The *fitted* slope is also measured on active hours (fixed effects per 2 °C
  outdoor bucket, `room_gain_from_active_hours`), because there our written
  table is exogenous; it is diagnostics-only, travels with its gates
  (`valid`/`notes`, hours, buckets, R², leave-one-out) and never becomes the
  applied gain while the pump reports a factor.
  While the curve is active the pump follows our table, so readiness turns
  self-referential — `sensor.qvantum_adaptive_curve_room_error` (median
  `|indoor − target|` over the same window) is the outcome signal instead.
  Readiness also only covers the outdoor range the baseline was observed at
  (`baseline_outdoor_min_c`/`max_c`); points outside it are extrapolated from
  the cached table and listed in `extrapolated_points`, and only the trims
  correct them once the weather gets there.
- The solar aperture is identified from the **day-block energy balance**
  (`Q_day ≈ a·ΣΔT + 24c − b·ΣGHI`, idle hours contributing `q = 0`), not from
  heating hours: the sunny hours are exactly the ones the pump's room term has
  already switched off, so a heating-hour fit is censored — and at hour
  resolution the control response flips its sign. `fit_daily_solar_model`
  gates on day count, `t`, R² and a leave-one-day-out band, and an ungated
  window means **no** term (never a preserved older aperture). When it is
  valid, `effective_solar_model` makes the term use its `b` *and* its `a`
  together; the hourly fit stays the fallback and still owns the load term's
  demand estimate, which must subtract the same applied gain. Solar
  calibration retries within the hour when it cannot fetch its inputs
  (`CALIBRATION_RETRY_HOURS`) and waits `CALIBRATION_REFRESH_HOURS` after a
  completed attempt; the flag is persisted and exposed as `calibration_ok`.
- A failed **forecast** refetch keeps the previous payload while it still
  covers the current hour and is under `FORECAST_REUSE_HOURS` old (both
  coordinators, `WeatherForecast.covers`); Open-Meteo 503s sporadically well
  inside its quota, and dropping a usable payload on one error flapped
  `forecast_ok` and took the readiness window down with it. `forecast_reused`
  in the diagnostics distinguishes a served-cached cycle from a fresh fetch.
- Statistics-based curve features (trims, room gain, solar calibration) and
  the efficiency building metrics resolve the indoor sensor through
  `statistics.pick_indoor_metric_key`: the configured sensor wins once it has
  enough hourly rows, otherwise the next candidate (BT2 last) keeps them
  alive. Never hard-code BT2 or the external sensor; always degrade to
  "no series" rather than a wrong one. Both external-room metrics must stay
  enabled, for different reasons: `room_temp_ext` (HTTP) sits in
  `DEFAULT_ENABLED_HTTP_ONLY_METRICS`, while the `room_temp_external` holding
  sensor is created explicitly enabled in `sensor.py` and listed in
  `special_sensor_keys` — it is deliberately in **no** `DEFAULT_ENABLED_*` list,
  and adding it to one would make `sensor.py` create it a second time under the
  same unique id.
  A disabled entity records no statistics, so moving either into a
  `DEFAULT_DISABLED_*` list silently sends those features back to BT2. Those
  lists are the source of truth in both directions: `disable_entities_by_default`
  clears a stale `disabled_by=INTEGRATION` when a metric moves out again, but
  never touches a `disabled_by=USER`.
- Removing the config entry reverts holding 22 to Auto (`async_remove_entry`),
  because nothing would be left to supervise the User-defined table we wrote.
  Reload and HA shutdown deliberately keep it: a reload re-activates from the
  curve Store, and while HA is down the pump should still heat on the last
  curve. A removal-time write must borrow a *temporary* Modbus unit
  (`_async_temporary_modbus_unit`): `_async_modbus_unit` ties its release to
  `entry.async_on_unload`, which cannot run once the entry is unloaded.

---

*Keep this file accurate when architecture or workflow changes.*

## Learnings
Add a bullet here only after the same mistake happens twice. One line: the correction, not the story. Remove a bullet as soon as it is fixed, obsolete, or contradicted. Do not add one-off notes.

