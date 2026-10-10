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
  extra_dhw.py                # ExtraDhwTimer (HA Store + async_call_later)
  calculations.py             # power / tap-water / instantaneous COP
  statistics.py               # shared recorder long-term-statistics helpers
  efficiency.py               # pure COP/SCOP/aux-share math (no HA imports)
  building.py                 # pure degree-hours / heat-loss math (no HA imports)
  dhw_loss.py                 # pure DHW standing-loss math (no HA imports)
  plant_analytics.py          # pure cycling / health-grade math (no HA imports)
  efficiency_coordinator.py   # rolling SCOP / aux / building / DHW / cycling
  entity.py                   # QvantumEntity, icons, write-access mixin
  config_flow.py
  const.py                    # HA keys; re-exports client constants
  sensor.py, binary_sensor.py, climate.py, number.py, switch.py,
  button.py, select.py, fan.py, water_heater.py
  services.py / services.yaml
  client/                     # vendored comms — no Home Assistant imports
    protocol.py, exceptions.py, constants.py, models.py
    cloud/  modbus/
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
- Import maps/types from `client.modbus`.
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

**One concern per PR.** A follow-up change — even in a file an open PR already
touches — gets its own branch from the latest `main`; never append it to that
PR's branch. Before pushing to a PR branch, check `gh pr view <n> --json state`:
a merged PR's branch is gone, so branch from `main` again.

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
per layer; land on `main` in order. Do not hand-stack or rebase a layer onto
`main`.

- Create: `gh stack init <b1> <b2> …` (bottom → top) or `gh stack add` / `link`.
- Submit: `gh stack submit`.
- Sync: `gh stack sync` (atomic `--force-with-lease`). Prefer over `gh stack push`.
- Merge: `gh stack merge --yes --squash` or `gh stack merge <pr>`.

Fallback if `gh-stack` is missing: child base = parent branch, `Depends on #N`,
then `git rebase --onto origin/main <old-parent-tip> <child>` after parent squash.

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
into `docs/releases/<tag>.md` (plus the `README.md` index). They are produced on
publish — stable and pre-release, never drafts — committed with the manifest
bump, and linked from the release body by the `Release` workflow. The script
reads the release-drafter body for the PR list, so `enhancement`/`bug` labels
drive what is described; other labels are collapsed into one internal-changes
line. Only the newest pre-release keeps a file — older ones are pruned (and their
release-body link removed) once a newer release exists, because a pre-release and
the stable that follows it share the same PRs. Regenerate a single release with:

```bash
python3 .github/scripts/generate_release_changelog.py --tag <tag> --update-link
```

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

- Wrong Modbus register or cloud endpoint; holding vs input (holding 88 =
  `sg_enabled`, not a runtime counter).
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

Derived metrics (`cop_*`, `scop_total`, `aux_heat_share`, …) are computed in HA
after the poll. Never add them to `DEFAULT_ENABLED_*` / `DEFAULT_DISABLED_*`:
those lists are also the fetch universe, so the client would request a metric
the API does not return. Create derived entities explicitly in the platform and
list their keys in `special_sensor_keys` so registry cleanup keeps them.

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
- Adaptive-curve optional terms (COP feedback, cold-snap pre-charge) are off
  by default, persisted in the curve Store, and enabled via
  `qvantum.set_curve_terms`. They only apply while the curve is active and
  never override the indoor cap or the total clamp.

---

*Keep this file accurate when architecture or workflow changes.*

## Learnings
Add a bullet here only after the same mistake happens twice. One line: the correction, not the story. Remove a bullet as soon as it is fixed, obsolete, or contradicted. Do not add one-off notes.

