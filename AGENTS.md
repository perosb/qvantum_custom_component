# Qvantum Home Assistant — agent notes

HA integration for Qvantum heat pumps. One config entry, two exclusive transports:
**cloud HTTP** XOR **local Modbus TCP**. Requires HA **2026.9+**. HACS zip:
`custom_components/qvantum/`. The integration is not QE-specific: the **QA+QH**
air-source and **QG** ground-source families use the same cloud/Modbus interface,
but the *Pump hardware model (QE)* section describes the QE only.

**Where the detail lives.** This file is the working agreement: workflow, hard
invariants and the map of the code. Measurements, derivations and field lists
live in `docs/` and are read on demand, before touching that area:

| Area | Document |
|---|---|
| QE hardware model: 175 L buffer tank, through-flow DHW, BT30/BT31, draw power | `docs/qe-architecture.md` (compact: `docs/qe-architecture-quick-reference.md`) |
| Derived metrics: power, COP, DHW, tank volume, standing loss | `docs/derived-metrics.md` |
| Adaptive curve: readiness, room gain, trims, solar/wind model, write policy | `docs/heating-curve.md` |
| Tap-water capacity 1–7 and its two repair issues | `docs/tap-water-capacity.md` |
| Telemetry consent, uploader, `SUMMARY_SCHEMA_VERSION` | `docs/telemetry.md` |
| Summary export field list | `README.md#qvantumexport_summary` |

Do not paste a measurement back into this file. A number that lives in two
places goes stale in one of them: one `dhwpower` change had to correct the same
claim in two docs and here. This file states the rule; the doc states the
measurement and the numbers behind it.

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
docs/derived-metrics.md
docs/tap-water-capacity.md
docs/telemetry.md
docs/qe-architecture.md
docs/qe-architecture-quick-reference.md
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

## Pump hardware model (QE)

The QE is an **exhaust-air heat pump** with an integrated **175 L buffer tank of
heating water** and **through-flow DHW** in plate heat exchanger EP31 (< 0.8 L;
no DHW tank, no legionella store). The compressor charges the tank; a draw takes
10–20 kW from it while the compressor is 1–6 kW and recharges afterwards.

Four things every DHW metric depends on:

- `dhwenergy` (input 103/104) is the **draw meter** on the secondary side
  (BT33 → BT34 × BF1), not the compressor's DHW output.
- `dhwpower` is the **instantaneous** draw power `4.186/60 · BF1 · (BT34 − BT33)`
  in Modbus mode. Never integrate it for energy — `dhwenergy` is the energy.
- The immersion heater (EB1, 5 kW as 1+2+2) heats tank water, not the tap; its
  DHW energy is attributed by `additiondhwdemand`, and `hp_status` follows the
  compressor, so element-only DHW can read `Idle`.
- BT30 is **one point in the stratified tank**, BT31 is the DHW primary inlet at
  EP31, BT33/BT34 are cold/hot tap water, and BT14/BT15 are **air** in/out of the
  evaporator (the manual's "BT14 heating-medium return" label contradicts the
  live values).

Full reference, sensor map, formulas and the measurement behind each claim:
`docs/qe-architecture.md`. This section is QE-specific: QA+QH (air source) and QG
(ground source) share the transport and many registers, but not the tank, the
through-flow DHW or the draw-power model — do not transfer these claims to them
without checking their manuals. The integration creates entities only for metrics
present in the payload, so absent registers degrade to no entity.

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
- The datasheet is followed **until a measurement contradicts it**, and then the
  measurement wins with its numbers in the map. Input 29 (`gp2_speed`, the DHW
  pump, which on this FLVP product line pumps tank water through the through-flow
  DHW exchanger **only while hot water is drawn** — the tank is charged directly
  by the compressor) is listed at scale 1, whole percent. A QE-6 on fw 1.11.10
  read 29-85 in the register while the Qvantum app showed 5-8 % mid-draw
  (2026-10-25) and 0-480 across a week, which no percentage can, so the map
  scales it 0.1 like GP1 (input 28, whose 700 matches its 70 % setting). A
  sensor that reports >100 % is the signal that a scale row is wrong.
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

**Never push to `main`** (no force). Land only via PR and squash-merge it **once
CI is green** — the standing instruction is to merge then, without asking first,
unless a request says otherwise ("do not merge", "leave it open for review",
"stack it"). Waiting for CI is the only gate; a red or pending check is never
merged. `main` takes squash merges only and requires **every review thread
resolved**: after pushing a fix, reply in the thread and resolve it, or
`mergeStateStatus` stays `BLOCKED` with green CI. Every push re-runs the `review`
workflow, so batch documentation with the code change.

1. New feature branch from latest `main`. Never commit on `main`.
2. Conventional commit (table below).
3. `git push -u origin HEAD` — that branch only.
4. `gh pr create` against the default branch. Stay on the feature branch.
5. Keep the PR `## Summary` current as the diff evolves — it feeds the
   per-release changelog.

Do not ask permission for these steps, and merge on green (see above).

**One concern per PR.** A follow-up — even in a file an open PR touches — gets
its own branch from the latest `main`, never an append to that PR's branch.
Before pushing to a branch, check `gh pr view <n> --json state`: a merged PR's
branch is gone, so branch from `main` again.

**The owning document is part of the change.** A PR that alters architecture,
derived-metric semantics, persistence or this workflow updates the doc that owns
it (see *Where the detail lives*) in the same PR — and this file when the change
also moves a rule a review has to enforce. It is injected as the authoritative
note for the next session, so a version describing the old behaviour is worse
than no note at all.

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
A **docs-only** pull request (`docs/**`, a root `*.md`, `LICENSE`) also skips the
Python test matrix: the `changes` job diffs the PR and the `test` job runs only
when a non-prose file moved, so a typo fix does not wait two minutes for an
install and a suite. A skipped job is not a failure — the ruleset requires no
specific check.
Translations, tests, `docs/releases/**` and the workflows do not count as
behaviour, so a test-only or tooling PR passes on its own.

Passing the job is not the bar: the automated reviewer (`pi-review.yml`,
prompt in `.github/pi/review-prompt.md`) also checks that the *sentence which is
now wrong* is the one that changed. What has to move, when:

| Change | Documentation |
|---|---|
| Derived metric, gate, threshold, persisted field, `SUMMARY_SCHEMA_VERSION` meaning | the metric's section in `docs/` — `derived-metrics.md`, `heating-curve.md`, `qe-architecture.md` or `telemetry.md` — plus the one-line invariant in `AGENTS.md` if a review rule moves |
| New snapshot / diagnostics field | the documented field lists in `docs/` (`AGENTS.md` only when the field is also a new invariant) |
| New entity, option, service, user-visible behaviour | `docs/` (and `README.md` for the summary export's field list) |
| A measured constant or protocol behaviour (a probe, a verified register) | the place that documents the measurement, with its numbers |
| Pump hardware model, DHW path, draw-power semantics | `docs/qe-architecture.md` |
| Tap-water capacity level, its table or its repair issues | `docs/tap-water-capacity.md` |
| Workflow, tooling, release automation | `AGENTS.md` |

The reviewer's output contract is JSON; when the model answers in prose instead,
`post_review.py` posts that text as a fallback comment and **passes with a
`::warning::`** — failing the check there blocked two merges (#430, #434) on a
formatting slip with no findings behind it. A run that produces no text at all,
or whose fallback cannot be posted, still fails.

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

The `DEFAULT_ENABLED_*` / `DEFAULT_DISABLED_*` lists are the source of truth in
both directions: `disable_entities_by_default` clears a stale
`disabled_by=INTEGRATION`, but never a `disabled_by=USER`. A disabled entity
records no statistics, so a metric some fit reads must not be moved into a
disabled list.

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
another key must move that key too via `extra_updates` — otherwise the UI shows
the old state until the next poll. Cloud settings PATCHes answer
`{"success": true}`, which is not APPLIED (`client.models.result_applied`), so
every cloud settings write stays poll-driven (up to 120 s) while Modbus applies
optimistically.

**Tap-water capacity** is the case where this matters most: the level is derived
from the start/stop pair, the pair is the truth, and a pair outside
`TAP_WATER_CAPACITY_MAPPINGS` must not be rounded to a nearest level (the entity
reports `unknown` with its attributes, and two separate repair issues track the
two situations — none of them on a read-only Modbus link). `TAP_WATER_CAPACITY_START_STOP`
in `client/constants.py` is the shared inverse. The saga, the measured
firmware change and why the table must not be re-pointed by guessing:
`docs/tap-water-capacity.md`.

## Telemetry

**Opt-in and never implied.** `telemetry.py` stores only the decision and a
locally generated random token; it never sends anything. The question is a
fixable **repair issue** raised once after a week of use, with a two-choice fix
flow in `repairs.py`. Consent must never be a condition for a feature: enabling
the adaptive curve does not imply it, and a README notice is not consent.

The summary export is **whitelist-built and identifier-free** — fields are picked
explicitly, coerced to numbers/booleans/short enums, and the `counters` block is
counts, never a log of when. `tests/test_summary.py` asserts it carries no entity
ids or names.

`SUMMARY_SCHEMA_VERSION` moves for a new field, a removed field **and a field
whose meaning changed** — bump it in the same PR as the semantic change, and
check the released `manifest.json` first: a version no release has carried is
edited in place instead of bumped. Details, the v6/v8 history and why:
`docs/telemetry.md`.

## Derived metrics — power, COP, DHW

Computed in HA after the poll; the derivations and their measurements are in
`docs/derived-metrics.md` and `docs/qe-architecture.md` §7. The invariants a
review has to catch:

- Never add a derived key (`cop_*`, `scop_total`, `aux_heat_share`,
  `efficiency_health`, `tap_water_cap`/`tap_water_minutes`, `dhw_meter_*`,
  `heating_power_mean_w`) to `DEFAULT_ENABLED_*` /
  `DEFAULT_DISABLED_*`: those lists are also the fetch universe, so the client
  would request a metric the API does not return. Create the entity explicitly
  in its platform and list the key in `special_sensor_keys`, so registry cleanup
  keeps it.
- **The element is attributed by `additiondhwdemand`**, its own DHW flag — not by
  the demand flags, which have almost always cleared by the time a coarse counter
  steps. Energy that still cannot be attributed is counted in
  `cop.unattributed_kwh`, never dropped.
- `cop_dhw` is **one charge segment**, and two invariants break easily: the
  interval that clears the demand is not booked (closing publish uses
  `charge.last_tank`), and element-only evidence checks the **compressor**
  counter delta, or a defrost reads as element heat.
- `dhw_standing_loss` is a **coefficient quoted at 40 K** tank-to-ambient, not a
  raw daily figure; the store key is `dhw_standing_loss_per_k` and an older
  snapshot is ignored, never reinterpreted. The open free-cooling window is
  deliberately not persisted.
- The DHW heat-meter comparison is a **poll-resolution integral**; `mean(flow) ·
  mean(ΔT)` is not the integral of `flow · ΔT` and lands far below it.
- The buffer-tank volume is **identified** (element-only balance), and
  `element_only_tank_balance` must keep both guards — shunt closed
  (`qn8position` = 0) and no `heatingenergy` counter step — or the element's
  energy is divided by a rise it did not cause. The counter is the signal, not
  the derived `heatingpower`, which is zero in exactly the state being guarded.
- `dhwpower` is instantaneous and must **not** be integrated for energy;
  `heatingpower` is a counter rate that holds between steps (a ~3–4 min average —
  no heating-water flow exists to replace it, so it must not be "fixed" into a
  rolling window either).
- A new COP-store field belongs in the diagnostics `cop` block in the same PR.
- The two tap-water comfort sensors are **estimates** from a tank model
  (`tap_water_minutes`, `tap_water_cap`), never a volume, an energy or the
  capacity level.
- The indoor sensor is resolved through `statistics.pick_indoor_metric_key`:
  never hard-code BT2 or the external sensor, and degrade to "no series" rather
  than to a plausible wrong one (a placeholder series records −30 °C).

## Adaptive curve

Modbus-only. The module freezes the pump's seven-point table as a baseline and
writes it back adjusted; `docs/heating-curve.md` is the full description
(readiness, room gain, trims, solar and wind identification, write policy, card).
The rules that must not drift:

- Optional terms are **off by default** (COP feedback, pre-charge, wind); solar
  is on because it is identified and measured on the owner's history. The rule is
  *identified/measured terms on, heuristic ones off*: a switch can only silence a
  valid model, never force an invalid one to act.
- `ready` compares the **learned baseline** to `cal_heat_temp` on heating hours,
  with the pump's room term removed: median ≤ 1.5 °C, p95 ≤ 3.0 °C, ≥ 3 days and
  ≥ 24 heating hours. A tighter median gate is unreachable by a ten-degree
  seven-point table — do not "improve" it.
- The room term is removed **only on hours the pump followed our table**
  (`active_since_ts` → `last_active_ts`); on Auto hours the pump already holds it
  and the baseline was fitted to it, so correcting there double-counts. The gain
  is the pump's own `room_comp_factor`, re-read every poll — never fitted over.
  The Auto-hour fit is the fallback for a missing factor and is diagnostics-only.
- The room **value** is the pump's own input: in the external mode the firmware
  averages it with BT2 (`pump_room_reading`), and that blend is gated three times
  (`bt2_detected` = 1, a plausible room temperature, within 10 K of the configured
  reading — and the result checked again, because in the no-sensor modes the
  configured reading *is* BT2). Without the gates a placeholder BT2 puts the
  deviation tens of kelvin off. Trims, the indoor cap and `room_error` stay on
  the configured sensor: those describe the house.
- The solar aperture comes from the **day-block energy balance**, never from
  heating hours (the sunny hours are exactly the ones the pump's room term has
  switched off). An ungated window means **no** term, never a preserved older
  aperture. Wind is identified but off by default and is a **site proxy**: "no
  identifiable term" is the correct answer for a sheltered site.
- The load term is fed by a **poll-resolution rolling mean** of `heatingpower`
  with a 30-minute coverage gate — never by one sample per curve cycle, which
  aliases the compressor's cycling. Below the gate nothing is published.
- Writes are **masked and hysteretic**, never "whatever changed": only knots the
  forecast (or the measured outdoor reading) can reach, plus a 0.75 K dead band
  per knot, and the table is validated as what the pump will **end up** with.
  A missing forecast must not widen the scope to all seven knots.
- The baseline is lifted to the pump's **minimum supply** every cycle; such a
  knot is `floor_pinned` (nothing to act on), **not** `clamped` — `days_clamped`
  pools actionable points only, and a floor pin would otherwise fire at every
  installation on every mild day.
- When the pump stops following our table (the app or a cloud profile moved the
  curve type) the coordinator drops to shadow, clears the trims and opens
  `curve_left_user_defined` — and deliberately does **not** write Auto back: that
  would fight a user who chose the pump's own curve. `_async_revert(reason)` is
  the path that writes Auto, and it logs the reason at WARNING (INFO when the user
  switched control off) so a reversion we caused is distinguishable in the log.
- Removing the config entry reverts holding 22 to Auto via a **temporary** Modbus
  unit (`_async_temporary_modbus_unit`; `_async_modbus_unit` ties its release to
  `entry.async_on_unload`, which cannot run then). Reload and HA shutdown keep the
  table on purpose.
- `diagnostics.py` mirrors the curve state: a new `CurveSnapshot` or persisted
  curve field goes into `_curve_diagnostics` in the same PR. A field missing from
  the dump cannot be read from a user's report.
- Both external-room metrics must stay enabled: `room_temp_ext` sits in
  `DEFAULT_ENABLED_HTTP_ONLY_METRICS`, `room_temp_external` is created explicitly
  and listed in `special_sensor_keys` (it is in **no** `DEFAULT_ENABLED_*` list —
  adding it would create it twice). A disabled entity records no statistics, so
  moving either sends the curve features back to BT2.

## Product constraints

- `single_config_entry`: one instance; switch cloud ↔ Modbus via reconfigure.
- Modbus writes opt-in (`CONF_MODBUS_WRITE`) — never default on.
- Interval-only option changes apply in place; host/port/unit/enablement reloads.
- Do not invent endpoints or registers. Maps: `client/modbus/maps.py`.
  HTTP paths: `client/cloud/endpoints.py`.
- The 175 L tank is a buffer tank for heating water, not a DHW tank. DHW is
  through-flow in EP31; see *Pump hardware model (QE)*.
- The integration is product-agnostic across QE, QA+QH and QG; QE-specific
  hardware claims live in *Pump hardware model (QE)* and `docs/qe-architecture.md`.

---

*Keep this file accurate when architecture or workflow changes.*

## Learnings
Add a bullet here only after the same mistake happens twice. One line: the correction, not the story. Remove a bullet as soon as it is fixed, obsolete, or contradicted. Do not add one-off notes.
