"""Tests for Qvantum config-entry diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.qvantum.heating_curve import ActiveRoomGain
from custom_components.qvantum.curve_coordinator import CurveSnapshot
from custom_components.qvantum.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)
from homeassistant.util import dt as dt_util

from custom_components.qvantum.plant_analytics import DhwMeterWindow
from custom_components.qvantum.solar_gain import SolarDailyModel, SolarModel

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
    curve: SimpleNamespace | None = None,
    client: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        coordinator=coordinator,
        client=client if client is not None else SimpleNamespace(),
        maintenance_coordinator=maintenance,
        extra_dhw=extra_dhw,
        curve_coordinator=curve,
        modbus_host="Qvantum-HP",
        modbus_port=502,
        modbus_unit_id=1,
    )


def _curve_coordinator() -> SimpleNamespace:
    snapshot = CurveSnapshot(
        shadow=False,
        points={"curve_30": 30.0, "curve_minus_30": 55.0},
        baseline={"curve_30": 28.0, "curve_minus_30": 52.0},
        adjustment_c=1.5,
        outdoor_c=0.4,
        night_day_c=0.3,
        solar_c=0.5,
        load_c=0.3,
        capped_by_indoor=False,
        deviation_c=0.2,
        ready=True,
        blocker=None,
        median_abs_c=0.4,
        max_abs_c=1.1,
        p95_abs_c=0.9,
        window_hours=72.0,
        model=_solar_model(),
        daily_model=_solar_daily_model(),
        calibrated_at="2026-09-25T10:00:00+00:00",
        baseline_auto=True,
        trims={"curve_30": 0.1},
        clamped=True,
        clamped_points=("curve_minus_30",),
        floor_pinned_points=("curve_30", "curve_20"),
        baseline_learned_hours=48,
        baseline_outdoor_min_c=-2.0,
        baseline_outdoor_max_c=11.0,
        extrapolated_points=("curve_minus_20", "curve_minus_30"),
        room_error_median_abs_c=0.6,
        room_error_median_c=-0.2,
        room_error_p90_abs_c=1.4,
        room_error_samples=96,
        room_error_source="room_temp_external",
        indoor_margin_c=0.4,
        indoor_cap_armed=True,
    )
    return SimpleNamespace(
        data=snapshot,
        active=True,
        writable=True,
        _mode="active",
        _revert_pending=False,
        _forecast_ok=True,
        _baseline={"curve_30": 28.0, "curve_minus_30": 52.0},
        _daily_model=_solar_daily_model(),
        _baseline_auto=True,
        _curve_23=20,
        _baseline_stats={
            "hours": 48,
            "min_c": -2.0,
            "max_c": 11.0,
            "corrected": True,
        },
        _trims={"curve_30": 0.1},
        _trim_evidence={"curve_30": 12, "curve_minus_30": 0},
        counters={
            "since": "2026-10-01",
            "reverts": 2,
            "trim_updates": 6,
            "days_clamped": 3,
            "days_extrapolated": 4,
        },
        _room_gain=1.8,
        _room_gain_source="room_temp_external",
        _room_gain_factor=5.0,
        _room_gain_from_factor=True,
        _room_gain_fitted=5.08,
        _room_gain_active=ActiveRoomGain(
            gain_c_per_c=4.33,
            hours=18,
            buckets=3,
            span_c=0.96,
            source="room_temp_external",
            std_err_c_per_c=0.4,
            r2=0.88,
            residual_std_c=0.15,
            leave_one_out_c_per_c=2.47,
            valid=False,
            notes="one hour carries the estimate",
        ),
        _room_gain_fitted_source="room_temp_external",
        _active_since_ts=1758790800.0,
        _model=_solar_model(),
        _calibrated_at="2026-09-25T10:00:00+00:00",
        _last_calibration_ts=1758794400.0,
        _last_calibration_ok=False,
        _last_baseline_learn_ts=1758794400.0,
        _last_active_ts=None,
        _last_trim_ts=1758794400.0,
        _indoor_margins=[(1758794400.0, 0.5)],
        _powers=[1200.0, 1300.0],
    )


def _solar_model() -> SolarModel:
    return SolarModel(
        a_w_per_k=120.0,
        b_m2=2.5,
        c_w=900.0,
        trust=0.8,
        r2_opaque=0.9,
        r2_solar=0.7,
        b_std_err=0.3,
        n_opaque=40,
        n_solar=30,
        valid=True,
    )


def _solar_daily_model() -> SolarDailyModel:
    return SolarDailyModel(
        a_w_per_k=152.0,
        b_m2=1.9,
        c_w=-600.0,
        trust=0.7,
        r2=0.92,
        n_days=25,
        b_std_err=0.6,
        t_stat=3.0,
        b_first_half_m2=1.7,
        b_second_half_m2=2.1,
        b_jackknife_min_m2=1.5,
        b_jackknife_max_m2=2.2,
        valid=True,
        notes="days=25 t=3.00 r2=0.920 jackknife=1.50-2.20",
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
async def test_cop_diagnostics_reports_held_figures_and_the_open_charge(hass):
    """A held COP and the DHW charge it measures are part of the support dump."""
    coordinator = _coordinator()
    coordinator._cop_last = {"cop_heating": 3.4, "cop_dhw": 1.0}
    coordinator._cop_last_times = {
        "cop_heating": datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc),
        "cop_dhw": datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc),
    }
    coordinator._dhw_tank_liters = 143.2
    coordinator._dhw_charge = SimpleNamespace(
        started=datetime(2026, 10, 18, 9, 3, tzinfo=timezone.utc),
        start_tank=57.3,
        last_tank=64.1,
        draw_kwh=0.2,
        electrical_kwh=1.4,
        compressor_kwh=0.0,
    )
    runtime = _runtime(coordinator, maintenance=_maintenance())
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    cop = result["coordinator"]["cop"]
    assert cop["heating"] == 3.4
    assert cop["dhw"] == 1.0
    assert cop["heating_updated_at"] == "2026-10-18T08:00:00+00:00"
    assert cop["dhw_tank_liters"] == 143.2
    assert cop["dhw_charge"]["started"] == "2026-10-18T09:03:00+00:00"
    assert cop["dhw_charge"]["start_tank_c"] == 57.3
    assert cop["dhw_charge"]["electrical_kwh"] == 1.4
    json.dumps(result)


@pytest.mark.asyncio
async def test_cop_diagnostics_reports_the_dhw_meter_window(hass):
    """The meter warning is only checkable if its two totals travel with it."""
    coordinator = _coordinator()
    window = DhwMeterWindow()
    # Near now (the dump prunes buckets that left the seven-day window), but
    # pinned inside one hour: two samples 60 s apart straddled an hour
    # boundary whenever the suite ran in the first minute after one, and the
    # dump then reported two buckets.
    base = dt_util.utcnow().timestamp()
    base -= base % 3600.0
    base += 10.0
    window.add(
        now_ts=base,
        flow_l_min=5.0,
        hot_out_c=55.0,
        cold_in_c=10.0,
        meter_kwh=0.0,
    )
    window.add(
        now_ts=base + 60.0,
        flow_l_min=5.0,
        hot_out_c=55.0,
        cold_in_c=10.0,
        meter_kwh=0.3,
    )
    coordinator._dhw_meter = window
    runtime = _runtime(coordinator, maintenance=_maintenance())
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    meter = result["coordinator"]["cop"]["dhw_meter"]
    assert meter["buckets"] == 1
    assert meter["estimated_kwh"] > 0.0
    assert meter["metered_kwh"] == 0.3
    assert meter["draw_hours"] == pytest.approx(0.02)
    json.dumps(result)


@pytest.mark.asyncio
async def test_cop_diagnostics_tolerates_a_broken_dhw_meter(hass):
    """A dump must never fail because the window object misbehaves."""
    coordinator = _coordinator()
    coordinator._dhw_meter = SimpleNamespace(
        totals=MagicMock(side_effect=RuntimeError("x"))
    )
    runtime = _runtime(coordinator, maintenance=_maintenance())
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    assert result["coordinator"]["cop"]["dhw_meter"] == {"error": "unavailable"}


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
async def test_external_room_feed_state(hass):
    coordinator = _coordinator()
    coordinator.modbus_enabled = True
    coordinator.client = SimpleNamespace(writable=True)
    coordinator.external_room_temp_entity_id = "sensor.room"
    coordinator._external_room_last_value = 20.6
    coordinator._external_room_last_write_ts = "2026-10-05T19:00:00+00:00"
    coordinator._external_room_write_errors = 2
    runtime = _runtime(coordinator, client=coordinator.client)
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime, data={}))

    assert result["coordinator"]["external_room_feed"] == {
        "entity_id": "sensor.room",
        "last_value": 20.6,
        "last_write": "2026-10-05T19:00:00+00:00",
        "write_errors": 2,
    }
    json.dumps(result)


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


@pytest.mark.asyncio
async def test_curve_diagnostics_exports_adaptive_state(hass):
    runtime = _runtime(_coordinator(), curve=_curve_coordinator())
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    curve = result["curve"]
    assert curve["active"] is True
    assert curve["writable"] is True
    assert curve["mode"] == "active"
    assert curve["shadow"] is False
    assert curve["revert_pending"] is False
    assert curve["forecast_ok"] is True
    assert curve["forecast_reused"] is False
    assert curve["baseline"]["curve_30"] == 28.0
    assert curve["baseline_auto"] is True
    assert curve["temp_compensation_curve"] == 20
    assert curve["baseline_stats"] == {
        "hours": 48,
        "min_c": -2.0,
        "max_c": 11.0,
        "corrected": True,
    }
    assert curve["points"]["curve_minus_30"] == 55.0
    assert curve["trims"] == {"curve_30": 0.1}
    assert curve["adjustment_c"] == 1.5
    assert curve["outdoor_c"] == 0.4
    assert curve["night_day_c"] == 0.3
    assert curve["solar_c"] == 0.5
    assert curve["load_c"] == 0.3
    assert curve["capped_by_indoor"] is False
    assert curve["clamped"] is True
    assert curve["clamped_points"] == ["curve_minus_30"]
    # A warm end pinned on the pump's floor is named apart from the actionable
    # clamp: a dump has to distinguish "adjust the baseline" from "nothing to do".
    assert curve["floor_pinned_points"] == ["curve_30", "curve_20"]
    assert curve["deviation_c"] == 0.2
    assert curve["ready"] is True
    assert curve["blocker"] is None
    assert curve["median_abs_c"] == 0.4
    assert curve["max_abs_c"] == 1.1
    assert curve["p95_abs_c"] == 0.9
    assert curve["window_hours"] == 72.0
    assert curve["baseline_learned_hours"] == 48
    assert curve["baseline_outdoor_min_c"] == -2.0
    assert curve["baseline_outdoor_max_c"] == 11.0
    assert curve["extrapolated_points"] == ["curve_minus_20", "curve_minus_30"]
    assert curve["room_gain"] == 1.8
    assert curve["room_gain_source"] == "room_temp_external"
    # The pump's own setting, so a report shows both numbers side by side.
    assert curve["room_gain_factor"] == 5.0
    assert curve["room_gain_from_factor"] is True
    assert curve["counters"] == {
        "since": "2026-10-01",
        "reverts": 2,
        "trim_updates": 6,
        "days_clamped": 3,
        "days_extrapolated": 4,
    }
    assert curve["trim_evidence"] == {"curve_30": 12, "curve_minus_30": 0}
    assert curve["room_error"] == {
        "median_abs_c": 0.6,
        "median_c": -0.2,
        "p90_abs_c": 1.4,
        "samples": 96,
        "source": "room_temp_external",
    }
    assert curve["model"]["trust"] == 0.8
    assert curve["model"]["b_m2"] == 2.5
    assert curve["model_daily"]["b_m2"] == 1.9
    assert curve["model_daily"]["t_stat"] == 3.0
    assert curve["model_daily"]["n_days"] == 25
    assert curve["model_daily"]["valid"] is True
    assert curve["model_daily"]["b_jackknife_min_m2"] == 1.5
    assert curve["calibrated_at"] == "2026-09-25T10:00:00+00:00"
    assert curve["calibration_ok"] is False
    # The measured slope beside the applied gain: the applied one may just be
    # the pump's configured factor.
    assert curve["room_gain_fitted"] == 5.08
    # The active-hour measurement, with the evidence that it is not usable yet.
    assert curve["room_gain_active"]["gain_c_per_c"] == 4.33
    assert curve["room_gain_active"]["valid"] is False
    assert curve["room_gain_active"]["leave_one_out_c_per_c"] == 2.47
    assert curve["room_gain_active"]["std_err_c_per_c"] == 0.4
    assert curve["room_gain_active"]["t_stat"] == pytest.approx(4.33 / 0.4)
    assert curve["room_gain_active"]["source"] == "room_temp_external"
    assert curve["room_gain_fitted_source"] == "room_temp_external"
    # Whether the warm-house brake can act at all.
    assert curve["indoor_cap_armed"] is True
    assert curve["indoor_margin_c"] == 0.4
    assert curve["timestamps"]["last_trim_ts"] == 1758794400.0
    assert curve["timestamps"]["last_active_ts"] is None
    # Without the start of the active period the readiness window cannot tell
    # hours the pump followed our table from hours it followed its own.
    assert curve["timestamps"]["active_since_ts"] == 1758790800.0
    assert curve["indoor_margins"] == [[1758794400.0, 0.5]]
    assert curve["power_samples"] == [1200.0, 1300.0]
    json.dumps(result)


@pytest.mark.asyncio
async def test_curve_diagnostics_absent_in_cloud_mode(hass):
    runtime = _runtime(_coordinator(), maintenance=_maintenance())
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    assert result["curve"] is None


@pytest.mark.asyncio
async def test_curve_diagnostics_tolerates_malformed_state(hass):
    curve = SimpleNamespace(data=None)
    runtime = _runtime(_coordinator(), curve=curve)
    result = await async_get_config_entry_diagnostics(hass, _entry(runtime))

    curve_result = result["curve"]
    assert curve_result["active"] is False
    assert curve_result["writable"] is False
    assert curve_result["points"] == {}
    assert curve_result["baseline"] == {}
    assert curve_result["trims"] == {}
    assert curve_result["model"] is None
    assert curve_result["model_daily"] is None
    assert curve_result["calibration_ok"] is None
    assert curve_result["extrapolated_points"] == []
    assert curve_result["trim_evidence"] == {}
    assert curve_result["room_error"] is None
    assert curve_result["adjustment_c"] is None
    assert curve_result["indoor_margins"] == []
    assert curve_result["power_samples"] == []
    json.dumps(result)
