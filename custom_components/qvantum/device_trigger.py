"""Device triggers for Qvantum heat pump status bits."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import (
    numeric_state as numeric_state_trigger,
    state as state_trigger,
)
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .device_automation_helpers import (
    BINARY_TRIGGER_MAP,
    NUMERIC_TRIGGER_MAP,
    async_entries_for_status_metrics,
)

TRIGGER_TYPES = set(BINARY_TRIGGER_MAP) | set(NUMERIC_TRIGGER_MAP)

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required(CONF_ENTITY_ID): cv.entity_id_or_uuid,
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Return device triggers for available status entities."""
    entries = async_entries_for_status_metrics(hass, device_id)
    triggers: list[dict[str, str]] = []

    for trigger_type, (metric, _to_state) in BINARY_TRIGGER_MAP.items():
        entry = entries.get(metric)
        if entry is None:
            continue
        triggers.append(
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_ENTITY_ID: entry.id,
                CONF_TYPE: trigger_type,
            }
        )

    for trigger_type, (metric, _bound, _threshold) in NUMERIC_TRIGGER_MAP.items():
        entry = entries.get(metric)
        if entry is None:
            continue
        triggers.append(
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_ENTITY_ID: entry.id,
                CONF_TYPE: trigger_type,
            }
        )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a state or numeric-state trigger for the configured type."""
    trigger_type = config[CONF_TYPE]
    entity_id = config[CONF_ENTITY_ID]

    if trigger_type in NUMERIC_TRIGGER_MAP:
        _metric, bound, threshold = NUMERIC_TRIGGER_MAP[trigger_type]
        numeric_config: dict[str, Any] = {
            numeric_state_trigger.CONF_PLATFORM: "numeric_state",
            numeric_state_trigger.CONF_ENTITY_ID: entity_id,
            bound: threshold,
        }
        numeric_config = await numeric_state_trigger.async_validate_trigger_config(
            hass, numeric_config
        )
        return await numeric_state_trigger.async_attach_trigger(
            hass, numeric_config, action, trigger_info, platform_type="device"
        )

    _metric, to_state = BINARY_TRIGGER_MAP[trigger_type]
    state_config: dict[str, Any] = {
        state_trigger.CONF_PLATFORM: "state",
        state_trigger.CONF_ENTITY_ID: entity_id,
        state_trigger.CONF_TO: to_state,
    }
    state_config = await state_trigger.async_validate_trigger_config(hass, state_config)
    return await state_trigger.async_attach_trigger(
        hass, state_config, action, trigger_info, platform_type="device"
    )
