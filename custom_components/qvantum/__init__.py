"""The Qvantum Heat Pump integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import json

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION, PATCH_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_registry import async_migrate_entries
from homeassistant.components.persistent_notification import async_dismiss


from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
)
from .api import QvantumAPI
from .const import DOMAIN, VERSION, CONFIG_VERSION, FIRMWARE_KEYS
from .coordinator import QvantumDataUpdateCoordinator
from .maintenance_coordinator import QvantumMaintenanceCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SELECT,
    # Platform.FAN,
]

type MyConfigEntry = ConfigEntry[RuntimeData]


@dataclass
class RuntimeData:
    """Class to hold your data."""

    coordinator: QvantumDataUpdateCoordinator
    maintenance_coordinator: QvantumMaintenanceCoordinator | None = None
    device: DeviceInfo | None = None


async def async_setup_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Set up Qvantum Heat Pump Integration from a config entry."""

    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]
    user_agent = f"Home Assistant/{MAJOR_VERSION}.{MINOR_VERSION}.{PATCH_VERSION} Qvantum/{VERSION}"

    hass.data[DOMAIN] = QvantumAPI(
        username=username, password=password, user_agent=user_agent
    )
    hass.data[DOMAIN].hass = hass

    coordinator = QvantumDataUpdateCoordinator(hass, config_entry)
    await coordinator.async_config_entry_first_refresh()

    if not coordinator.data.get("device") or not coordinator.data.get("device").get(
        "device_metadata"
    ):
        _LOGGER.error(
            "No device data found when setting up Qvantum integration, 2nd attempt"
        )
        await coordinator.async_config_entry_first_refresh()

    if not coordinator.data.get("device") or not coordinator.data.get("device").get(
        "device_metadata"
    ):
        _LOGGER.error(
            "No device data found when setting up Qvantum integration, failure"
        )
        return False

    device_metadata = coordinator.data.get("device").get("device_metadata")
    device = DeviceInfo(
        identifiers={
            (DOMAIN, f"qvantum-{coordinator.data.get('device').get('id')}"),
        },
        manufacturer=coordinator.data.get("device").get("vendor"),
        model=coordinator.data.get("device").get("model"),
        translation_key="qvantum_flvp",
        name="Qvantum",
        serial_number=coordinator.data.get("device").get("serial"),
        sw_version=f"{device_metadata.get('display_fw_version')}/{device_metadata.get('cc_fw_version')}/{device_metadata.get('inv_fw_version')}",
    )

    # Only register the service if it hasn't been registered yet
    if not hass.services.has_service(DOMAIN, "extra_hot_water"):
        await async_setup_services(hass)

    # Initialize maintenance coordinator (handles firmware updates and maintenance tasks)
    maintenance_coordinator = QvantumMaintenanceCoordinator(
        hass, config_entry, coordinator
    )
    await maintenance_coordinator.async_config_entry_first_refresh()

    config_entry.runtime_data = RuntimeData(
        coordinator, maintenance_coordinator, device
    )
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


# Example migration function
async def async_migrate_entry(hass, config_entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug("Migrating configuration from version %s.%s", config_entry.version, config_entry.minor_version)

    if config_entry.version > 1:
        return False

    if config_entry.version == 1:

        @callback
        def update_unique_id(entity_entry):
            """Update unique ID of entity entry."""

            if entity_entry.domain in ["switch", "climate", "fan", "number"]:
                _LOGGER.debug("Skipping migration of entity %s in domain %s", entity_entry.entity_id, entity_entry.domain)
                return None

            old_unique_id = entity_entry.unique_id
            new_unique_id = entity_entry.unique_id
            new_unique_id = new_unique_id.replace("_outdoor_temperature_", "_bt1_")
            new_unique_id = new_unique_id.replace("_indoor_temperature_", "_bt2_")
            new_unique_id = new_unique_id.replace("_heating_flow_temperature_target_", "_cal_heat_temp_")
            new_unique_id = new_unique_id.replace("_heating_flow_temperature_", "_bt11_")
            new_unique_id = new_unique_id.replace("_tap_water_tank_temperature_", "_bt30_")
            new_unique_id = new_unique_id.replace("_tap_water_capacity", "_tap_water_cap")
            new_unique_id = new_unique_id.replace("_tap_water_start_", "_dhw_normal_start_")
            new_unique_id = new_unique_id.replace("_tap_water_stop_", "_dhw_normal_stop_")

            if old_unique_id == new_unique_id:
                return None

            _LOGGER.debug("Updating unique ID for entity %s from %s to %s",
                entity_entry.entity_id, old_unique_id, new_unique_id
            )
            return {
                "new_unique_id": entity_entry.unique_id.replace(
                    old_unique_id, new_unique_id
                )
            }

        await async_migrate_entries(hass, config_entry.entry_id, update_unique_id)

        hass.config_entries.async_update_entry(config_entry, version=CONFIG_VERSION)

        _LOGGER.debug("Migration to configuration version %s.%s successful", config_entry.version, config_entry.minor_version)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Unload Qvantum Heat Pump Integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )

    if (
        unload_ok
        and config_entry.runtime_data
        and config_entry.runtime_data.maintenance_coordinator
    ):
        # Clear any firmware update notifications when unloading
        maintenance_coordinator = config_entry.runtime_data.maintenance_coordinator

        # Only attempt to clear notifications if we have a real coordinator with a main coordinator
        if (
            hasattr(maintenance_coordinator, "main_coordinator")
            and maintenance_coordinator.main_coordinator
            and hasattr(maintenance_coordinator.main_coordinator, "_device")
            and maintenance_coordinator.main_coordinator._device
        ):
            device_id = maintenance_coordinator.main_coordinator._device.get("id")

            if device_id:
                # Clear notifications for all firmware components
                for fw_key in FIRMWARE_KEYS:
                    notification_id = f"qvantum_firmware_update_{device_id}_{fw_key}"
                    try:
                        await async_dismiss(hass, notification_id)
                        _LOGGER.debug(
                            "Cleared firmware notification %s", notification_id
                        )
                    except Exception as err:
                        _LOGGER.debug(
                            "Could not clear firmware notification %s: %s",
                            notification_id,
                            err,
                        )

    return unload_ok