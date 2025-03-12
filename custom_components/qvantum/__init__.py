"""The Qvantum Heat Pump integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import QvantumDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

type MyConfigEntry = ConfigEntry[RuntimeData]


@dataclass
class RuntimeData:
    """Class to hold your data."""

    coordinator: DataUpdateCoordinator
    device: DeviceInfo | None = None


async def async_setup_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Set up Qvantum Heat Pump Integration from a config entry."""

    coordinator = QvantumDataUpdateCoordinator(hass, config_entry)

    await coordinator.async_config_entry_first_refresh()

    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_update_listener)
    )

    device = DeviceInfo(
        identifiers={
            (DOMAIN, f"qvantum-{coordinator.data.get('device').get('id')}"),
        },
        manufacturer=coordinator.data.get("device").get("vendor"),
        model=coordinator.data.get("device").get("model"),
        name="Qvantum",
        serial_number=coordinator.data.get("device").get("serial"),
        sw_version=f"{coordinator.data.get('device_metadata').get('display_fw_version')}/{coordinator.data.get('device_metadata').get('cc_fw_version')}/{coordinator.data.get('device_metadata').get('inv_fw_version')}",
    )    

    config_entry.runtime_data = RuntimeData(coordinator, device)
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def _async_update_listener(hass: HomeAssistant, config_entry):
    """Handle config options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Delete device if selected from UI."""
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
