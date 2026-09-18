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

from custom_components.qvantum.const import DOMAIN, FILTER_SOON_DUE_HOURS
from custom_components.qvantum.device_automation_helpers import (
    BINARY_CONDITION_MAP,
    BINARY_TRIGGER_MAP,
    CONDITION_FILTER_SOON_DUE,
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
    )


def test_metric_from_unique_id_extracts_status_metrics():
    assert metric_from_unique_id("qvantum_wifi_connected_123") == "wifi_connected"
    assert metric_from_unique_id("qvantum_time_to_defrost_pump-1") == "time_to_defrost"
    assert (
        metric_from_unique_id("qvantum_ventilation_filter_time_left_dev")
        == "ventilation_filter_time_left"
    )


def test_metric_from_unique_id_ignores_prefix_collisions():
    """compressor_blocked_sec must not resolve as compressor_blocked."""
    assert metric_from_unique_id("qvantum_compressor_blocked_sec_123") is None
    assert metric_from_unique_id("qvantum_compressor_blocked_123") == "compressor_blocked"


def test_metric_from_unique_id_rejects_unknown():
    assert metric_from_unique_id(None) is None
    assert metric_from_unique_id("other_wifi_connected_1") is None
    assert metric_from_unique_id("qvantum_bt1_123") is None


def test_filter_below_hours_matches_const():
    assert filter_below_hours() == float(FILTER_SOON_DUE_HOURS)
    assert FILTER_SOON_DUE_HOURS == 48


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
    hass = MagicMock()
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

    assert set(found) == {"wifi_connected", "ventilation_filter_time_left"}
    assert found["wifi_connected"].id == "e-wifi"
    assert found["ventilation_filter_time_left"].id == "e-filter"


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
    }
    with patch(
        "custom_components.qvantum.device_trigger.async_entries_for_status_metrics",
        return_value=entries,
    ):
        triggers = await device_trigger.async_get_triggers(hass, "ha-device")

    types = {t[CONF_TYPE] for t in triggers}
    assert types == {"defrosting", TRIGGER_FILTER_SOON_DUE}
    assert all(t[CONF_PLATFORM] == "device" for t in triggers)
    assert all(t[CONF_DOMAIN] == DOMAIN for t in triggers)


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
    }
    with patch(
        "custom_components.qvantum.device_condition.async_entries_for_status_metrics",
        return_value=entries,
    ):
        conditions = await device_condition.async_get_conditions(hass, "ha-device")

    types = {c[CONF_TYPE] for c in conditions}
    assert types == {"is_compressor_blocked", "is_wifi_disconnected"}
    assert all(c[CONF_CONDITION] == "device" for c in conditions)


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


def test_trigger_and_condition_maps_stay_aligned():
    """Every binary trigger has a matching is_* condition on the same metric."""
    trigger_metrics = {metric for metric, _ in BINARY_TRIGGER_MAP.values()}
    condition_metrics = {metric for metric, _ in BINARY_CONDITION_MAP.values()}
    assert trigger_metrics == condition_metrics
