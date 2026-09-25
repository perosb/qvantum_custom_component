"""Device conditions for Qvantum heat pump status bits."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.const import (
    CONF_CONDITION,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_STATE,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    condition,
    config_validation as cv,
)
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .device_automation_helpers import (
    BINARY_CONDITION_MAP,
    NUMERIC_CONDITION_MAP,
    async_entries_for_status_metrics,
)

CONDITION_TYPES = set(BINARY_CONDITION_MAP) | set(NUMERIC_CONDITION_MAP)

CONDITION_SCHEMA = cv.DEVICE_CONDITION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(CONDITION_TYPES),
        vol.Required(CONF_ENTITY_ID): cv.entity_id_or_uuid,
    }
)


async def async_get_conditions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Return device conditions for available status entities."""
    entries = async_entries_for_status_metrics(hass, device_id)
    conditions: list[dict[str, str]] = []

    for condition_type, (metric, _state) in BINARY_CONDITION_MAP.items():
        entry = entries.get(metric)
        if entry is None:
            continue
        conditions.append(
            {
                CONF_CONDITION: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_ENTITY_ID: entry.id,
                CONF_TYPE: condition_type,
            }
        )

    for condition_type, (metric, _bound, _threshold) in NUMERIC_CONDITION_MAP.items():
        entry = entries.get(metric)
        if entry is None:
            continue
        conditions.append(
            {
                CONF_CONDITION: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_ENTITY_ID: entry.id,
                CONF_TYPE: condition_type,
            }
        )

    return conditions


@callback
def async_condition_from_config(
    hass: HomeAssistant, config: ConfigType
) -> condition.ConditionCheckerType:
    """Create a condition checker for the configured device condition."""
    condition_type = config[CONF_TYPE]
    entity_id = config[CONF_ENTITY_ID]

    if condition_type in NUMERIC_CONDITION_MAP:
        _metric, bound, threshold = NUMERIC_CONDITION_MAP[condition_type]
        numeric_config: dict[str, Any] = {
            CONF_CONDITION: "numeric_state",
            CONF_ENTITY_ID: entity_id,
            bound: threshold,
        }
        numeric_config = cv.NUMERIC_STATE_CONDITION_SCHEMA(numeric_config)
        numeric_config = condition.numeric_state_validate_config(hass, numeric_config)
        return condition.async_numeric_state_from_config(numeric_config)

    _metric, expected = BINARY_CONDITION_MAP[condition_type]
    state_config: dict[str, Any] = {
        CONF_CONDITION: "state",
        CONF_ENTITY_ID: entity_id,
        CONF_STATE: expected,
    }
    state_config = cv.STATE_CONDITION_SCHEMA(state_config)
    state_config = condition.state_validate_config(hass, state_config)
    return condition.state_from_config(state_config)
