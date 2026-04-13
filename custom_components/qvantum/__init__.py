"""The Qvantum Heat Pump integration."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
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
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
    async_migrate_entries,
)
from homeassistant.components.persistent_notification import async_dismiss


from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
)
from .api import QvantumAPI
from .const import (
    DOMAIN,
    VERSION,
    CONFIG_VERSION,
    FIRMWARE_KEYS,
    CONF_MODBUS_TCP,
    CONF_MODBUS_HOST,
    DEFAULT_MODBUS_HOST,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_UNIT_ID,
)
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
    Platform.FAN,
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

    modbus_enabled = config_entry.options.get(
        CONF_MODBUS_TCP,
        config_entry.data.get(CONF_MODBUS_TCP, False),
    )
    modbus_host = config_entry.options.get(
        CONF_MODBUS_HOST,
        config_entry.data.get(CONF_MODBUS_HOST, DEFAULT_MODBUS_HOST),
    )

    hass.data[DOMAIN] = QvantumAPI(
        username=username,
        password=password,
        user_agent=user_agent,
        modbus_tcp=modbus_enabled,
        modbus_host=modbus_host,
        modbus_port=DEFAULT_MODBUS_PORT,
        modbus_unit_id=DEFAULT_MODBUS_UNIT_ID,
    )
    hass.data[DOMAIN].hass = hass

    coordinator = QvantumDataUpdateCoordinator(hass, config_entry)
    await coordinator.async_restore_dhw_state()
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

    remove_listener = config_entry.add_update_listener(_async_update_listener)
    config_entry.async_on_unload(remove_listener)

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

    if config_entry.version > CONFIG_VERSION:
        # Entry is from a newer version of the integration (downgrade case)
        return False

    if config_entry.version == CONFIG_VERSION:
        return True

    if config_entry.version == 1:

        @callback
        def migrate_v1_unique_ids(entity_entry):
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
            new_unique_id = new_unique_id.replace(
                "_tap_water_capacity", "_tap_water_cap"
            )

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

        await async_migrate_entries(hass, config_entry.entry_id, migrate_v1_unique_ids)

    if config_entry.version < 5:

        @callback
        def migrate_to_v5_number_unique_ids(entity_entry):
            """Pre-migrate number entities to the qvantum_number_ format to free up unique IDs.

            Number entities that share a metric key with sensor entities (e.g.
            tap_water_start/stop) would collide when the sensor rename below runs,
            because HA enforces unique_id uniqueness across all platforms within a
            config entry.

            This intentionally applies the same number-prefix normalization later
            schema versions use, but it must happen here first so the old number
            unique IDs are vacated before sensor entities are renamed in the same
            migration path. In other words, this is a compatibility pre-step to
            avoid cross-platform unique_id conflicts during upgrades from older
            entries, not an accidental second migration.
            """
            if entity_entry.domain != "number":
                return None
            old_unique_id = entity_entry.unique_id
            # Skip entries already in the new format
            if old_unique_id.startswith("qvantum_number_"):
                return None
            new_unique_id = old_unique_id
            new_unique_id = new_unique_id.replace(
                "_dhw_normal_start_", "_tap_water_start_"
            )
            new_unique_id = new_unique_id.replace(
                "_dhw_normal_stop_", "_tap_water_stop_"
            )
            new_unique_id = new_unique_id.replace("qvantum_", "qvantum_number_", 1)
            if old_unique_id == new_unique_id:
                return None
            _LOGGER.debug(
                "Updating unique ID for number entity %s from %s to %s",
                entity_entry.entity_id,
                old_unique_id,
                new_unique_id,
            )
            return {"new_unique_id": new_unique_id}

        await async_migrate_entries(
            hass, config_entry.entry_id, migrate_to_v5_number_unique_ids
        )

        # Rename dhw_normal_start/stop sensor entities to tap_water_start/stop.
        # Use the entity registry directly so we can handle collisions gracefully:
        # if the target unique_id is already in use by another entity (e.g. a
        # "live" sensor that was already created with the new metric name) the
        # entity being renamed is an orphaned stale entry and should be removed.
        ent_reg = async_get_entity_registry(hass)
        entry_entities = [
            e
            for e in ent_reg.entities.values()
            if e.config_entry_id == config_entry.entry_id
        ]
        uid_map: dict[str, str] = {e.unique_id: e.entity_id for e in entry_entities}
        for entity_entry in entry_entities:
            if entity_entry.domain == "number":
                continue
            old_uid = entity_entry.unique_id
            new_uid = old_uid.replace("_dhw_normal_start_", "_tap_water_start_")
            new_uid = new_uid.replace("_dhw_normal_stop_", "_tap_water_stop_")
            if old_uid == new_uid:
                continue
            if new_uid in uid_map and uid_map[new_uid] != entity_entry.entity_id:
                _LOGGER.debug(
                    "Removing orphaned entity %s (unique_id %s) because"
                    " %s already holds unique_id %s",
                    entity_entry.entity_id,
                    old_uid,
                    uid_map[new_uid],
                    new_uid,
                )
                ent_reg.async_remove(entity_entry.entity_id)
                uid_map.pop(old_uid, None)
            else:
                _LOGGER.debug(
                    "Updating unique ID for entity %s from %s to %s",
                    entity_entry.entity_id,
                    old_uid,
                    new_uid,
                )
                ent_reg.async_update_entity(
                    entity_entry.entity_id, new_unique_id=new_uid
                )
                uid_map.pop(old_uid, None)
                uid_map[new_uid] = entity_entry.entity_id

    if config_entry.version < 6:

        @callback
        def migrate_to_v6_entity_domains(entity_entry):
            """Migrate demand sensors from sensor to binary_sensor domain."""
            demand_keys = ["dhwdemand", "heatingdemand", "coolingdemand"]
            if entity_entry.domain == "sensor":
                for key in demand_keys:
                    if entity_entry.unique_id.endswith(f"_{key}"):
                        new_entity_id = (
                            f"binary_sensor.{entity_entry.entity_id.split('.', 1)[1]}"
                        )
                        _LOGGER.debug(
                            "Migrating entity %s from sensor to binary_sensor: %s",
                            entity_entry.entity_id,
                            new_entity_id,
                        )
                        return {"new_entity_id": new_entity_id}
            return None

        await async_migrate_entries(
            hass, config_entry.entry_id, migrate_to_v6_entity_domains
        )

    if config_entry.version < 7:

        @callback
        def migrate_to_v7_number_unique_ids(entity_entry):
            """Prefix number entity unique IDs with 'number_' to avoid conflicts with sensor entities."""
            if entity_entry.domain != "number":
                return None
            old_unique_id = entity_entry.unique_id
            # Skip entries already in the new format
            if old_unique_id.startswith("qvantum_number_"):
                return None
            # Old format: qvantum_{metric_key}_{device_id}
            # New format: qvantum_number_{metric_key}_{device_id}
            new_unique_id = old_unique_id.replace("qvantum_", "qvantum_number_", 1)
            if new_unique_id == old_unique_id:
                return None
            _LOGGER.debug(
                "Updating unique ID for number entity %s from %s to %s",
                entity_entry.entity_id,
                old_unique_id,
                new_unique_id,
            )
            return {"new_unique_id": new_unique_id}

        await async_migrate_entries(
            hass, config_entry.entry_id, migrate_to_v7_number_unique_ids
        )

    hass.config_entries.async_update_entry(config_entry, version=CONFIG_VERSION)

    _LOGGER.debug(
        "Migration to configuration version %s.%s successful",
        config_entry.version,
        config_entry.minor_version,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: MyConfigEntry) -> bool:
    """Unload Qvantum Heat Pump Integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )

    if hass.data.get(DOMAIN) is not None:
        try:
            await hass.data[DOMAIN].close()
        except Exception as err:
            _LOGGER.debug("Failed closing Qvantum API session on unload: %s", err)
        hass.data.pop(DOMAIN, None)

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
                        result = async_dismiss(hass, notification_id)
                        if inspect.isawaitable(result):
                            await result
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