"""Tests for Qvantum config-entry diagnostics."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.qvantum.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)

REDACTED = "**REDACTED**"
# Same length/format as a real Qvantum serial number.
SERIAL = "3010100244154033"


def _coordinator() -> SimpleNamespace:
    return SimpleNamespace(
        modbus_enabled=False,
        poll_interval=120,
        device_id="dev-1",
        data={
            "device": {"id": "dev-1", "model": "QE-6"},
            "values": {"hpid": "dev-1", "bt2": 21.5, "hp_status": 3},
        },
        _enabled_metrics_cache={"dev-1": ["bt2", "bt1"]},
        _last_shower_cold_temp=8.5,
        _last_shower_flow_lpm=7.2,
        _last_shower_temp_c=38.1,
        _last_shower_duration_min=6.0,
        _last_tap_water_cap=3.4,
        _last_published_tap_water_cap=3.2,
        _last_published_tap_water_minutes=42,
        _tap_water_cap_zero_mode=False,
        _tap_water_cap_reheating_floor_mode=True,
        _shower_event_history=[{"start": "2026-09-25T06:00:00+00:00"}],
        client=SimpleNamespace(),
    )


def _maintenance() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "device_id": "dev-1",
            "firmware_versions": {
                "display_fw_version": "1.2.3",
                "cc_fw_version": "4.5.6",
            },
            "access_level": {
                "writeAccessLevel": 30,
                "expiresAt": "2026-01-26T18:35:29.768Z",
            },
            "firmware_changed": False,
            "last_check": "2026-09-25T10:00:00Z",
        },
        _last_firmware_versions={"display_fw_version": "1.2.3"},
    )


def _runtime(
    coordinator: SimpleNamespace | None,
    *,
    maintenance: SimpleNamespace | None = None,
    extra_dhw: SimpleNamespace | None = None,
    client: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        coordinator=coordinator,
        client=client if client is not None else SimpleNamespace(),
        maintenance_coordinator=maintenance,
        extra_dhw=extra_dhw,
        modbus_host="Qvantum-HP",
        modbus_port=502,
        modbus_unit_id=1,
    )


def _entry(runtime: SimpleNamespace | None = None, **overrides) -> SimpleNamespace:
    entry = SimpleNamespace(
        entry_id="entry-1",
        unique_id="test-unique-id",
        title="Qvantum",
        version=7,
        minor_version=0,
        state="loaded",
        data={"username": "user@example.com", "password": "hunter2"},
        options={},
    )
    if runtime is not None:
        entry.runtime_data = runtime
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


def test_redact_keys_cover_credentials():
    assert {
        "password",
        "username",
        "token",
        "refresh_token",
        "expiresAt",
        "serial",
        "device_id",
        "hpid",
    } <= TO_REDACT


@pytest.mark.asyncio
async def test_cloud_mode_structure(hass):
    runtime = _runtime(_coordinator(), maintenance=_maintenance())
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    assert result["runtime_data_loaded"] is True
    coordinator = result["coordinator"]
    assert coordinator["modbus_enabled"] is False
    assert coordinator["modbus_writable"] is False
    assert coordinator["poll_interval"] == 120
    assert coordinator["device_id"] == REDACTED
    assert coordinator["device"]["id"] == REDACTED
    assert coordinator["values"]["hpid"] == REDACTED
    assert coordinator["values"]["bt2"] == 21.5
    assert coordinator["enabled_metrics"] == {REDACTED: ["bt1", "bt2"]}
    assert coordinator["dhw_ema"]["last_shower_cold_temp"] == 8.5
    assert coordinator["dhw_ema"]["last_published_tap_water_minutes"] == 42
    assert coordinator["shower_event_history_count"] == 1
    assert result["extra_dhw"] is None
    assert result["modbus_link"] is None
    assert result["maintenance"]["firmware_versions"]["display_fw_version"] == "1.2.3"
    assert result["maintenance"]["firmware_changed"] is False
    json.dumps(result)  # diagnostics must stay JSON-serializable


@pytest.mark.asyncio
async def test_modbus_mode_structure(hass):
    coordinator = _coordinator()
    coordinator.modbus_enabled = True
    coordinator.client = SimpleNamespace(writable=True)
    timer = SimpleNamespace(restore_at=1712232000.0, unsub=object())
    runtime = _runtime(coordinator, extra_dhw=timer, client=coordinator.client)
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime, data={}))

    assert result["coordinator"]["modbus_enabled"] is True
    assert result["coordinator"]["modbus_writable"] is True
    assert result["extra_dhw"] == {"restore_at": 1712232000.0, "armed": True}
    assert result["modbus_link"] == {
        "host": "Qvantum-HP",
        "port": 502,
        "unit_id": 1,
    }
    # Maintenance coordinator exists only in cloud mode.
    assert result["maintenance"] is None


@pytest.mark.asyncio
async def test_secrets_and_expiry_are_redacted(hass):
    runtime = _runtime(_coordinator(), maintenance=_maintenance())
    entry = _entry(
        runtime,
        data={
            "username": "user@example.com",
            "password": "hunter2",
            "other": "keep",
        },
    )
    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["entry"]["data"]["username"] == "**REDACTED**"
    assert result["entry"]["data"]["other"] == "keep"
    # expiresAt is dropped entirely; only numeric access fields are reported.
    assert result["maintenance"]["access_level"] == {"writeAccessLevel": 30}

    serialized = json.dumps(result)
    assert "hunter2" not in serialized
    assert "user@example.com" not in serialized
    assert "2026-01-26T18:35:29.768Z" not in serialized


@pytest.mark.asyncio
async def test_device_serial_is_redacted_everywhere(hass):
    coordinator = _coordinator()
    coordinator.device_id = SERIAL
    coordinator.data = {
        "device": {
            "id": SERIAL,
            "serial": SERIAL,
            "model": "QE-6",
            "connectivity": {
                "connected": True,
                "timestamp": "2026-09-23T14:47:22.607Z",
            },
        },
        "values": {"hpid": SERIAL, "bt2": 20.3},
    }
    coordinator._enabled_metrics_cache = {SERIAL: ["bt2"]}
    entry = _entry(
        _runtime(coordinator),
        unique_id=SERIAL,
        title=f"Qvantum QE-6 ({SERIAL})",
    )
    result = await async_get_config_entry_diagnostics(hass, entry)

    assert SERIAL not in json.dumps(result)
    assert result["entry"]["title"] == f"Qvantum QE-6 ({REDACTED})"
    assert result["coordinator"]["device_id"] == REDACTED
    assert result["coordinator"]["device"]["id"] == REDACTED
    assert result["coordinator"]["device"]["serial"] == REDACTED
    assert result["coordinator"]["values"]["hpid"] == REDACTED
    assert result["coordinator"]["values"]["bt2"] == 20.3
    assert result["coordinator"]["enabled_metrics"] == {REDACTED: ["bt2"]}


@pytest.mark.asyncio
async def test_unloaded_entry_hides_serial_from_title(hass):
    entry = _entry(unique_id=SERIAL, title=f"Qvantum QE-6 ({SERIAL})")
    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["runtime_data_loaded"] is False
    assert SERIAL not in json.dumps(result)
    assert result["entry"]["title"] == f"Qvantum QE-6 ({REDACTED})"


@pytest.mark.asyncio
async def test_unloaded_entry_does_not_crash(hass):
    result = await async_get_config_entry_diagnostics(hass, _entry())

    assert result["runtime_data_loaded"] is False
    assert "coordinator" not in result
    assert result["entry"]["entry_id"] == "entry-1"
    assert result["entities"] == {"total": 0, "enabled": 0}


@pytest.mark.asyncio
async def test_missing_coordinators_render_none(hass):
    timer = SimpleNamespace(restore_at=None, unsub=None)
    runtime = _runtime(None, extra_dhw=timer)
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    assert result["coordinator"] is None
    assert result["maintenance"] is None
    assert result["extra_dhw"] == {"restore_at": None, "armed": False}


@pytest.mark.asyncio
async def test_malformed_state_is_tolerated(hass):
    coordinator = SimpleNamespace(
        modbus_enabled=True,
        poll_interval=None,
        device_id=None,
        data=None,
        _enabled_metrics_cache={"dev-1": "not-a-list"},
        client=object(),
    )
    maintenance = SimpleNamespace(
        data={"access_level": "expired", "firmware_versions": None},
        _last_firmware_versions=None,
    )
    runtime = _runtime(coordinator, maintenance=maintenance)
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    assert result["coordinator"]["values"] == {}
    assert result["coordinator"]["device"] == {}
    assert result["coordinator"]["modbus_writable"] is False
    assert result["coordinator"]["enabled_metrics"] == {"dev-1": "not-a-list"}
    assert result["coordinator"]["dhw_ema"]["last_shower_cold_temp"] is None
    assert result["coordinator"]["shower_event_history_count"] == 0
    assert result["maintenance"]["firmware_versions"] == {}
    assert result["maintenance"]["last_firmware_versions"] == {}
    assert result["maintenance"]["access_level"] == {}


@pytest.mark.asyncio
async def test_missing_device_section_still_redacts_values(hass):
    """Only one identity section is present: the scrub skips the other."""
    coordinator = _coordinator()
    coordinator.data = {"values": {"hpid": "dev-1", "bt2": 20.3}}
    runtime = _runtime(coordinator)
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    assert result["coordinator"]["device"] == {}
    assert result["coordinator"]["values"]["hpid"] == REDACTED
    assert result["coordinator"]["values"]["bt2"] == 20.3


@pytest.mark.asyncio
async def test_unknown_identifiers_pass_through(hass):
    """No unique id, device id or hpid: the scrub leaves the payload alone."""
    coordinator = _coordinator()
    coordinator.device_id = None
    coordinator.data = None
    entry = _entry(_runtime(coordinator), unique_id=None)
    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"]["device_id"] is None
    assert result["coordinator"]["values"] == {}
    assert result["entry"]["title"] == "Qvantum"


@pytest.mark.asyncio
async def test_entity_counts_report_total_and_enabled(hass):
    entries = [
        SimpleNamespace(disabled_by=None),
        SimpleNamespace(disabled_by="user"),
        SimpleNamespace(disabled_by=None),
    ]
    registry = MagicMock()
    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=registry),
        patch(
            "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
            return_value=entries,
        ),
    ):
        result = await async_get_config_entry_diagnostics(
            hass, _entry(_runtime(_coordinator()))
        )

    assert result["entities"] == {"total": 3, "enabled": 2}
