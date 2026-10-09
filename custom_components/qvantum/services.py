import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
import homeassistant.helpers.config_validation as cv

from .client.exceptions import (
    APIAuthError,
    APIConnectionError,
    APIRateLimitError,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _normalize_device_id(value: Any) -> str:
    """Return a device id string without mangling Modbus serials.

    Cloud device ids are numeric, but Modbus ids are serials that may start
    with zeros, so the value must never be round-tripped through ``int``.
    """
    if isinstance(value, bool) or value is None:
        raise vol.Invalid("device_id must be a string or integer")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    raise vol.Invalid("device_id must be a non-empty string or integer")


EXTRA_TAP_WATER_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): _normalize_device_id,
        vol.Required("minutes", default=120): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=480)
        ),
    }
)

SET_CURVE_CONTROL_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In(["shadow", "active"]),
    }
)

SET_CURVE_TERMS_SCHEMA = vol.Schema(
    {
        vol.Optional("cop_feedback"): cv.boolean,
    }
)


def _find_curve_coordinator(hass: HomeAssistant):
    """Return the loaded adaptive-curve coordinator, if any (Modbus only)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        candidate = getattr(runtime, "curve_coordinator", None)
        if candidate is not None:
            return candidate
    return None


async def async_setup_services(hass: HomeAssistant):
    _LOGGER.debug("Setting up services")

    async def extra_hot_water(service_call: ServiceCall) -> Any:
        data = service_call.data
        coordinator = None
        for entry in service_call.hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if runtime is not None:
                coordinator = runtime.coordinator
                break
        if coordinator is None:
            return {
                "qvantum": {
                    "exception": "unknown_error",
                    "details": "Qvantum is not set up",
                }
            }

        device_id = data["device_id"]
        minutes = data["minutes"]
        try:
            response = await coordinator.async_set_extra_tap_water(device_id, minutes)
            return {"qvantum": [response]}
        except APIAuthError as err:
            _LOGGER.error(
                "Authentication failed while handling extra tap water request: %s", err
            )
            return {
                "qvantum": {"exception": "authentication_failed", "details": str(err)}
            }
        except APIConnectionError as err:
            _LOGGER.error(
                "Connection failed while handling extra tap water request: %s", err
            )
            return {"qvantum": {"exception": "connection_failed", "details": str(err)}}
        except APIRateLimitError as err:
            _LOGGER.error(
                "Rate limit exceeded while handling extra tap water request: %s", err
            )
            return {
                "qvantum": {"exception": "rate_limit_exceeded", "details": str(err)}
            }
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error while handling extra tap water request: %s", err
            )
            return {"qvantum": {"exception": "unknown_error", "details": str(err)}}

    hass.services.async_register(
        domain=DOMAIN,
        service="extra_hot_water",
        service_func=extra_hot_water,
        schema=EXTRA_TAP_WATER_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def set_curve_control(service_call: ServiceCall) -> Any:
        """Shadow or active custom heating-curve control (Modbus only)."""
        data = service_call.data
        mode = data["mode"]
        curve_coordinator = _find_curve_coordinator(service_call.hass)
        if curve_coordinator is None:
            return {
                "qvantum": {
                    "exception": "unknown_error",
                    "details": "Adaptive curve control is only available in Modbus mode",
                }
            }
        try:
            await curve_coordinator.async_set_control_mode(mode)
        except Exception as err:
            _LOGGER.error("Failed to set curve control mode %s: %s", mode, err)
            return {"qvantum": {"exception": "unknown_error", "details": str(err)}}
        return {"qvantum": {"mode": mode, "active": curve_coordinator.active}}

    hass.services.async_register(
        domain=DOMAIN,
        service="set_curve_control",
        service_func=set_curve_control,
        schema=SET_CURVE_CONTROL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def set_curve_terms(service_call: ServiceCall) -> Any:
        """Enable or disable the optional adaptive-curve control terms."""
        data = service_call.data
        curve_coordinator = _find_curve_coordinator(service_call.hass)
        if curve_coordinator is None:
            return {
                "qvantum": {
                    "exception": "unknown_error",
                    "details": "Adaptive curve control is only available in Modbus mode",
                }
            }
        try:
            await curve_coordinator.async_set_terms(
                cop_feedback=data.get("cop_feedback"),
            )
        except Exception as err:
            _LOGGER.error("Failed to set curve terms %s: %s", data, err)
            return {"qvantum": {"exception": "unknown_error", "details": str(err)}}
        return {"qvantum": {"terms": curve_coordinator.terms}}

    hass.services.async_register(
        domain=DOMAIN,
        service="set_curve_terms",
        service_func=set_curve_terms,
        schema=SET_CURVE_TERMS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
