"""Tests for the opt-in summary export (whitelist-built, identifier-free)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from custom_components.qvantum.curve_coordinator import CURVE_KEYS
from custom_components.qvantum.client.modbus.maps import MODBUS_HOLDING_TO_SETTINGS_MAP
from custom_components.qvantum.summary import (
    FITTED_EFFICIENCY_KEYS,
    MODBUS_ONLY_PLANT_KEYS,
    PLANT_KEYS,
    SUMMARY_SCHEMA_VERSION,
    build_summary,
    device_summary,
    plant_summary,
)

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
        "_room_gain": 5.08,
        "_room_gain_source": "room_temp_external",
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

    assert curve["room_gain_configured_c_per_c"] == 5.0
    assert curve["room_gain_fitted_c_per_c"] == 5.08
    assert curve["room_gain_source"] == "room_temp_external"
    assert curve["daily_solar_b_m2"] == 1.875
    assert curve["daily_solar_days"] == 26


def test_a_foreign_gain_source_is_dropped() -> None:
    coordinator = curve_coordinator(_room_gain_source="sensor.inomhustemperatur")

    payload = build(curve=coordinator)

    assert payload["curve"]["room_gain_source"] is None
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
