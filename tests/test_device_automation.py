"""Tests for Qvantum device triggers and conditions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.const import (
    CONF_CONDITION,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)

from custom_components.qvantum.const import (
    DHW_MIN_SHOWER_FLOW_LPM,
    DHW_TANK_LOW_SHOWERS,
    DOMAIN,
    FILTER_SOON_DUE_HOURS,
)
from custom_components.qvantum.device_automation_helpers import (
    BINARY_CONDITION_MAP,
    BINARY_TRIGGER_MAP,
    CONDITION_DHW_SHOWER_ACTIVE,
    CONDITION_DHW_TANK_LOW,
    CONDITION_FILTER_SOON_DUE,
    NUMERIC_CONDITION_MAP,
    NUMERIC_TRIGGER_MAP,
    TRIGGER_DHW_SHOWER_STARTED,
    TRIGGER_DHW_TANK_LOW,
    TRIGGER_FILTER_SOON_DUE,
    async_entries_for_status_metrics,
    filter_below_hours,
    metric_from_unique_id,
)
from custom_components.qvantum import device_condition, device_trigger


def _entry(*, unique_id: str, domain: str, entry_id: str = "uuid-1") -> SimpleNamespace:
    return SimpleNamespace(
        unique_id=unique_id,
        domain=domain,
        id=entry_id,
        entity_id=f"{domain}.stub",
        config_entry_id="entry-1",
    )


def _hass_with_write_access(
    *,
    modbus: bool = False,
    writable: bool = False,
    write_access_level: int | None = None,
) -> MagicMock:
    """Build a hass stub whose config entry reports the given write access."""
    coordinator = SimpleNamespace(
        modbus_enabled=modbus, client=SimpleNamespace(writable=writable)
    )
    maintenance = SimpleNamespace(
        data=None
        if write_access_level is None
        else {"access_level": {"writeAccessLevel": write_access_level}}
    )
    runtime = SimpleNamespace(
        coordinator=coordinator, maintenance_coordinator=maintenance
    )
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        runtime_data=runtime
    )
    return hass


def test_metric_from_unique_id_extracts_status_metrics():
    assert metric_from_unique_id("qvantum_wifi_connected_123") == "wifi_connected"
    assert metric_from_unique_id("qvantum_time_to_defrost_pump-1") == "time_to_defrost"
    assert metric_from_unique_id("qvantum_alarm_active_123") == "alarm_active"
    assert (
        metric_from_unique_id("qvantum_ventilation_filter_time_left_dev")
        == "ventilation_filter_time_left"
    )
    assert metric_from_unique_id("qvantum_bf1_l_min_123") == "bf1_l_min"
    assert metric_from_unique_id("qvantum_tap_water_cap_123") == "tap_water_cap"
    assert metric_from_unique_id("qvantum_extra_tap_water_123") == "extra_tap_water"


def test_metric_from_unique_id_ignores_prefix_collisions():
    """compressor_blocked_sec must not resolve as compressor_blocked."""
    assert metric_from_unique_id("qvantum_compressor_blocked_sec_123") is None
    assert metric_from_unique_id("qvantum_compressor_blocked_123") == "compressor_blocked"


def test_metric_from_unique_id_ignores_setting_and_button_prefixes():
    """extra_tap_water_60min and tap_water_capacity_target are not status metrics."""
    assert metric_from_unique_id("qvantum_extra_tap_water_60min_123") is None
    assert metric_from_unique_id("qvantum_tap_water_capacity_target_123") is None
    assert metric_from_unique_id("qvantum_extra_tap_water_123") == "extra_tap_water"
    assert metric_from_unique_id("qvantum_tap_water_cap_123") == "tap_water_cap"


def test_metric_from_unique_id_rejects_unknown():
    assert metric_from_unique_id(None) is None
    assert metric_from_unique_id("other_wifi_connected_1") is None
    assert metric_from_unique_id("qvantum_bt1_123") is None


def test_filter_below_hours_matches_const():
    assert filter_below_hours() == float(FILTER_SOON_DUE_HOURS)
    assert FILTER_SOON_DUE_HOURS == 48


def test_dhw_numeric_thresholds_match_const():
    assert NUMERIC_TRIGGER_MAP[TRIGGER_DHW_SHOWER_STARTED] == (
        "bf1_l_min",
        "above",
        float(DHW_MIN_SHOWER_FLOW_LPM),
    )
    assert NUMERIC_TRIGGER_MAP[TRIGGER_DHW_TANK_LOW] == (
        "tap_water_cap",
        "below",
        DHW_TANK_LOW_SHOWERS,
    )
    assert DHW_MIN_SHOWER_FLOW_LPM == 3.0
    assert DHW_TANK_LOW_SHOWERS == 2.0


def test_alarm_code_unique_id_is_not_alarm_active():
    """alarm_1_code must not resolve as the alarm_active status metric."""
    assert metric_from_unique_id("qvantum_alarm_1_code_123") is None
    assert metric_from_unique_id("qvantum_alarm_active_123") == "alarm_active"


def test_trigger_schema_accepts_known_types():
    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "dev-ha-id",
        CONF_ENTITY_ID: "binary_sensor.qvantum_wifi_connected_1",
        CONF_TYPE: "wifi_disconnected",
    }
    validated = device_trigger.TRIGGER_SCHEMA(config)
    assert validated[CONF_TYPE] == "wifi_disconnected"


def test_trigger_schema_rejects_unknown_type():
    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "dev-ha-id",
        CONF_ENTITY_ID: "binary_sensor.x",
        CONF_TYPE: "not_a_real_trigger",
    }
    with pytest.raises(vol.Invalid):
        device_trigger.TRIGGER_SCHEMA(config)


@pytest.mark.parametrize(
    ("trigger_type", "entity_id"),
    [
        (TRIGGER_DHW_SHOWER_STARTED, "sensor.qvantum_bf1_l_min_1"),
        (TRIGGER_DHW_TANK_LOW, "sensor.qvantum_tap_water_cap_1"),
        ("extra_dhw_finished", "switch.qvantum_extra_tap_water_1"),
    ],
)
def test_trigger_schema_accepts_dhw_types(trigger_type, entity_id):
    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "dev-ha-id",
        CONF_ENTITY_ID: entity_id,
        CONF_TYPE: trigger_type,
    }
    validated = device_trigger.TRIGGER_SCHEMA(config)
    assert validated[CONF_TYPE] == trigger_type


def test_condition_schema_accepts_filter_soon_due():
    config = {
        CONF_CONDITION: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "dev-ha-id",
        CONF_ENTITY_ID: "sensor.qvantum_ventilation_filter_time_left_1",
        CONF_TYPE: CONDITION_FILTER_SOON_DUE,
    }
    validated = device_condition.CONDITION_SCHEMA(config)
    assert validated[CONF_TYPE] == CONDITION_FILTER_SOON_DUE


def test_async_entries_for_status_metrics_filters_domain_and_collisions():
    hass = _hass_with_write_access(modbus=True, writable=True)
    entries = [
        _entry(
            unique_id="qvantum_wifi_connected_1",
            domain="binary_sensor",
            entry_id="e-wifi",
        ),
        _entry(
            unique_id="qvantum_compressor_blocked_sec_1",
            domain="sensor",
            entry_id="e-sec",
        ),
        _entry(
            unique_id="qvantum_ventilation_filter_time_left_1",
            domain="sensor",
            entry_id="e-filter",
        ),
        _entry(
            unique_id="qvantum_wifi_connected_1",
            domain="sensor",  # wrong domain
            entry_id="e-wifi-wrong",
        ),
        _entry(
            unique_id="qvantum_extra_tap_water_1",
            domain="switch",
            entry_id="e-extra",
        ),
        _entry(
            unique_id="qvantum_extra_tap_water_60min_1",
            domain="button",
            entry_id="e-extra-button",
        ),
        _entry(
            unique_id="qvantum_tap_water_capacity_target_1",
            domain="number",
            entry_id="e-capacity",
        ),
    ]
    registry = MagicMock()
    with (
        patch(
            "custom_components.qvantum.device_automation_helpers.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.qvantum.device_automation_helpers.er.async_entries_for_device",
            return_value=entries,
        ),
    ):
        found = async_entries_for_status_metrics(hass, "ha-device")

    assert set(found) == {
        "wifi_connected",
        "ventilation_filter_time_left",
        "extra_tap_water",
    }
    assert found["wifi_connected"].id == "e-wifi"
    assert found["ventilation_filter_time_left"].id == "e-filter"
    assert found["extra_tap_water"].id == "e-extra"


@pytest.mark.parametrize(
    ("hass_kwargs", "writable_switch"),
    [
        ({"modbus": True, "writable": True}, True),
        ({"modbus": True, "writable": False}, False),
        ({"modbus": False, "write_access_level": 20}, True),
        ({"modbus": False, "write_access_level": 10}, False),
        ({"modbus": False}, False),
    ],
)
def test_async_entries_for_status_metrics_gates_switch_on_write_access(
    hass_kwargs, writable_switch
):
    """The extra-DHW switch is only listed when its entity can change state."""
    entries = [
        _entry(
            unique_id="qvantum_extra_tap_water_1",
            domain="switch",
            entry_id="e-extra",
        ),
        _entry(unique_id="qvantum_bf1_l_min_1", domain="sensor", entry_id="e-flow"),
    ]
    hass = _hass_with_write_access(**hass_kwargs)
    registry = MagicMock()
    with (
        patch(
            "custom_components.qvantum.device_automation_helpers.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.qvantum.device_automation_helpers.er.async_entries_for_device",
            return_value=entries,
        ),
    ):
        found = async_entries_for_status_metrics(hass, "ha-device")

    assert ("extra_tap_water" in found) is writable_switch
    assert "bf1_l_min" in found  # sensor-backed metrics are never gated


@pytest.mark.asyncio
async def test_async_get_triggers_omits_extra_dhw_without_write_access():
    """Read-only installs are not offered a trigger that can never fire."""
    entries = [
        _entry(
            unique_id="qvantum_extra_tap_water_1",
            domain="switch",
            entry_id="e-extra",
        ),
        _entry(unique_id="qvantum_bf1_l_min_1", domain="sensor", entry_id="e-flow"),
    ]
    hass = _hass_with_write_access(modbus=True, writable=False)
    with (
        patch(
            "custom_components.qvantum.device_automation_helpers.er.async_get",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.qvantum.device_automation_helpers.er.async_entries_for_device",
            return_value=entries,
        ),
    ):
        triggers = await device_trigger.async_get_triggers(hass, "ha-device")

    types = {t[CONF_TYPE] for t in triggers}
    assert "extra_dhw_finished" not in types
    assert TRIGGER_DHW_SHOWER_STARTED in types


@pytest.mark.asyncio
async def test_async_get_conditions_omit_extra_dhw_without_write_access():
    """The extra-DHW condition cannot pass without write access either."""
    entries = [
        _entry(
            unique_id="qvantum_extra_tap_water_1",
            domain="switch",
            entry_id="e-extra",
        ),
        _entry(unique_id="qvantum_bf1_l_min_1", domain="sensor", entry_id="e-flow"),
    ]
    hass = _hass_with_write_access(modbus=False, write_access_level=10)
    with (
        patch(
            "custom_components.qvantum.device_automation_helpers.er.async_get",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.qvantum.device_automation_helpers.er.async_entries_for_device",
            return_value=entries,
        ),
    ):
        conditions = await device_condition.async_get_conditions(hass, "ha-device")

    types = {c[CONF_TYPE] for c in conditions}
    assert "is_extra_dhw_active" not in types
    assert CONDITION_DHW_SHOWER_ACTIVE in types


@pytest.mark.asyncio
async def test_async_get_triggers_lists_available_types():
    hass = MagicMock()
    entries = {
        "time_to_defrost": _entry(
            unique_id="qvantum_time_to_defrost_1",
            domain="binary_sensor",
            entry_id="e-defrost",
        ),
        "ventilation_filter_time_left": _entry(
            unique_id="qvantum_ventilation_filter_time_left_1",
            domain="sensor",
            entry_id="e-filter",
        ),
        "bf1_l_min": _entry(
            unique_id="qvantum_bf1_l_min_1",
            domain="sensor",
            entry_id="e-flow",
        ),
        "tap_water_cap": _entry(
            unique_id="qvantum_tap_water_cap_1",
            domain="sensor",
            entry_id="e-tank",
        ),
        "extra_tap_water": _entry(
            unique_id="qvantum_extra_tap_water_1",
            domain="switch",
            entry_id="e-extra",
        ),
    }
    with patch(
        "custom_components.qvantum.device_trigger.async_entries_for_status_metrics",
        return_value=entries,
    ):
        triggers = await device_trigger.async_get_triggers(hass, "ha-device")

    types = {t[CONF_TYPE] for t in triggers}
    assert types == {
        "defrosting",
        TRIGGER_FILTER_SOON_DUE,
        TRIGGER_DHW_SHOWER_STARTED,
        TRIGGER_DHW_TANK_LOW,
        "extra_dhw_finished",
    }
    assert all(t[CONF_PLATFORM] == "device" for t in triggers)
    assert all(t[CONF_DOMAIN] == DOMAIN for t in triggers)
    by_type = {t[CONF_TYPE]: t for t in triggers}
    assert by_type[TRIGGER_DHW_SHOWER_STARTED][CONF_ENTITY_ID] == "e-flow"
    assert by_type[TRIGGER_DHW_TANK_LOW][CONF_ENTITY_ID] == "e-tank"
    assert by_type["extra_dhw_finished"][CONF_ENTITY_ID] == "e-extra"


@pytest.mark.asyncio
async def test_async_get_triggers_skips_dhw_without_entities():
    """DHW triggers are only listed when their status entity exists."""
    hass = MagicMock()
    entries = {
        "bf1_l_min": _entry(
            unique_id="qvantum_bf1_l_min_1",
            domain="sensor",
            entry_id="e-flow",
        ),
    }
    with patch(
        "custom_components.qvantum.device_trigger.async_entries_for_status_metrics",
        return_value=entries,
    ):
        triggers = await device_trigger.async_get_triggers(hass, "ha-device")

    types = {t[CONF_TYPE] for t in triggers}
    assert types == {TRIGGER_DHW_SHOWER_STARTED}


@pytest.mark.asyncio
async def test_async_get_conditions_lists_available_types():
    hass = MagicMock()
    entries = {
        "compressor_blocked": _entry(
            unique_id="qvantum_compressor_blocked_1",
            domain="binary_sensor",
            entry_id="e-cpr",
        ),
        "wifi_connected": _entry(
            unique_id="qvantum_wifi_connected_1",
            domain="binary_sensor",
            entry_id="e-wifi",
        ),
        "bf1_l_min": _entry(
            unique_id="qvantum_bf1_l_min_1",
            domain="sensor",
            entry_id="e-flow",
        ),
        "tap_water_cap": _entry(
            unique_id="qvantum_tap_water_cap_1",
            domain="sensor",
            entry_id="e-tank",
        ),
        "extra_tap_water": _entry(
            unique_id="qvantum_extra_tap_water_1",
            domain="switch",
            entry_id="e-extra",
        ),
    }
    with patch(
        "custom_components.qvantum.device_condition.async_entries_for_status_metrics",
        return_value=entries,
    ):
        conditions = await device_condition.async_get_conditions(hass, "ha-device")

    types = {c[CONF_TYPE] for c in conditions}
    assert types == {
        "is_compressor_blocked",
        "is_wifi_disconnected",
        CONDITION_DHW_SHOWER_ACTIVE,
        CONDITION_DHW_TANK_LOW,
        "is_extra_dhw_active",
    }
    assert all(c[CONF_CONDITION] == "device" for c in conditions)
    by_type = {c[CONF_TYPE]: c for c in conditions}
    assert by_type[CONDITION_DHW_SHOWER_ACTIVE][CONF_ENTITY_ID] == "e-flow"
    assert by_type[CONDITION_DHW_TANK_LOW][CONF_ENTITY_ID] == "e-tank"
    assert by_type["is_extra_dhw_active"][CONF_ENTITY_ID] == "e-extra"


@pytest.mark.asyncio
async def test_async_get_conditions_skip_dhw_without_entities():
    """DHW conditions are only listed when their status entity exists."""
    hass = MagicMock()
    entries = {
        "extra_tap_water": _entry(
            unique_id="qvantum_extra_tap_water_1",
            domain="switch",
            entry_id="e-extra",
        ),
    }
    with patch(
        "custom_components.qvantum.device_condition.async_entries_for_status_metrics",
        return_value=entries,
    ):
        conditions = await device_condition.async_get_conditions(hass, "ha-device")

    types = {c[CONF_TYPE] for c in conditions}
    assert types == {"is_extra_dhw_active"}


@pytest.mark.asyncio
async def test_async_attach_trigger_binary_uses_state_trigger():
    hass = MagicMock()
    action = AsyncMock()
    trigger_info = MagicMock()
    unsub = MagicMock()

    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "ha-device",
        CONF_ENTITY_ID: "binary_sensor.qvantum_time_to_defrost_1",
        CONF_TYPE: "defrosting",
    }

    with (
        patch(
            "custom_components.qvantum.device_trigger.state_trigger.async_validate_trigger_config",
            new_callable=AsyncMock,
            side_effect=lambda hass, cfg: cfg,
        ) as validate,
        patch(
            "custom_components.qvantum.device_trigger.state_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=unsub,
        ) as attach,
    ):
        result = await device_trigger.async_attach_trigger(
            hass, config, action, trigger_info
        )

    assert result is unsub
    validate.assert_awaited_once()
    attach.assert_awaited_once()
    state_config = attach.await_args.args[1]
    assert state_config["to"] == "on"
    assert state_config["entity_id"] == config[CONF_ENTITY_ID]
    assert attach.await_args.kwargs["platform_type"] == "device"


@pytest.mark.asyncio
async def test_async_attach_trigger_filter_uses_numeric_state():
    hass = MagicMock()
    action = AsyncMock()
    trigger_info = MagicMock()
    unsub = MagicMock()

    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "ha-device",
        CONF_ENTITY_ID: "sensor.qvantum_ventilation_filter_time_left_1",
        CONF_TYPE: TRIGGER_FILTER_SOON_DUE,
    }

    with (
        patch(
            "custom_components.qvantum.device_trigger.numeric_state_trigger.async_validate_trigger_config",
            new_callable=AsyncMock,
            side_effect=lambda hass, cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_trigger.numeric_state_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=unsub,
        ) as attach,
    ):
        result = await device_trigger.async_attach_trigger(
            hass, config, action, trigger_info
        )

    assert result is unsub
    numeric_config = attach.await_args.args[1]
    assert numeric_config["below"] == float(FILTER_SOON_DUE_HOURS)
    assert attach.await_args.kwargs["platform_type"] == "device"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trigger_type", "entity_id", "bound", "threshold"),
    [
        (
            TRIGGER_DHW_SHOWER_STARTED,
            "sensor.qvantum_bf1_l_min_1",
            "above",
            float(DHW_MIN_SHOWER_FLOW_LPM),
        ),
        (
            TRIGGER_DHW_TANK_LOW,
            "sensor.qvantum_tap_water_cap_1",
            "below",
            DHW_TANK_LOW_SHOWERS,
        ),
    ],
)
async def test_async_attach_trigger_dhw_uses_numeric_state(
    trigger_type, entity_id, bound, threshold
):
    hass = MagicMock()
    action = AsyncMock()
    trigger_info = MagicMock()
    unsub = MagicMock()

    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "ha-device",
        CONF_ENTITY_ID: entity_id,
        CONF_TYPE: trigger_type,
    }

    with (
        patch(
            "custom_components.qvantum.device_trigger.numeric_state_trigger.async_validate_trigger_config",
            new_callable=AsyncMock,
            side_effect=lambda hass, cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_trigger.numeric_state_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=unsub,
        ) as attach,
    ):
        result = await device_trigger.async_attach_trigger(
            hass, config, action, trigger_info
        )

    assert result is unsub
    numeric_config = attach.await_args.args[1]
    assert numeric_config[bound] == threshold
    assert numeric_config["entity_id"] == entity_id
    assert attach.await_args.kwargs["platform_type"] == "device"


@pytest.mark.asyncio
async def test_async_attach_trigger_extra_dhw_finished_uses_off_state():
    hass = MagicMock()
    action = AsyncMock()
    trigger_info = MagicMock()
    unsub = MagicMock()

    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "ha-device",
        CONF_ENTITY_ID: "switch.qvantum_extra_tap_water_1",
        CONF_TYPE: "extra_dhw_finished",
    }

    with (
        patch(
            "custom_components.qvantum.device_trigger.state_trigger.async_validate_trigger_config",
            new_callable=AsyncMock,
            side_effect=lambda hass, cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_trigger.state_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=unsub,
        ) as attach,
    ):
        result = await device_trigger.async_attach_trigger(
            hass, config, action, trigger_info
        )

    assert result is unsub
    state_config = attach.await_args.args[1]
    assert state_config["to"] == "off"
    assert state_config["entity_id"] == config[CONF_ENTITY_ID]


def test_async_condition_from_config_binary_builds_state_checker():
    hass = MagicMock()
    checker = MagicMock()
    config = {
        CONF_CONDITION: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "ha-device",
        CONF_ENTITY_ID: "binary_sensor.qvantum_wifi_connected_1",
        CONF_TYPE: "is_wifi_disconnected",
    }

    with (
        patch(
            "custom_components.qvantum.device_condition.cv.STATE_CONDITION_SCHEMA",
            side_effect=lambda cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_condition.condition.state_validate_config",
            side_effect=lambda hass, cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_condition.condition.state_from_config",
            return_value=checker,
        ) as from_config,
    ):
        result = device_condition.async_condition_from_config(hass, config)

    assert result is checker
    state_config = from_config.call_args.args[0]
    assert state_config["state"] == "off"


def test_async_condition_from_config_filter_builds_numeric_checker():
    hass = MagicMock()
    checker = MagicMock()
    config = {
        CONF_CONDITION: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "ha-device",
        CONF_ENTITY_ID: "sensor.qvantum_ventilation_filter_time_left_1",
        CONF_TYPE: CONDITION_FILTER_SOON_DUE,
    }

    with (
        patch(
            "custom_components.qvantum.device_condition.cv.NUMERIC_STATE_CONDITION_SCHEMA",
            side_effect=lambda cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_condition.condition.numeric_state_validate_config",
            side_effect=lambda hass, cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_condition.condition.async_numeric_state_from_config",
            return_value=checker,
        ) as from_config,
    ):
        result = device_condition.async_condition_from_config(hass, config)

    assert result is checker
    numeric_config = from_config.call_args.args[0]
    assert numeric_config["below"] == float(FILTER_SOON_DUE_HOURS)


@pytest.mark.parametrize(
    ("condition_type", "entity_id", "bound", "threshold"),
    [
        (
            CONDITION_DHW_SHOWER_ACTIVE,
            "sensor.qvantum_bf1_l_min_1",
            "above",
            float(DHW_MIN_SHOWER_FLOW_LPM),
        ),
        (
            CONDITION_DHW_TANK_LOW,
            "sensor.qvantum_tap_water_cap_1",
            "below",
            DHW_TANK_LOW_SHOWERS,
        ),
    ],
)
def test_async_condition_from_config_dhw_builds_numeric_checker(
    condition_type, entity_id, bound, threshold
):
    hass = MagicMock()
    checker = MagicMock()
    config = {
        CONF_CONDITION: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "ha-device",
        CONF_ENTITY_ID: entity_id,
        CONF_TYPE: condition_type,
    }

    with (
        patch(
            "custom_components.qvantum.device_condition.cv.NUMERIC_STATE_CONDITION_SCHEMA",
            side_effect=lambda cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_condition.condition.numeric_state_validate_config",
            side_effect=lambda hass, cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_condition.condition.async_numeric_state_from_config",
            return_value=checker,
        ) as from_config,
    ):
        result = device_condition.async_condition_from_config(hass, config)

    assert result is checker
    numeric_config = from_config.call_args.args[0]
    assert numeric_config[bound] == threshold
    assert numeric_config["entity_id"] == entity_id


def test_async_condition_from_config_extra_dhw_active_builds_state_checker():
    hass = MagicMock()
    checker = MagicMock()
    config = {
        CONF_CONDITION: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "ha-device",
        CONF_ENTITY_ID: "switch.qvantum_extra_tap_water_1",
        CONF_TYPE: "is_extra_dhw_active",
    }

    with (
        patch(
            "custom_components.qvantum.device_condition.cv.STATE_CONDITION_SCHEMA",
            side_effect=lambda cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_condition.condition.state_validate_config",
            side_effect=lambda hass, cfg: cfg,
        ),
        patch(
            "custom_components.qvantum.device_condition.condition.state_from_config",
            return_value=checker,
        ) as from_config,
    ):
        result = device_condition.async_condition_from_config(hass, config)

    assert result is checker
    state_config = from_config.call_args.args[0]
    assert state_config["state"] == "on"


def test_trigger_and_condition_maps_stay_aligned():
    """Every trigger has a matching is_* condition on the same metric."""
    trigger_metrics = {metric for metric, _ in BINARY_TRIGGER_MAP.values()}
    condition_metrics = {metric for metric, _ in BINARY_CONDITION_MAP.values()}
    assert trigger_metrics == condition_metrics

    assert {metric for metric, _, _ in NUMERIC_TRIGGER_MAP.values()} == {
        metric for metric, _, _ in NUMERIC_CONDITION_MAP.values()
    }
    assert set(NUMERIC_TRIGGER_MAP) == {
        TRIGGER_FILTER_SOON_DUE,
        TRIGGER_DHW_SHOWER_STARTED,
        TRIGGER_DHW_TANK_LOW,
    }
    assert set(NUMERIC_CONDITION_MAP) == {
        CONDITION_FILTER_SOON_DUE,
        CONDITION_DHW_SHOWER_ACTIVE,
        CONDITION_DHW_TANK_LOW,
    }
