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

EXTRA_TAP_WATER_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): int,
        vol.Required("minutes", default=120): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=480)
        ),
    }
)


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
