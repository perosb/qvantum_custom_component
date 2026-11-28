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
| Tap-water capacity 1–7 and its repair issue | `docs/tap-water-capacity.md` |
| Per-compressor-unit registers on QG (EP10/EP11), why the legacy values are means | `docs/compressor-units.md` |
| Alarm codes and their friendly names (source, English-only policy) | `docs/alarm-codes.md` |
| Telemetry consent, uploader, `SUMMARY_SCHEMA_VERSION` | `docs/telemetry.md` |
| Summary export field list | `docs/telemetry.md` |

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
  curve_coordinator.py        # adaptive curve: forecast, readiness, trims
  efficiency_coordinator.py   # rolling SCOP / aux / building / DHW / cycling
  extra_dhw.py                # ExtraDhwTimer (HA Store + async_call_later)
  calculations.py             # power / tap-water / instantaneous COP
  statistics.py               # shared recorder long-term-statistics helpers
  entity.py                   # QvantumEntity, icons, write-access mixin
  diagnostics.py              # config-entry support dump
  summary.py                  # identifier-free summary export + download view
  telemetry.py                # opt-in consent, token, uploader
  repairs.py                  # fix flows: telemetry consent, curve-ready invitation
  config_flow.py
  const.py                    # HA keys; re-exports client constants
  sensor.py, binary_sensor.py, climate.py, number.py, switch.py,
  button.py, select.py, fan.py, water_heater.py, calendar.py
  device_trigger.py, device_condition.py, device_automation_helpers.py
  services.py / services.yaml
  # pure math, no HA imports:
  numeric.py, efficiency.py, heating_curve.py, solar_gain.py, building.py,
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
`curve_coordinator` and `efficiency_coordinator` (both
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
  DHW energy is attributed by `additiondhwdemand`, and element-only DHW can read
  `Idle` because the coordinator rewrites a `hp_status` of `0` from
  `compressor_state`. That rewrite means the `hp_status` **entity is derived, not
  the register** — the pump reports input 41 as `0` while it heats. Never cite
  the entity state as a register reading, and never treat `hp_status = 1` as a
  defrost signal: the input-67 flag is the only one
  (`docs/qe-architecture.md` §3.5).
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
  (holding 74 is deliberate: the app has no cooling-priority setting; holding 58
  is `dhw_start_extra`, exposed as a number entity alongside `dhw_stop_extra`).
  `compressor_power` (input 93) must stay in the modbus metrics payload even
  though `powertotal` folds it in: its diagnostic sensor records the long-term
  statistics `compressor_power_avg` reads, and a popped key left that average
  permanently `None`. Since firmware 1.12 `powertotal` is the pump's **own**
  reading (input 92) when that address answers above 0, and the estimate
  (base + relay stages + `compressor_power`) only when it reads 0 — which is
  what 1.11 answers, and also what a unit that is off reports.
- Modbus client: every operation is bounded by an operation timeout and maps
  failures to `TransportError`; holding writes are validated for signedness and
  the datasheet MIN/MAX (`MODBUS_HOLDING_RANGE`, `client/modbus/model.py`)
  before encoding. Never invent registers or ranges — `client/modbus/maps.py` is
  the datasheet. A register-map tuple is `(address, data_type, scale)`; the
  `"string"` data type carries the **register count** in the scale slot
  (`price_region`: `(173, "string", 2)` for the char[4] bidding zone).
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
- Platform code asks `is_cloud(coordinator)` / `is_full_payload(coordinator)`
(`coordinator.py`) instead of reading `modbus_enabled` as "not cloud". The two
questions are different — the cloud returns a curated payload and has no
canonical settings setter, the other transports have both — and the flag answers
neither once a third transport exists. A question that genuinely *is* Modbus keeps the flag: a holding-register write
(`select`, `number`), an input-register source (display firmware), the link
report in the diagnostics. A question about the payload, a cloud-only control or
"can this transport write" does not.
`resolve_transport()` is a **module function** for the same reason
`transport_kind()` is one: the setup tests patch `QvantumDataUpdateCoordinator`
wholesale, so a static call on the patched class answers with a mock and every
entry takes the cloud branch. `__init__.py` derives `modbus_enabled` from it, so
the resolver is the single source of truth.
`resolve_transport()` honours only a transport
whose client the setup can build — cloud or Modbus today — and falls back to
`CONF_MODBUS_TCP` otherwise, which covers every entry created before
`CONF_TRANSPORT` existed and refuses one claiming a transport with no client
(the platforms would otherwise create the wrong entity set for the client the
setup actually built).

Device-registry lookups are **scoped to the owning config entry**:
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

Title: `type(scope): Imperative subject`. Body = *why* when the subject is not
enough.

| Prefix | Use |
|---|---|
| `feat:` | New / user-visible behavior |
| `fix:` | Bug |
| `refactor:` | Structure, no intended behavior change |
| `test:` | Tests only |
| `docs:` | Docs only |
| `chore:` | Tooling, CI, deps |

**Add a scope when the change is confined to one area** — it is what makes a
log readable a year later, and the squash merge takes the PR title, so the same
rule applies there. Omit it when the change genuinely spans the integration.

| Scope | Area |
|---|---|
| `modbus` | `client/modbus/`, register maps, the Modbus transport |
| `cloud` | `client/cloud/`, endpoints, the HTTP transport |
| `curve` | the adaptive curve: `heating_curve`, `curve_coordinator`, `solar_gain` |
| `efficiency` | `efficiency_coordinator`, `building`, `dhw_loss`, the COP/SCOP figures |
| `card` | `www/qvantum-curve-card.js` |
| `telemetry` | consent, uploader, `summary.py`, `SUMMARY_SCHEMA_VERSION` |
| `ci` | workflows, release automation, scripts under `.github/` |

`feat(modbus): read the unit's own total power from input 92` is the shape:
the scope names the area, the subject says what changed, and the body carries
the measurement that justifies it.

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

A **fork PR is not reviewed automatically**: `pull_request` runs from a fork get
no repository secrets, so the job is skipped. Review one on demand with
**Actions → pi-review → Run workflow** and the PR number. That run checks out the
default branch for the reviewer's own code (prompt, extensions, `post_review.py`)
and the PR head into `pr-head/` as **data only** — `repo_read`/`repo_grep` are
rooted there, and nothing in it is executed — so a PR that edits `.github/pi/`
cannot rewrite the thing reviewing it. `pi` is pinned at **1.6.0** and the model
ids are explicit (`z-ai/glm-5.3`, `z-ai/glm-5.3-flash`): the `~…-latest` aliases
resolve to a dated snapshot that rejects 1.6's mid-conversation reasoning update.

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
- **Labels come from the PR title via `.github/workflows/pr-labels.yml`, which
  must stay on `pull_request_target`** — a fork's `pull_request` run carries a
  read-only token for issues/PRs (measured on #576, 2026-11-26: `addLabels` 403,
  so the label had to be applied by hand), and since `release-drafter.yml`
  filters on `include-labels`, an unlabelled PR drops out of the notes entirely.
  The job reads the title from the event payload and labels; it **checks nothing
  out, runs no file from the pull request and holds no secret**. Adding
  `actions/checkout` or a secret there is what turns the trigger into
  fork-controlled code execution.

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
- **A change that only holds for the author's pump**: a threshold taken from one
  machine's measurements, a constant valid at one fan speed or tank size, a
  sensor's meaning assumed to be the same across QE/QA+QH/QG, or a term that
  fires without evidence that travels. Name the installation the number came
  from and what makes it general.

## Tests

`ruff` is a gate, not a suggestion: `ruff.toml` at the root selects a deliberately
narrow set (`E4/E7/E9`, `F`, `I`, `UP`, `B`, `RUF100`) and the `lint` job runs
`ruff check` on the whole repository, `tests/` and `.github/scripts/` included. It
is pinned in the workflow (`ruff==0.17.6`) because a new release can add a rule
that turns the gate red on its own. `D` (docstrings), `ANN` (annotations) and
`E501` are deliberately out — their backlog is real but fixing it is a separate
decision, and `E501` would flag forty lines, most of them log format strings.
`isort` keeps aliased re-exports combined and wrapped
(`combine-as-imports`/`force-wrap-aliases`), so `from .const import (A as A,
B as B)` stays one statement — without it every alias gets its own block.
Run it locally with `uv run --with ruff==0.17.6 ruff check --fix`.

Type checking is a gate **only for the seven Home-Assistant-free modules** —
`numeric`, `efficiency`, `building`, `dhw_loss`, `plant_analytics`,
`heating_curve`, `solar_gain` — listed in `mypy.ini` and run by the `types` job
(`mypy==2.4.1`). They carry the curve's and the solar term's arithmetic, where a
type error becomes a wrong number in the pump rather than a missing field in a
report. The HA-facing modules are deliberately out: their
`getattr(coordinator, name, None)` reads are defensive on purpose (the test
harness builds half a coordinator), `getattr` with a literal name is invisible to
a checker anyway, and the class of bug those two modules actually suffered —
an export field missing from the dump — is pinned by a parity test in
`tests/test_diagnostics.py`. Do not "finish" the job by adding the whole
integration: that needs the persisted coordinator state declared first.

```bash
./run-tests.sh          # venv + requirements-test.txt + pytest
python -m pytest        # repo root; pytest.ini sets coverage + timeout
```

- Coverage floor **92%** (`--cov-fail-under` in `pytest.ini`, so the suite
  itself fails below it). Timeout **30s** per test.
- The **per-pull-request** figure is measured in CI with `diff-cover` against
  the PR's base commit (`--fail-under 80` on the changed lines) and written to
  the run summary, with the project coverage report beneath it in a fenced
  block. `.github/scripts/coverage_summary.py` renders both; diff-cover's own
  text report stays in the job log. The external Codecov app is gone on purpose (its checks hung in
  `in_progress` for whole PRs) — do not re-add an upload.
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
`disabled_by=INTEGRATION`, but never a `disabled_by=USER`. They are also the
**fetch universe**: `build_metrics_payload` drops every key the coordinator did
not ask for, so a key that is enabled by default must be in them or it is never
polled — and the binary-sensor platform skips an enabled-by-default key that is
absent from the payload, which closes the loop silently (not fetched → no
entity → not in the registry → not fetched). `tests/test_modbus_device.py` pins
the invariant for every pollable binary sensor and carries the measurement
behind it. A disabled entity
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

Temperature entities must publish the **`native_*` surface** where the installed
Home Assistant has it. HA 2026.11 deprecates `current_temperature`,
`target_temperature` and `_attr_temperature_unit` in favour of
`native_current_temperature`, `native_target_temperature` and
`_attr_native_temperature_unit` (removed in 2027.11), and logs a warning per
entity that still defines the old ones. `climate.py` and `water_heater.py` probe
the base class once (`NATIVE_TEMPERATURE_API = hasattr(..., "native_current_temperature")`)
and alias the old names to the native properties **only** on releases that lack
them, so one code path serves 2026.9–2026.10 and warns on nothing later. Do not
set both attribute names — the deprecated one is what logs.

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
the old state until the next poll. Every transport confirms: Modbus with
`{"status": "APPLIED"}`, a cloud settings PATCH with `{"status": "APPLIED",
"res": {...}}` and a cloud command with `{"heatpump_status": "APPLIED"}`.
`client.models.result_applied` is the one check, so a cloud write
applies optimistically like a Modbus one; a response that carries no state is
not applied and the caller waits for the next poll.

**Tap-water capacity** is the case where this matters most: the level is derived
from the start/stop pair, the pair is the truth, and a pair outside
`TAP_WATER_CAPACITY_MAPPINGS` must not be rounded to a nearest level (the entity
reports `unknown` with its attributes, and one repair issue tracks a pair that is
not in the table at all — none of them on a read-only Modbus link). `TAP_WATER_CAPACITY_START_STOP`
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

`tests/data/pool_rows.json` holds one row per pool generation and
`tests/test_pool_rows.py` compares the current one against the live export **block
by block**, so a bump without a fixture row fails CI. Older rows keep their
pseudonyms stripped (`install_token`, `key`, `generated_on`); the current one is
built from the suite itself, so it carries no user data. A block that did not
exist yet is `null` there, and the parser treats missing as missing, not as
malformed — the same rule the pool script follows.
`SUMMARY_SCHEMA_VERSION` moves for a new field, a removed field **and a field
whose meaning changed** — in the same PR as the change. Whether it moves *at all*
depends on what has **shipped**, and that is a two-step check, not a guess:

```bash
git tag --sort=-creatordate | head -3        # which tags exist
git show <tag>:custom_components/qvantum/summary.py | grep SUMMARY_SCHEMA_VERSION
```

- A value **no release has carried** is edited **in place**: the constant stays
  and the new fields join that version. Bumping there manufactures a generation
  no user ever uploaded.
- A value a **release** has carried is **bumped**. A *pre-release counts* — its
  users upload rows the moment it is installed.
- **CI enforces the current version, and only that**: `tests/test_pool_rows.py`
  fails unless `tests/data/pool_rows.json` has a row for
  `SUMMARY_SCHEMA_VERSION`, and compares that row block by block against the live
  export. Regenerating it is part of the same PR as the schema change.
- **Add a row for the version a release carries, when you bump.** The file holds
  one row per *interesting* generation (8, 13, 14, 16, 19), not every one that
  ever shipped — 5, 7, 9, 10, 11 and the v18 pre-release have none, and backfilling
  them would mean writing shapes nobody uploaded. That is a deliberate gap, not a
  to-do.
- Check the tag, not `manifest.json`: a pre-release does not bump the manifest, so
  it says the last *stable* version while a newer pre-release is in users' hands.

Details, the v6/v8 history and why: `docs/telemetry.md`.

## Derived metrics — power, COP, DHW

Computed in HA after the poll; the derivations and their measurements are in
`docs/derived-metrics.md` and `docs/qe-architecture.md` §7. The invariants a
review has to catch:

- Never add a derived key (`cop_*`, `scop_total`, `aux_heat_share`,
  `efficiency_health`, `tap_water_cap`/`tap_water_minutes`, `dhw_meter_*`,
  `dhw_charge_*`, `heating_power_mean_w`) to `DEFAULT_ENABLED_*` /
  `DEFAULT_DISABLED_*`: those lists are also the fetch universe, so the client
  would request a metric the API does not return. Create the entity explicitly
  in its platform and list the key in `special_sensor_keys`, so registry cleanup
  keeps it.
- **A counter's meaning is its doc section, not what it did in the window you
  looked at.** `dhwenergy` is a *draw* meter (BT33 → BT34 × BF1) and reads 0
  while the tank charges — that is the design (`docs/qe-architecture.md` §11),
  not a missing counter. The charge work is attributed from
  `compressor_energy` by `_split_electrical_input` and published as
  `dhw_charge_electrical_energy` / `dhw_charge_element_energy`; read
  `docs/derived-metrics.md` before explaining a figure that looks wrong.
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
  mean(ΔT)` is not the integral of `flow · ΔT` and lands far below it. Its
  **warning compares the window's two halves, not the level**: the level carries
  a structural bias (measured −23 % for three weeks with short draws), so a
  level threshold is permanently on — movement ≥ 5 points, or a level beyond
  ±40 %, is what fires.
- The buffer-tank volume is **identified** (element-only balance), and
  `element_only_tank_balance` must keep both guards — shunt closed
  (`qn8position` = 0) and no `heatingenergy` counter step — or the element's
  energy is divided by a rise it did not cause. The counter is the signal, not
  the derived `heatingpower`, which is zero in exactly the state being guarded.
- `dhwpower` is instantaneous and must **not** be integrated for energy;
  `heatingpower` is a counter rate that holds between steps (a ~3–4 min average —
  no heating-water flow exists to replace it, so it must not be "fixed" into a
  rolling window either). Three invariants keep that hold honest: it is derived
  on **both** transports (the envelope fits read its hourly means and are
  transport-agnostic, so the entity is created explicitly rather than through a
  fetch list), it is only held while the **compressor is turning** (`hp_status`
  is the pump's mode, not a running signal), and a step that implies a
  physically impossible rate is **rejected** rather than published — the counter
  can flush several 0.1 kWh increments in consecutive polls.
- The SCOP window measures **every counter over the same interval**. The
  recorder keeps a separate history per entity, so a counter added or enabled
  later starts inside the window; giving each its own span divides a heating
  delta over N days by an electrical delta over M days.
- A new COP-store field belongs in the diagnostics `cop` block in the same PR.
- The two tap-water comfort sensors are **estimates** from a tank model
  (`tap_water_minutes`, `tap_water_cap`), never a volume, an energy or the
  capacity level.
- The indoor sensor is resolved through `statistics.pick_indoor_metric_key`:
  never hard-code BT2 or the external sensor, and degrade to "no series" rather
  than to a plausible wrong one (a placeholder series records −30 °C).

## Adaptive curve

Runs on every transport that can write settings — Modbus and the cloud — and
`QvantumCurveCoordinator.writable` (`coordinator.write_access`) is the gate: the
Modbus write option, or an **elevated** cloud account
(`writeAccessLevel >= 20`, what `elevate_access` grants; a plain account gets
403). The cloud's curated `/settings` list carries neither the pump's supply
limits nor holding 23, so `temp_compensation_curve` stays empty there. The
limits **are** readable: the app reads `min_supply`/`max_supply` from the
internal `/values` endpoint, so the cloud client requests them with the metrics
and aliases them to `min_heating_supply`/`max_heating_supply`
(`CLOUD_EXTRA_VALUE_NAMES`). Without that alias `_supply_bounds` fell back to the
datasheet range, so the table was validated against a span the pump does not
accept; the measurement is in `docs/heating-curve.md`.
The module freezes the pump's seven-point table as a baseline and writes it
back adjusted; `docs/heating-curve.md` is the full description
(readiness, room gain, trims, solar and wind identification, write policy, card).
The rules that must not drift:

- Optional terms are **off by default** (COP feedback, pre-charge, wind); solar
  and the room-error integral are on because their inputs are measured. The rule
  is *identified/measured terms on, heuristic ones off*: a switch can only
  silence a valid model, never force an invalid one to act.
- The **room-error integral** is the only term that reads the outcome, and its
  invariants are load-bearing: one-sided **output** (never raises — a cold house
  is the cap's floor and the trims' job) with a **magnitude that unwinds** when
  the mean error falls below the dead band, because an integrator that can only
  grow leaves the house cold once its correction is no longer needed;
  dead-banded on the cap's 24 h mean, rate- and bound-limited to the trims' own
  authority, added **before** `TOTAL_MAX_C` and
  the indoor cap so the floor can lift it and the ceiling cannot veto it, reset
  (never seeded) on reversion, on the pump leaving User defined and on a setpoint
  change, and silent in shadow. Its catch-up after a restore is capped, because
  an outage is not evidence.
- `ready` compares the **learned baseline** to `cal_heat_temp` on heating hours,
  with the pump's room term removed: median ≤ 1.5 °C, p95 ≤ 3.0 °C, ≥ 3 days and
  ≥ 24 heating hours. A tighter median gate is unreachable by a ten-degree
  seven-point table — do not "improve" it.
- The active-hour **measurement is dated** (`measured_at`, persisted with the
  estimate and dumped with `age_hours`) and **never spans a change in
  `room_comp_factor`**: a changed factor drops the estimate and re-stamps
  `room_gain_factor_since_ts`. Detection reads the **raw** register, not the
  ranged value the gain is derived from, and a *missing* reading is not a change
  — a register that flaps once must not empty the window. A stale measurement
  must never be read as "the pump ignored the write" (`docs/heating-curve.md`
  carries the incident and its numbers).
- The room term is removed **only on hours the pump followed our table**
  (`active_since_ts` → `last_active_ts`); on Auto hours the pump already holds it
  and the baseline was fitted to it, so correcting there double-counts. The gain
  is the pump's own `room_comp_factor`, re-read every poll — never fitted over.
  The Auto-hour fit is the fallback for a missing factor and is diagnostics-only.
- The room **value** the pump's term uses is the **configured** reading, with no
  mean over anything — measured with room and target steps on 1.12.8, see
  `docs/heating-curve.md` for the numbers; the older blend with BT2 came from a
  different method on 1.11.x and is history, not a rule — do not re-add it.
  BT2 stands in only when the configured reading is missing, gated on
  `bt2_detected` = 1 and a plausible room value (a placeholder puts the deviation
  tens of kelvin off). Trims, the indoor cap and `room_error` stay on the
  configured sensor: those describe the house. The external feed's **source**
  must be an entity this integration does not provide — the picker excludes them
  and the feed refuses them (`external_room_feed.self_reference`) — because
  `qvantum_extern_rumstemperatur` mirrors holding 14 and a self-reference holds
  the register at its last value.
- `capped_by_indoor` means the adjustment **moved**, not that it touched the
  band: the edge follows the 24 h mean and the total is a float sum, so a clamp
  that changes 7e-16 must not report itself binding — it also pauses the trim
  loop.
- The indoor cap averages the room error over **24 h**, so two things bound it:
  a **setpoint change clears the window** (averaging across it judges the house
  by a target it no longer has) and a **cold house follows the current
  reading** rather than the mean, because the protective floor may not wait a
  day. The dump carries `indoor_margin_now_c` / `indoor_margin_mean_c` beside
  `indoor_margin_c`, so a lagging cap is distinguishable from a biased one.
- The solar aperture comes from the **day-block energy balance**, never from
  heating hours (the sunny hours are exactly the ones the pump's room term has
  switched off). An ungated window means **no** term, never a preserved older
  aperture. Wind is identified but off by default and is a **site proxy**: "no
  identifiable term" is the correct answer for a sheltered site.
- The load term is fed by a **poll-resolution rolling mean** of `heatingpower`
  with a 30-minute coverage gate — never by one sample per curve cycle, which
  aliases the compressor's cycling. Below the gate nothing is published. It runs
  on **both** transports: a mean over the window is the energy over the window,
  so the cloud's 120 s counter staircase does not alias it.
- Writes are **masked and hysteretic**, never "whatever changed": only knots the
  forecast (or the measured outdoor reading) can reach, plus a 0.75 K dead band
  per knot, and the table is validated as what the pump will **end up** with.
  A missing forecast must not widen the scope to all seven knots.
- The baseline is lifted to the pump's **minimum supply** every cycle; such a
  knot is `floor_pinned` (nothing to act on), **not** `clamped` — `days_clamped`
  pools actionable points only, and a floor pin would otherwise fire at every
  installation on every mild day.
- The integration **never activates the curve itself** — it writes to the pump,
  so that decision is the user's. When `ready` has held for a day in shadow it
  opens the fixable `curve_ready` issue with the gate's median/p95/heating hours;
  a decline is remembered (`ready_issue_declined`, persisted) until readiness
  lapses, and the issue is only raised when the curve's transport can write
  (`curve.writable`), because without it the switch is unavailable. The text says whose feature it is: the
  integration's, not Qvantum's.
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
  curve field goes into `_curve_diagnostics` in the same PR, and the efficiency
  snapshot has its own `efficiency` block for the same reason. A field missing from
  the dump cannot be read from a user's report.
- Both external-room metrics must stay enabled: `room_temp_ext` sits in
  `DEFAULT_ENABLED_HTTP_ONLY_METRICS`, `room_temp_external` is created explicitly
  and listed in `special_sensor_keys` (it is in **no** `DEFAULT_ENABLED_*` list —
  adding it would create it twice). A disabled entity records no statistics, so
  moving either sends the curve features back to BT2.

## Product constraints

**It is not one installation.** This integration has many users on QE, QA+QH and
QG, on cloud and Modbus, in eleven languages, with their own curves, tank sizes,
ventilation rates and house losses. Anything tuned to the owner's pump is a bug
for everyone else: a threshold derived from one machine's numbers, a constant
that only holds at one fan speed, a term that assumes one family's sensor
semantics, or a fix that works because *this* house has a certain floor area.
State the physical bound rather than the observed value, gate on evidence that
travels (a datasheet range, a ratio that cannot occur, a register that is present
or absent) and let a transport or product that lacks the input keep its previous
behaviour — absence is not evidence. A review asks "what does this do on a pump
this author has never seen?" before it asks "does it work here?"

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
