"""Base entity classes for Qvantum integration."""

import logging
from typing import Union, List
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import QvantumDataUpdateCoordinator
from .maintenance_coordinator import QvantumMaintenanceCoordinator

_LOGGER = logging.getLogger(__name__)


def resolve_device_id(device: DeviceInfo | dict[str, object]) -> str | None:
    """Resolve device ID from device info.

    Handles both dict format (from coordinator) and DeviceInfo format.
    """
    # Check if it's a dict with direct "id" key
    if isinstance(device, dict) and "id" in device:
        return str(device["id"])

    # Check if it's DeviceInfo with identifiers
    if isinstance(device, dict) and "identifiers" in device:
        identifiers = device["identifiers"]
        # identifiers should be a set of (domain, identifier) tuples
        for domain, identifier in identifiers:
            if domain == DOMAIN and identifier.startswith(f"{DOMAIN}-"):
                device_id = identifier.removeprefix(f"{DOMAIN}-")
                if device_id:
                    return str(device_id)

    return None


class QvantumAccessMixin:
    """Mixin to provide write access checking for Qvantum entities."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._write_access_warning_logged = False

    @property
    def _has_write_access(self) -> bool:
        """Check if the user has write access level >= 20."""
        try:
            if not isinstance(self.coordinator, QvantumDataUpdateCoordinator):
                return True  # Maintenance entities always available
            maintenance_coordinator = (
                self.coordinator.config_entry.runtime_data.maintenance_coordinator
            )
            if not maintenance_coordinator or not maintenance_coordinator.data:
                return False
            access_level = maintenance_coordinator.data.get("access_level", {})
            return access_level.get("writeAccessLevel", 0) >= 20
        except AttributeError:
            # For tests or incomplete setup, deny write access and log misconfiguration once per entity
            if not self._write_access_warning_logged:
                _LOGGER.debug(
                    "Qvantum write access check failed due to missing coordinator runtime data; "
                    "denying write access for entity %s",
                    getattr(self, "_attr_unique_id", self),
                )
                self._write_access_warning_logged = True
            return False


# Centralized icon map for all Qvantum entities keyed by metric_key
_ENTITY_ICONS: dict[str, str] = {
    # Binary sensors / demand
    "heatingdemand": "mdi:heat-wave",
    "dhwdemand": "mdi:water-pump",
    "coolingdemand": "mdi:snowflake",
    "cooling_enabled": "mdi:snowflake",
    "additiondemand": "mdi:lightning-bolt",
    "additiondhwdemand": "mdi:lightning-bolt",
    # Sensors
    "tap_water_cap": "mdi:account-group",
    # Switches
    "op_mode": "mdi:auto-mode",
    "man_mode": "mdi:radiator",
    "op_man_dhw": "mdi:water-outline",
    "op_man_addition": "mdi:transmission-tower-import",
    "extra_tap_water": "mdi:water-boiler",
    "enable_sc_sh": "mdi:radiator",
    "enable_sc_dhw": "mdi:water-thermometer",
    # Select
    "use_adaptive": "mdi:leaf",
}


class QvantumEntity(QvantumAccessMixin, CoordinatorEntity):
    """Base class for all Qvantum entities with common initialization."""

    def __init__(
        self,
        coordinator: Union[QvantumDataUpdateCoordinator, QvantumMaintenanceCoordinator],
        metric_key: str,
        device: DeviceInfo | dict[str, object],
        enabled_by_default: bool = True,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)

        # Resolve device ID from device info or coordinator data
        self._hpid = self._resolve_device_id(device)

        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_has_entity_name = True
        self._attr_entity_registry_enabled_default = enabled_by_default
        self._attr_icon = _ENTITY_ICONS.get(metric_key)

    @property
    def metric_key(self) -> str:
        """Return the metric key for this entity."""
        return self._metric_key

    @property
    def _values(self) -> dict:
        """Return current values from coordinator data, safe when data is None."""
        return (self.coordinator.data or {}).get("values", {})

    def _resolve_device_id(self, device: DeviceInfo | dict[str, object]) -> str | None:
        """Resolve device ID from device info or coordinator data."""
        device_id = resolve_device_id(device)
        if device_id:
            return device_id

        # Falls back to coordinator data if device ID not found in device info
        values_data = (self.coordinator.data or {}).get("values", {})
        heatpump_id = values_data.get("hpid")
        if heatpump_id is not None:
            return str(heatpump_id)

        return None


def disable_entities_by_default(
    hass: HomeAssistant, entities: List["QvantumEntity"]
) -> None:
    """Disable entities that should be disabled by default."""
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity_registry import RegistryEntryDisabler

    entity_registry = er.async_get(hass)
    for entity in entities:
        if not entity._attr_entity_registry_enabled_default:
            entity_entry = entity_registry.async_get(entity.entity_id)
            if entity_entry and entity_entry.disabled_by is None:
                # Entity is currently enabled, respect user's choice
                continue
            if (
                entity_entry is None
                or entity_entry.disabled_by != RegistryEntryDisabler.USER
            ):
                entity_registry.async_update_entity(
                    entity.entity_id, disabled_by=RegistryEntryDisabler.INTEGRATION
                )


def extract_metric_key(unique_id: str, device_id: str) -> str:
    """Extract metric key from unique_id."""
    prefix = "qvantum_"
    suffix = f"_{device_id}"
    if not unique_id.startswith(prefix) or not unique_id.endswith(suffix):
        raise ValueError(
            f"Invalid unique_id format: expected '{prefix}<metric_key>{suffix}', got '{unique_id}'"
        )
    return unique_id[len(prefix) : len(unique_id) - len(suffix)]


def cleanup_disabled_entities(
    hass: HomeAssistant,
    coordinator: QvantumDataUpdateCoordinator,
    possible_metrics: set[str],
    domain: str,
) -> None:
    """Clean up disabled entities that are no longer supported in the current mode."""
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import device_registry as dr

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    device_reg_id = None
    for dev in device_registry.devices.values():
        if (DOMAIN, f"qvantum-{coordinator.device_id}") in dev.identifiers:
            device_reg_id = dev.id
            break
    if device_reg_id:
        entities_to_remove = []
        for entity_entry in entity_registry.entities.values():
            if (
                entity_entry.device_id == device_reg_id
                and entity_entry.domain == domain
                and entity_entry.unique_id.startswith("qvantum_")
                and entity_entry.unique_id.endswith(f"_{coordinator.device_id}")
            ):
                metric_key = extract_metric_key(
                    entity_entry.unique_id, coordinator.device_id
                )
                if metric_key not in possible_metrics:
                    entities_to_remove.append(entity_entry.entity_id)
        for entity_id in entities_to_remove:
            entity_registry.async_remove(entity_id)