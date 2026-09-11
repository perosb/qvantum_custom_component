# Qvantum Home Assistant custom component — agent notes

Home Assistant integration for Qvantum heat pumps. One config entry, two exclusive
transports: **cloud HTTP** or **local Modbus TCP**. Requires Home Assistant **2026.9+**
(shared Modbus connection). HACS zip is `custom_components/qvantum/`.

## Layout

```
custom_components/qvantum/
  __init__.py                 # setup/unload, RuntimeData, Cloud XOR Modbus
  coordinator.py              # poll, derived metrics, extra-DHW helper
  maintenance_coordinator.py  # firmware / elevate-access (cloud)
  extra_dhw.py                # ExtraDhwTimer (HA Store + async_call_later)
  calculations.py             # derived metrics (heatingpower, tap capacity, …)
  entity.py                   # QvantumEntity, icons, write-access mixin
  config_flow.py              # cloud login or Modbus host/port/unit probe
  const.py                    # HA config keys, enabled-metric lists; re-exports client constants
  sensor.py, binary_sensor.py, climate.py, number.py, switch.py, button.py, select.py, fan.py
  services.py / services.yaml # extra_hot_water
  client/                     # vendored comms library — no Home Assistant imports
    protocol.py               # QvantumClient Protocol
    exceptions.py             # AuthError, TransportError, RateLimitError (+ API* aliases)
    constants.py              # DHW modes, fan presets, capacity map, relay watts
    models.py
    cloud/                    # QvantumCloudClient (Firebase + REST)
    modbus/                   # QvantumModbusClient, device, maps, model
  translations/               # cs, da, de, en, es, fi, fr, hu, nl, pl, sv
  test_data/                  # recorded HTTP fixtures
tests/                        # unit tests (pytest)
```

`config_entry.runtime_data` is a `RuntimeData` dataclass: `coordinator`,
`maintenance_coordinator`, `device`, `client`, `extra_dhw` (Modbus only), and
Modbus host/port/unit. Platforms read `config_entry.runtime_data.coordinator`
and `device`. Shared writes go through `QvantumClient` on `coordinator.client`.
Cloud-only and Modbus-only writes go through coordinator helpers
(`async_set_extra_tap_water`, `async_write_metric`, `async_set_smartcontrol`,
`async_elevate_access`) so platforms stay protocol-blind.

## Client vs Home Assistant

`client/` is the seed of a future standalone library. Isolation is enforced by
`tests/test_client_package.py`.

- **Never** import `homeassistant*` or `custom_components*` from `client/`.
  Relative imports must stay inside `client/`.
- `client/__init__.py` must not import `cloud` or `modbus`. HA `const.py` imports
  `client.constants`; an eager cloud/Modbus import would pull `aiohttp` /
  `modbus_connection` into every load path.
- Import transports from `client.cloud` / `client.modbus` (or
  `client.cloud.client` / `client.modbus.client`).
- Coordinators and entities keep **canonical metric/setting names**. Both
  transports return HTTP-shaped payloads (`{"metrics": …}`, `{"settings": …}`).
  Successful writes return `{"status": "APPLIED"}` (`SETTING_UPDATE_APPLIED`).
- Extra-DHW **duration** and derived calculations stay in HA. Cloud encodes
  minutes on the wire; Modbus only writes Extra/Normal — `ExtraDhwTimer` restores
  Normal. Construct the timer only when Modbus is enabled.
- Poll `latency` is coordinator telemetry, not a protocol register.
- Cloud uses `async_get_clientsession(hass)` (do not own a session in HA).
  Modbus borrows HA 2026.9 `async_get_unit`; the client never opens or closes TCP.
- Prefer named setters (`set_indoor_temperature_target`, `update_setting`, …).
  `write_metric` is Modbus (holding field by canonical name). Cloud has
  `set_smartcontrol` / `update_settings`; those are cloud-only. Entities call
  coordinator helpers for those, not `isinstance` on the transport.
- Import maps and device types from `client.modbus`.

Exceptions: `AuthError`, `TransportError`, `RateLimitError`. HA and tests still
use aliases `APIAuthError`, `APIConnectionError`, `APIRateLimitError`. Do not
name a class `ConnectionError` (shadows the builtin).

## Git workflow

When a task is complete:

1. Create a feature branch from latest `main` (never commit directly to `main`).
2. Commit with a clear message.
3. Push the branch.
4. Open a PR against the default branch with `gh pr create`.

Do not ask for permission for these steps. Do not merge unless asked.

## Commits and PRs

**Commits** — conventional prefix when it fits (`feat:`, `fix:`, `refactor:`,
`test:`, `chore:`, `docs:`), imperative subject, optional body that says *why*.
Release automation commits `Update for new version <tag>` — leave that to CI.

**PR title** — same as a good commit subject. User-facing bugfixes often use
`fix: …`. Larger extractions may omit the prefix (`Cut over HA to Cloud XOR
Modbus clients and drop QvantumAPI`).

**PR body** — keep this shape:

```markdown
## Summary
- What changed, in bullets.
- Call out transport (cloud vs Modbus) and user-visible behavior.

## Test plan
- [x] `pytest` — N passed, coverage %
- [ ] In HA, … (only for behavior a unit test cannot prove)
```

Stacked work: one concern per PR, `Depends on #N` in the body, land on `main`
in order. Do not bundle unrelated refactors.

Labels (`bug` / `enhancement` / `chore`) feed release-drafter. Version lives in
`manifest.json` and `const.py`; the release workflow rewrites it from the tag.

## Tests

```bash
./run-tests.sh          # venv + requirements-test.txt + pytest
python -m pytest        # from repo root; pytest.ini sets coverage and timeout
```

- Coverage floor **80%** (`--cov-fail-under=80`). Aim to keep or raise it.
- Per-test timeout **30s** (`pytest-timeout`). A hung asyncio wait should fail,
  not freeze the suite.
- Modbus tests inject `modbus_connection.mock.MockModbusConnection` — never open
  a TCP socket.
- `tests/test_api.py` defines a **test-only** `QvantumAPI` helper that constructs
  Cloud or Modbus. Production code must not grow a new facade.
- Async tests that spawn tasks must cancel leftovers in `finally` (see
  `test_close_waits_for_in_flight_modbus_lock`).
- Dual-mode tests (HTTP and Modbus on one object) are obsolete; skip or rewrite
  against a single transport.

## Translations and entities

Edit `custom_components/qvantum/translations/*.json` together. Keep HVAC terms
consistent across languages; validate JSON. Entity unique IDs and config-entry
migrations live in `__init__.py` — bump `CONFIG_VERSION` when the entry schema
changes, and add a migration test.

Icons belong on `QvantumEntity`. Cloud-only entities (SmartControl, firmware
boards, access expiry, elevate-access) must not be created in Modbus mode.

## Product constraints

- `single_config_entry`: one instance; switch cloud ↔ Modbus via reconfigure.
- Modbus writes are opt-in (`CONF_MODBUS_WRITE`). Do not enable them by default.
- Interval-only option changes apply in place; host/port/unit/enablement reloads
  the entry.
- Do not invent cloud endpoints or Modbus registers. Maps live in
  `client/modbus/maps.py`; HTTP paths in `client/cloud/endpoints.py`.

---

*Keep this file accurate when architecture or workflow changes.*
