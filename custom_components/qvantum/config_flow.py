"""Config flow for Qvantum integration."""

from __future__ import annotations

import inspect
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.const import __version__ as ha_version
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .api import QvantumAPI, APIAuthError, APIConnectionError
from .const import (
    DEFAULT_MODBUS_HOST,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_SCAN_INTERVAL,
    DEFAULT_MODBUS_UNIT_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_MODBUS_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    VERSION,
    CONFIG_VERSION,
    CONF_MODBUS_HOST,
    CONF_MODBUS_PORT,
    CONF_MODBUS_SCAN_INTERVAL,
    CONF_MODBUS_TCP,
    CONF_MODBUS_UNIT_ID,
    CONF_MODBUS_WRITE,
)

_LOGGER = logging.getLogger(__name__)

STEP_CLOUD_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_MODBUS_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MODBUS_HOST, default=DEFAULT_MODBUS_HOST): str,
        vol.Optional(CONF_MODBUS_PORT, default=DEFAULT_MODBUS_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Optional(CONF_MODBUS_UNIT_ID, default=DEFAULT_MODBUS_UNIT_ID): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
        vol.Optional(
            CONF_MODBUS_SCAN_INTERVAL, default=DEFAULT_MODBUS_SCAN_INTERVAL
        ): vol.All(vol.Coerce(int), vol.Clamp(min=MIN_MODBUS_SCAN_INTERVAL)),
    }
)


def _normalize_modbus_scan_interval(value: Any) -> int:
    """Coerce Modbus scan interval to int and enforce the configured minimum."""
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MODBUS_SCAN_INTERVAL
    return max(interval, MIN_MODBUS_SCAN_INTERVAL)


def _normalize_modbus_host(value: Any) -> str:
    """Strip host whitespace and fall back to the default when empty."""
    host = str(value or "").strip()
    return host or DEFAULT_MODBUS_HOST


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate cloud credentials and return a title."""
    user_agent = f"Home Assistant/{ha_version} Qvantum/{VERSION}"
    api = QvantumAPI(data[CONF_USERNAME], data[CONF_PASSWORD], user_agent=user_agent)
    try:
        await api.authenticate()
        device = await api.get_primary_device()
        if isinstance(device, dict):
            serial = device.get("serial")
            if serial is not None:
                serial = str(serial).strip() or None
            vendor = str(device.get("vendor") or "").strip()
            model = str(device.get("model") or "").strip()
            name = " ".join(part for part in (vendor, model) if part) or "Qvantum"
            title = f"{name} ({serial})" if serial else name
        else:
            serial = None
            title = "Qvantum"
        return {"title": title, "serial": serial}
    except APIAuthError as err:
        raise InvalidAuth from err
    except APIConnectionError as err:
        raise CannotConnect from err
    finally:
        if hasattr(api, "close"):
            try:
                close_result = api.close()
                if inspect.isawaitable(close_result):
                    await close_result
            except Exception:
                _LOGGER.debug(
                    "Failed to close Qvantum API session in config flow validation",
                    exc_info=True,
                )


async def validate_modbus(
    hass: HomeAssistant, host: str, port: int, unit_id: int
) -> dict[str, Any]:
    """Probe the heat pump over Modbus TCP and return serial identity."""
    from homeassistant.components.modbus import async_get_temporary_unit
    from modbus_connection import ModbusTcpParams

    from .modbus_device import IdentityProbeError, async_probe_identity

    try:
        async with async_get_temporary_unit(
            hass, ModbusTcpParams(host=host, port=port), unit_id
        ) as unit:
            serial, sw_version = await async_probe_identity(unit)
    except IdentityProbeError as err:
        raise CannotConnect from err
    except HomeAssistantError as err:
        raise CannotConnect from err
    except Exception as err:
        _LOGGER.debug("Modbus probe failed", exc_info=True)
        raise CannotConnect from err
    return {
        "title": f"Qvantum ({serial})",
        "serial": serial,
        "sw_version": sw_version,
    }


class QvantumConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Qvantum Integration."""

    VERSION = CONFIG_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return QvantumOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose cloud HTTP or local Modbus."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["cloud", "modbus"],
        )

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up using a Qvantum account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                unique_id = info.get("serial") or info["title"]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_MODBUS_TCP: False,
                        CONF_MODBUS_WRITE: False,
                    },
                    options={
                        CONF_MODBUS_TCP: False,
                        CONF_MODBUS_WRITE: False,
                    },
                )
        return self.async_show_form(
            step_id="cloud",
            data_schema=STEP_CLOUD_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up using local Modbus TCP."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalize_modbus_host(user_input.get(CONF_MODBUS_HOST))
            port = int(user_input.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT))
            unit_id = int(user_input.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID))
            interval = _normalize_modbus_scan_interval(
                user_input.get(CONF_MODBUS_SCAN_INTERVAL, DEFAULT_MODBUS_SCAN_INTERVAL)
            )
            try:
                info = await validate_modbus(self.hass, host, port, unit_id)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["serial"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_MODBUS_TCP: True,
                        CONF_MODBUS_WRITE: False,
                        CONF_MODBUS_HOST: host,
                        CONF_MODBUS_PORT: port,
                        CONF_MODBUS_UNIT_ID: unit_id,
                    },
                    options={
                        CONF_MODBUS_TCP: True,
                        CONF_MODBUS_WRITE: False,
                        CONF_MODBUS_HOST: host,
                        CONF_MODBUS_PORT: port,
                        CONF_MODBUS_UNIT_ID: unit_id,
                        CONF_MODBUS_SCAN_INTERVAL: interval,
                    },
                )
        return self.async_show_form(
            step_id="modbus",
            data_schema=STEP_MODBUS_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose cloud or Modbus when reconfiguring."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["reconfigure_cloud", "reconfigure_modbus"],
        )

    def _reconfigure_entry(self) -> ConfigEntry:
        return self.hass.config_entries.async_get_entry(self.context["entry_id"])

    async def async_step_reconfigure_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure using a Qvantum account."""
        errors: dict[str, str] = {}
        config_entry = self._reconfigure_entry()
        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    config_entry,
                    unique_id=config_entry.unique_id,
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_MODBUS_TCP: False,
                        CONF_MODBUS_WRITE: False,
                    },
                    options={
                        CONF_MODBUS_TCP: False,
                        CONF_MODBUS_WRITE: False,
                        CONF_SCAN_INTERVAL: config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    },
                    reason="reconfigure_successful",
                )
        return self.async_show_form(
            step_id="reconfigure_cloud",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=config_entry.data.get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure using local Modbus TCP."""
        errors: dict[str, str] = {}
        config_entry = self._reconfigure_entry()
        if user_input is not None:
            host = _normalize_modbus_host(user_input.get(CONF_MODBUS_HOST))
            port = int(user_input.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT))
            unit_id = int(user_input.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID))
            interval = _normalize_modbus_scan_interval(
                user_input.get(CONF_MODBUS_SCAN_INTERVAL, DEFAULT_MODBUS_SCAN_INTERVAL)
            )
            try:
                await validate_modbus(self.hass, host, port, unit_id)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    config_entry,
                    unique_id=config_entry.unique_id,
                    data={
                        CONF_MODBUS_TCP: True,
                        CONF_MODBUS_WRITE: False,
                        CONF_MODBUS_HOST: host,
                        CONF_MODBUS_PORT: port,
                        CONF_MODBUS_UNIT_ID: unit_id,
                    },
                    options={
                        CONF_MODBUS_TCP: True,
                        CONF_MODBUS_WRITE: False,
                        CONF_MODBUS_HOST: host,
                        CONF_MODBUS_PORT: port,
                        CONF_MODBUS_UNIT_ID: unit_id,
                        CONF_MODBUS_SCAN_INTERVAL: interval,
                    },
                    reason="reconfigure_successful",
                )
        return self.async_show_form(
            step_id="reconfigure_modbus",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODBUS_HOST,
                        default=config_entry.options.get(
                            CONF_MODBUS_HOST,
                            config_entry.data.get(CONF_MODBUS_HOST, DEFAULT_MODBUS_HOST),
                        ),
                    ): str,
                    vol.Optional(
                        CONF_MODBUS_PORT,
                        default=config_entry.options.get(
                            CONF_MODBUS_PORT,
                            config_entry.data.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT),
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                    vol.Optional(
                        CONF_MODBUS_UNIT_ID,
                        default=config_entry.options.get(
                            CONF_MODBUS_UNIT_ID,
                            config_entry.data.get(
                                CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID
                            ),
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
                    vol.Optional(
                        CONF_MODBUS_SCAN_INTERVAL,
                        default=_normalize_modbus_scan_interval(
                            config_entry.options.get(
                                CONF_MODBUS_SCAN_INTERVAL,
                                DEFAULT_MODBUS_SCAN_INTERVAL,
                            )
                        ),
                    ): vol.All(
                        vol.Coerce(int), vol.Clamp(min=MIN_MODBUS_SCAN_INTERVAL)
                    ),
                }
            ),
            errors=errors,
        )


class QvantumOptionsFlowHandler(OptionsFlow):
    """Handles the options flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        super().__init__()
        self.options = dict(config_entry.options)
        self._config_entry = config_entry

    def _source_entry(self) -> ConfigEntry:
        return self._config_entry

    def _modbus_enabled(self) -> bool:
        entry = self._source_entry()
        return bool(
            self.options.get(
                CONF_MODBUS_TCP,
                entry.data.get(CONF_MODBUS_TCP, False),
            )
        )

    async def async_step_init(self, user_input=None):
        """Handle options flow for the current connection mode only."""
        if user_input is not None:
            if self._modbus_enabled():
                normalized = {
                    CONF_MODBUS_TCP: True,
                    CONF_MODBUS_WRITE: False,
                    CONF_MODBUS_HOST: _normalize_modbus_host(
                        user_input.get(CONF_MODBUS_HOST, DEFAULT_MODBUS_HOST)
                    ),
                    CONF_MODBUS_PORT: int(
                        user_input.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)
                    ),
                    CONF_MODBUS_UNIT_ID: int(
                        user_input.get(CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID)
                    ),
                    CONF_MODBUS_SCAN_INTERVAL: _normalize_modbus_scan_interval(
                        user_input.get(CONF_MODBUS_SCAN_INTERVAL)
                    ),
                }
            else:
                normalized = {
                    CONF_MODBUS_TCP: False,
                    CONF_MODBUS_WRITE: False,
                    CONF_SCAN_INTERVAL: user_input.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                }
            return self.async_create_entry(title="", data={**self.options, **normalized})

        entry = self._source_entry()
        if self._modbus_enabled():
            data_schema = vol.Schema(
                {
                    vol.Required(
                        CONF_MODBUS_HOST,
                        default=self.options.get(
                            CONF_MODBUS_HOST,
                            entry.data.get(CONF_MODBUS_HOST, DEFAULT_MODBUS_HOST),
                        ),
                    ): str,
                    vol.Optional(
                        CONF_MODBUS_PORT,
                        default=self.options.get(
                            CONF_MODBUS_PORT,
                            entry.data.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT),
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                    vol.Optional(
                        CONF_MODBUS_UNIT_ID,
                        default=self.options.get(
                            CONF_MODBUS_UNIT_ID,
                            entry.data.get(
                                CONF_MODBUS_UNIT_ID, DEFAULT_MODBUS_UNIT_ID
                            ),
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
                    vol.Optional(
                        CONF_MODBUS_SCAN_INTERVAL,
                        default=_normalize_modbus_scan_interval(
                            self.options.get(
                                CONF_MODBUS_SCAN_INTERVAL, DEFAULT_MODBUS_SCAN_INTERVAL
                            )
                        ),
                    ): vol.All(
                        vol.Coerce(int), vol.Clamp(min=MIN_MODBUS_SCAN_INTERVAL)
                    ),
                }
            )
        else:
            data_schema = vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Clamp(min=MIN_SCAN_INTERVAL)),
                }
            )

        return self.async_show_form(step_id="init", data_schema=data_schema)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
