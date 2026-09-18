"""Water heater platform wrapping existing DHW / VV APIs."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .const import (
    DHW_MODE_ECO,
    DHW_MODE_EXTRA,
    DHW_MODE_NORMAL,
    DHW_MODE_SMART,
)
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response
from .entity import QvantumAccessMixin

_LOGGER = logging.getLogger(__name__)

# Operation modes shown in HA. eco/off use HA standard state strings.
OPERATION_ECO = STATE_ECO
OPERATION_NORMAL = "normal"
OPERATION_EXTRA = "extra"
OPERATION_SMART = "smart"
OPERATION_OFF = STATE_OFF

DHW_MODE_TO_OPERATION = {
    DHW_MODE_ECO: OPERATION_ECO,
    DHW_MODE_NORMAL: OPERATION_NORMAL,
    DHW_MODE_EXTRA: OPERATION_EXTRA,
    DHW_MODE_SMART: OPERATION_SMART,
}

OPERATION_TO_DHW_MODE = {v: k for k, v in DHW_MODE_TO_OPERATION.items()}

# Prefer tank top sensor, then other VV tank sensors (not cold inlet bt33).
CURRENT_TEMPERATURE_KEYS = ("bt30", "bt31", "bt34")

# Match number.tap_water_stop limits.
TAP_WATER_STOP_MIN = 60
TAP_WATER_STOP_MAX = 80
TAP_WATER_STOP_STEP = 1

# Indefinite extra DHW — same as switch.extra_tap_water turn_on.
EXTRA_DHW_INDEFINITE_MINUTES = -1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the water heater platform."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    entities: list[QvantumWaterHeaterEntity] = []
    values = (coordinator.data or {}).get("values", {})
    if "tap_water_stop" in values or any(k in values for k in CURRENT_TEMPERATURE_KEYS):
        entities.append(QvantumWaterHeaterEntity(coordinator, device))

    async_add_entities(entities)
    _LOGGER.debug("Setting up platform WATER_HEATER")


class QvantumWaterHeaterEntity(
    QvantumAccessMixin, CoordinatorEntity, WaterHeaterEntity
):
    """DHW water heater wrapping tap_water_* / dhw_mode / extra_tap_water."""

    def __init__(
        self, coordinator: QvantumDataUpdateCoordinator, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._hpid = (self.coordinator.data or {}).get("values", {}).get("hpid")
        self._attr_unique_id = f"qvantum_water_heater_{self._hpid}"
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = PRECISION_WHOLE
        self._attr_target_temperature_step = float(TAP_WATER_STOP_STEP)
        self._attr_min_temp = float(TAP_WATER_STOP_MIN)
        self._attr_max_temp = float(TAP_WATER_STOP_MAX)
        self._attr_device_info = device
        self._attr_translation_key = "dhw"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:water-boiler"

    @property
    def _values(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("values", {})

    @property
    def supported_features(self) -> WaterHeaterEntityFeature:
        features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
        )
        if "op_man_dhw" in self._values:
            features |= WaterHeaterEntityFeature.ON_OFF
        return features

    @property
    def available(self) -> bool:
        return (
            self.current_temperature is not None or self.target_temperature is not None
        ) and self._has_write_access

    @property
    def current_temperature(self) -> float | None:
        """Tank temperature — prefer bt30, else best available VV tank temp."""
        values = self._values
        for key in CURRENT_TEMPERATURE_KEYS:
            temperature = values.get(key)
            if temperature is not None:
                return temperature
        return None

    @property
    def target_temperature(self) -> float | None:
        """DHW stop temperature (tap_water_stop)."""
        stop = self._values.get("tap_water_stop")
        if stop is None:
            return None
        return float(stop)

    @property
    def current_operation(self) -> str | None:
        """Map DHW mode / extra_tap_water / op_man_dhw to an operation mode."""
        return map_operation_mode(self._values)

    @property
    def operation_list(self) -> list[str]:
        return operation_modes_for_values(
            self._values, modbus_enabled=bool(self.coordinator.modbus_enabled)
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write tap_water_stop via the existing set_tap_water path."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        stop = int(temperature)
        response = await self.coordinator.client.set_tap_water(self._hpid, stop=stop)
        await handle_setting_update_response(
            response,
            self.coordinator,
            "values",
            "tap_water_stop",
            stop,
        )

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Map HA operation mode onto existing DHW write helpers."""
        if operation_mode not in self.operation_list:
            raise HomeAssistantError(
                f"Unsupported DHW operation mode: {operation_mode}"
            )

        values = self._values

        if operation_mode == OPERATION_OFF:
            await self._async_set_op_man_dhw(0)
            return

        # Leaving Off re-enables DHW in manual mode when the flag exists.
        if values.get("op_man_dhw") == 0:
            await self._async_set_op_man_dhw(1)

        if operation_mode == OPERATION_EXTRA:
            response = await self.coordinator.async_set_extra_tap_water(
                self._hpid, EXTRA_DHW_INDEFINITE_MINUTES
            )
            await handle_setting_update_response(
                response, self.coordinator, "values", "extra_tap_water", "on"
            )
            self._optimistic_dhw_mode(DHW_MODE_EXTRA)
            return

        if operation_mode == OPERATION_NORMAL:
            response = await self.coordinator.async_set_extra_tap_water(self._hpid, 0)
            await handle_setting_update_response(
                response, self.coordinator, "values", "extra_tap_water", "off"
            )
            self._optimistic_dhw_mode(DHW_MODE_NORMAL)
            return

        # Eco / Smart require writing holding 53 (dhw_mode). Available on Modbus;
        # cloud has no dedicated Eco/Smart DHW-mode setter.
        if not self.coordinator.modbus_enabled and "dhw_mode" not in values:
            raise HomeAssistantError(
                f"DHW mode '{operation_mode}' requires Modbus (dhw_mode holding)"
            )

        dhw_mode = OPERATION_TO_DHW_MODE[operation_mode]
        response = await self.coordinator.async_write_metric(
            self._hpid, "extra_tap_water", dhw_mode
        )
        await handle_setting_update_response(
            response,
            self.coordinator,
            "values",
            "extra_tap_water",
            "on" if dhw_mode == DHW_MODE_EXTRA else "off",
        )
        self._optimistic_dhw_mode(dhw_mode)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn DHW back on (Normal)."""
        await self.async_set_operation_mode(OPERATION_NORMAL)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable DHW via op_man_dhw when available."""
        await self.async_set_operation_mode(OPERATION_OFF)

    async def _async_set_op_man_dhw(self, value: int) -> None:
        response = await self.coordinator.client.update_setting(
            self._hpid, "op_man_dhw", value
        )
        await handle_setting_update_response(
            response, self.coordinator, "values", "op_man_dhw", value
        )

    def _optimistic_dhw_mode(self, dhw_mode: int) -> None:
        """Keep raw dhw_mode in sync after a successful write when present."""
        data = self.coordinator.data
        if not data:
            return
        values = data.get("values")
        if isinstance(values, dict) and "dhw_mode" in values:
            values["dhw_mode"] = dhw_mode
            self.coordinator.async_set_updated_data(data)


def map_operation_mode(values: dict[str, Any]) -> str | None:
    """Map coordinator values to a water_heater operation mode."""
    if values.get("op_man_dhw") == 0:
        return OPERATION_OFF

    dhw_mode = values.get("dhw_mode")
    if dhw_mode in DHW_MODE_TO_OPERATION:
        return DHW_MODE_TO_OPERATION[dhw_mode]

    extra = values.get("extra_tap_water")
    if extra in ("on", True, DHW_MODE_EXTRA):
        return OPERATION_EXTRA

    # Cloud SoftControl is not a DHW mode; Extra off → Normal.
    if extra in ("off", False, DHW_MODE_ECO, DHW_MODE_NORMAL, DHW_MODE_SMART, 0):
        return OPERATION_NORMAL

    if extra is None and dhw_mode is None:
        return None
    return OPERATION_NORMAL


def operation_modes_for_values(
    values: dict[str, Any], *, modbus_enabled: bool
) -> list[str]:
    """Return supported operation modes for the current transport/data."""
    modes: list[str] = [OPERATION_NORMAL, OPERATION_EXTRA]
    # Eco/Smart need dhw_mode (Modbus holding 53).
    if modbus_enabled or "dhw_mode" in values:
        modes = [OPERATION_ECO, OPERATION_NORMAL, OPERATION_EXTRA, OPERATION_SMART]
    if "op_man_dhw" in values:
        modes = [*modes, OPERATION_OFF]
    return modes
