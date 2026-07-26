import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .api import APIAuthError, APIConnectionError, APIRateLimitError, QvantumAPI

_LOGGER = logging.getLogger(__name__)

EXTRA_TAP_WATER_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): vol.All(vol.Coerce(str), vol.Length(min=1)),
        vol.Required("minutes", default=120): vol.All(vol.Coerce(int), vol.Range(min=0, max=480)),
    }
)


def _resolve_api(hass: HomeAssistant, device_id: int | None = None) -> QvantumAPI:
    """Resolve the API for the requested device id.

    When a service call includes a device id, prefer the config entry whose
    coordinator device id matches that value. This avoids sending commands to the
    wrong account or integration instance when multiple Qvantum entries are loaded.
    Falls back to the legacy ``hass.data[DOMAIN]`` behavior for unit tests and
    any setup that still exposes the API there.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if device_id is not None:
        for entry in entries:
            runtime_data = getattr(entry, "runtime_data", None)
            coordinator = getattr(runtime_data, "coordinator", None)
            if coordinator is None:
                continue
            coordinator_device_id = getattr(coordinator, "device_id", None)
            if coordinator_device_id is not None and str(coordinator_device_id) == str(device_id):
                api = getattr(runtime_data, "api", None)
                if api is not None:
                    return api

    for entry in entries:
        runtime_data = getattr(entry, "runtime_data", None)
        api = getattr(runtime_data, "api", None)
        if api is not None:
            return api

    api = hass.data.get(DOMAIN)
    if api is not None:
        return api

    raise HomeAssistantError("Qvantum integration is not loaded")


async def async_setup_services(hass: HomeAssistant):
    _LOGGER.debug("Setting up services")

    async def extra_hot_water(service_call: ServiceCall) -> Any:
        data = service_call.data
        device_id = data["device_id"]
        try:
            api = _resolve_api(service_call.hass, device_id)
        except HomeAssistantError as err:
            _LOGGER.error("Cannot handle extra tap water request: %s", err)
            return {"qvantum": {"exception": "not_loaded", "details": str(err)}}

        minutes = data["minutes"]
        try:
            response = await api.set_extra_tap_water(device_id, minutes)
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
