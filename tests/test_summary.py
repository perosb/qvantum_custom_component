"""Tests for the opt-in summary export (whitelist-built, identifier-free)."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

from custom_components.qvantum.const import DOMAIN
from custom_components.qvantum.curve_coordinator import CURVE_KEYS
from custom_components.qvantum.heating_curve import ActiveRoomGain
from custom_components.qvantum.client.modbus.maps import MODBUS_HOLDING_TO_SETTINGS_MAP
from custom_components.qvantum.summary import (
    FITTED_EFFICIENCY_KEYS,
    MODBUS_ONLY_PLANT_KEYS,
    PLANT_KEYS,
    QvantumSummaryView,
    SUMMARY_FILENAME,
    SUMMARY_SCHEMA_VERSION,
    SUMMARY_URL,
    build_summary,
    counters_summary as summary_counters_summary,
    device_metadata,
    device_summary,
    plant_summary,
)

ENTRY_ID = "01JTESTENTRY0000000000000"

#: Values as the coordinator holds them: settings, readings and — crucially —
#: the user's own entity ids and names. None of the latter may ever travel.
VALUES = {
    "sensor_mode": 4,
    "room_comp_factor": 5.0,
    "curve_type_heating": 1,
    "heating_offset": 0,
    "tap_water_start": 45,
    "tap_water_stop": 50,
    "max_heating_supply": 65,
    "stop_heating": 17,
    "room_temp_external": 19.8,
    "bt2": 20.5,
    "external_room_temp_entity": "sensor.inomhustemperatur",
    "external_room_temp_friendly": "Tvättstugan temperatur",
}

IDENTIFIER_STRINGS = (
    "sensor.inomhustemperatur",
    "Tvättstugan temperatur",
    "Tvättstugan",
)


def curve_coordinator(**overrides):
    """A curve coordinator stub with the identified gain pair.

    The baseline/climate fields live on the **snapshot** (``coordinator.data``),
    not on the coordinator — a stub that puts them on the coordinator would hide
    a broken field path.
    """
    snapshot = SimpleNamespace(
        median_abs_c=1.26,
        p95_abs_c=3.87,
        ready=False,
        baseline_outdoor_min_c=3.4,
        baseline_outdoor_max_c=14.1,
        baseline_learned_hours=148,
    )
    values = {
        "active": True,
        "data": snapshot,
        "_room_gain_factor": 5.0,
        "_room_gain": 5.0,
        "_room_gain_source": "room_temp_external",
        "_room_gain_fitted": 5.08,
        "_room_gain_fitted_source": "room_temp_external",
        "_model": SimpleNamespace(a_w_per_k=111.1, b_m2=0.0, trust=0.69),
        "_daily_model": SimpleNamespace(b_m2=1.875, trust=0.69, n_days=26),
        "_baseline": dict.fromkeys(CURVE_KEYS, 20.0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def efficiency_snapshot():
    """An efficiency snapshot with the identified envelope figures."""
    return SimpleNamespace(
        heat_loss_w_per_k=152.4,
        thermal_time_constant=41.5,
        thermal_gain_k_per_h=0.12,
        thermal_mass_kwh_per_k=1.7,
        dhw_standing_loss=0.04,
        compressor_starts_per_hour=1.2,
        efficiency_health_grade="B",
        efficiency_health_score=0.72,
        efficiency_health_coverage=0.8,
        solar_gain_now_w=241.0,
    )


def build(**overrides):
    kwargs = {
        "version": "2026.10.4",
        "transport": "modbus",
        "device": SimpleNamespace(
            manufacturer="Qvantum",
            model="QE-6",
            sw_version="2026.10.4",
            hw_version="2.1",
        ),
        "values": VALUES,
        "curve": curve_coordinator(),
        "efficiency": efficiency_snapshot(),
    }
    kwargs.update(overrides)
    return build_summary(**kwargs)


def strings_in(payload) -> list[str]:
    """Every string anywhere in the payload."""
    found: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            found.extend(strings_in(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(strings_in(value))
    elif isinstance(payload, str):
        found.append(payload)
    return found


def test_summary_carries_a_schema_version_and_a_date_only() -> None:
    payload = build()

    assert payload["schema_version"] == SUMMARY_SCHEMA_VERSION
    assert payload["integration_version"] == "2026.10.4"
    assert payload["transport"] == "modbus"
    # A date, not a timestamp: the time of day adds identification and answers
    # no question in the pool.
    assert payload["generated_on"] == date.today().isoformat()
    assert "T" not in payload["generated_on"]
    assert build(generated_on="2026-10-15")["generated_on"] == "2026-10-15"


def test_summary_never_carries_an_identifier() -> None:
    """The whole point: entity ids and friendly names cannot travel."""
    payload = build()
    flattened = strings_in(payload)

    for identifier in IDENTIFIER_STRINGS:
        assert identifier not in flattened
    for text in flattened:
        assert not text.startswith(
            ("sensor.", "binary_sensor.", "climate.", "switch.", "number.", "select.")
        )


def test_plant_keys_are_canonical_setting_names() -> None:
    """A raw holding-register key exports as null on every cloud installation."""
    canonical = set(MODBUS_HOLDING_TO_SETTINGS_MAP.values())

    for key in PLANT_KEYS:
        assert key in canonical, f"{key} is not a canonical setting name"
    # Modbus-only fields are declared, not silently promised.
    assert set(MODBUS_ONLY_PLANT_KEYS) <= set(PLANT_KEYS)


def test_a_cloud_payload_leaves_the_supply_limits_null() -> None:
    """The cloud API does not expose them; a null is the honest answer."""
    cloud_values = {
        "sensor_mode": 4,
        "room_comp_factor": 5.0,
        "curve_type_heating": 1,
        "indoor_temperature_offset": 0,
        "tap_water_start": 45,
        "tap_water_stop": 50,
        "stop_heating": 17,
    }

    plant = plant_summary(cloud_values)

    assert plant["indoor_temperature_offset"] == 0
    assert plant["max_heating_supply"] is None


def test_plant_carries_only_the_whitelisted_settings() -> None:
    plant = plant_summary(VALUES)

    assert set(plant) == set(PLANT_KEYS)
    assert plant["room_comp_factor"] == 5.0
    assert plant["sensor_mode"] == 4
    # Readings are not settings and must not ride along.
    assert "room_temp_external" not in plant
    assert "bt2" not in plant


def test_summary_degrades_without_a_curve_or_efficiency() -> None:
    payload = build(curve=None, efficiency=None)

    assert payload["curve"] is None
    assert payload["climate"] == {
        "outdoor_min_c": None,
        "outdoor_max_c": None,
        "heating_hours": None,
    }
    assert set(payload["fitted"]) == set(FITTED_EFFICIENCY_KEYS)
    assert all(value is None for value in payload["fitted"].values())
    assert payload["verdicts"] == {}


def test_climate_comes_from_the_curve_snapshot() -> None:
    """The observed span and heating hours are snapshot fields."""
    payload = build()

    assert payload["climate"] == {
        "outdoor_min_c": 3.4,
        "outdoor_max_c": 14.1,
        "heating_hours": 148,
    }


def test_the_gain_pair_travels_because_the_pool_needs_it() -> None:
    """Configured against fitted: the question the first pool answers."""
    curve = build()["curve"]

    # Applied is the pump's factor; fitted is the measured slope. Reporting the
    # same number twice would hide exactly the comparison the pool needs.
    gain = curve["room_gain"]
    assert gain["configured_c_per_c"] == 5.0
    assert gain["applied_c_per_c"] == 5.0
    assert gain["applied_source"] == "room_temp_external"
    assert gain["fitted"]["c_per_c"] == 5.08
    assert gain["fitted"]["source"] == "room_temp_external"
    assert gain["fitted"]["window"] == "auto"
    assert curve["daily_solar_b_m2"] == 1.875
    assert curve["daily_solar_days"] == 26


def test_a_foreign_gain_source_is_dropped() -> None:
    coordinator = curve_coordinator(_room_gain_source="sensor.inomhustemperatur")

    payload = build(curve=coordinator)

    assert payload["curve"]["room_gain"]["applied_source"] is None
    assert "sensor.inomhustemperatur" not in strings_in(payload)


@pytest.mark.parametrize("value", [float("nan"), True, "5.0", None])
def test_non_numeric_settings_become_none(value) -> None:
    assert plant_summary({"room_comp_factor": value})["room_comp_factor"] is None


def test_device_metadata_keeps_names_and_versions() -> None:
    summary = device_summary(
        SimpleNamespace(
            manufacturer="NIBE Element",
            model="QE-6",
            sw_version="2026.10.4",
            hw_version=None,
        )
    )

    assert summary["manufacturer"] == "NIBE Element"
    assert summary["model"] == "QE-6"
    # Version strings contain dots; the guard must not reject them.
    assert summary["firmware"] == "2026.10.4"
    assert summary["hardware"] is None


def test_device_metadata_scopes_the_lookup_to_the_entry() -> None:
    """The lookup is unambiguous: identifier *and* owning config entry."""
    with patch("custom_components.qvantum.summary.dr") as patched_registry:
        registry = MagicMock()
        registry.async_get_device_by_identifier.return_value = "device-entry"
        patched_registry.async_get.return_value = registry

        found = device_metadata(MagicMock(), "3010100111111111", ENTRY_ID)

    assert found == "device-entry"
    registry.async_get_device.assert_not_called()
    registry.async_get_device_by_identifier.assert_called_once_with(
        (DOMAIN, "qvantum-3010100111111111"), ENTRY_ID
    )


def test_device_metadata_survives_a_stripped_harness() -> None:
    """No registry: metadata is dropped rather than the export failing."""
    with patch("custom_components.qvantum.summary.dr") as patched_registry:
        patched_registry.async_get.side_effect = RuntimeError("no registry")

        assert device_metadata(MagicMock(), "3010100111111111", ENTRY_ID) is None


def test_device_summary_without_a_device() -> None:
    assert set(device_summary(None)) == {
        "manufacturer",
        "model",
        "firmware",
        "hardware",
    }
    assert all(value is None for value in device_summary(None).values())


def test_free_text_is_rejected_by_the_character_whitelist() -> None:
    summary = device_summary(
        SimpleNamespace(
            manufacturer="Tvättstugan temperatur",  # non-ASCII, user-shaped
            model="x" * 60,  # absurdly long
            sw_version="sensor.inomhustemperatur",  # entity id shaped
            hw_version="  ",
        )
    )

    assert summary["manufacturer"] is None
    assert summary["model"] is None
    # Dots are not the discriminator (versions have them); the entity-id prefix
    # is, so an id-shaped string is refused even from a source that should
    # never produce one.
    assert summary["firmware"] is None
    assert summary["hardware"] is None


def test_verdicts_can_be_attached_when_they_exist() -> None:
    payload = build(
        verdicts={"gain_vs_factor": {"delta": 0.08, "ci": [0.0, 0.16]}}
    )

    assert payload["verdicts"]["gain_vs_factor"]["delta"] == 0.08


def summary_request(*, entry: bool = True):
    """A mocked request whose app carries a hass with (or without) an entry."""
    hass = MagicMock()
    if not entry:
        hass.config_entries.async_entries.return_value = []
    else:
        runtime = SimpleNamespace(
            coordinator=SimpleNamespace(
                data={"values": VALUES},
                device_id="3010100111111111",
                modbus_enabled=True,
            ),
            efficiency_coordinator=SimpleNamespace(data=efficiency_snapshot()),
            curve_coordinator=curve_coordinator(),
        )
        hass.config_entries.async_entries.return_value = [
            SimpleNamespace(runtime_data=runtime, entry_id=ENTRY_ID)
        ]
    request = make_mocked_request("GET", SUMMARY_URL)
    request.app["hass"] = hass
    return request


async def test_the_summary_view_serves_a_download() -> None:
    """The browser downloads the same whitelist-built payload as the file."""
    with patch("custom_components.qvantum.summary.dr") as patched_registry:
        registry = MagicMock()
        patched_registry.async_get.return_value = registry
        response = await QvantumSummaryView().get(summary_request())

    # Scoped to the owning entry: identifiers are only unique per config entry,
    # so the deprecated unscoped ``async_get_device`` must not be used.
    registry.async_get_device.assert_not_called()
    registry.async_get_device_by_identifier.assert_called_once_with(
        (DOMAIN, "qvantum-3010100111111111"), ENTRY_ID
    )

    assert response.status == 200
    disposition = response.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert SUMMARY_FILENAME in disposition
    payload = json.loads(response.text)
    assert payload["schema_version"] == SUMMARY_SCHEMA_VERSION
    assert payload["plant"]["room_comp_factor"] == 5.0
    assert payload["climate"]["heating_hours"] == 148
    for identifier in IDENTIFIER_STRINGS:
        assert identifier not in response.text


async def test_the_summary_view_reports_a_missing_entry() -> None:
    """No configured entry: 404 rather than an empty summary."""
    response = await QvantumSummaryView().get(summary_request(entry=False))

    assert response.status == 404
    assert json.loads(response.text)["exception"] == "not_loaded"


def test_the_summary_view_requires_authentication() -> None:
    """The session carries the auth; no token in the URL."""
    assert QvantumSummaryView.requires_auth is True
    assert QvantumSummaryView.url == SUMMARY_URL


def test_the_fitted_gain_is_null_until_a_fit_has_run() -> None:
    """An install whose factor wins reports no fitted number, not the factor."""
    coordinator = curve_coordinator(_room_gain_fitted=None, _room_gain_fitted_source=None)

    curve = build(curve=coordinator)["curve"]

    gain = curve["room_gain"]
    assert gain["configured_c_per_c"] == 5.0
    assert gain["applied_c_per_c"] == 5.0
    assert gain["fitted"]["c_per_c"] is None
    assert gain["fitted"]["source"] is None
    assert gain["fitted"]["window"] == "auto"
    assert gain["fitted"]["valid"] is False


def test_an_active_measurement_travels_with_its_evidence() -> None:
    """The active-hour slope wins, and says why it may not be trusted."""
    estimate = ActiveRoomGain(
        gain_c_per_c=4.33,
        hours=18,
        buckets=3,
        span_c=0.96,
        source="room_temp_external",
        std_err_c_per_c=0.40,
        r2=0.88,
        residual_std_c=0.15,
        leave_one_out_c_per_c=2.47,
        valid=False,
        notes="one hour carries the estimate (leave-one-out 2.47 °C/°C > 0.50)",
    )

    fitted = build(curve=curve_coordinator(_room_gain_active=estimate))["curve"][
        "room_gain"
    ]["fitted"]

    assert fitted["window"] == "active"
    assert fitted["c_per_c"] == 4.33
    assert fitted["source"] == "room_temp_external"
    assert fitted["valid"] is False
    assert "leave-one-out" in fitted["notes"]
    assert fitted["hours"] == 18
    assert fitted["buckets"] == 3
    assert fitted["r2"] == 0.88
    assert fitted["std_err_c_per_c"] == 0.40
    assert fitted["t_stat"] == pytest.approx(4.33 / 0.40)
    assert fitted["leave_one_out_c_per_c"] == 2.47


def test_counters_block_is_exported_and_absent_on_cloud():
    """How often the cold/warm end was exercised, as counts."""
    coordinator = SimpleNamespace(
        counters={
            "since": "2026-10-01",
            "reverts": 2,
            "trim_updates": 6,
            "days_clamped": 3,
            "days_extrapolated": 4,
        }
    )

    counters = summary_counters_summary(coordinator)

    assert counters == {
        "since": "2026-10-01",
        "reverts": 2,
        "trim_updates": 6,
        "days_clamped": 3,
        "days_extrapolated": 4,
    }
    # No curve coordinator (cloud mode) means no counters at all.
    assert summary_counters_summary(None) is None
    assert summary_counters_summary(SimpleNamespace()) is None


def test_schema_version_covers_the_counters_block():
    from custom_components.qvantum.summary import SUMMARY_SCHEMA_VERSION

    assert SUMMARY_SCHEMA_VERSION >= 5
