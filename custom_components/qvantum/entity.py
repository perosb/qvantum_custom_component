"""Base entity classes for Qvantum integration."""

import logging
from typing import Union
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import QvantumDataUpdateCoordinator
from .maintenance_coordinator import QvantumMaintenanceCoordinator

_LOGGER = logging.getLogger(__name__)


class QvantumAccessMixin:
    """Mixin to provide write access checking for Qvantum entities."""

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
            # For tests or incomplete setup, deny write access and log misconfiguration
            _LOGGER.warning(
                "Qvantum write access check failed due to missing coordinator runtime data; "
                "denying write access for entity %s",
                getattr(self, "_attr_unique_id", self),
            )
            return False


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

    def _resolve_device_id(self, device: DeviceInfo | dict[str, object]) -> str | None:
        """Resolve device ID from device info or coordinator data."""

        # DeviceInfo is a TypedDict (dict subclass), so check if it's dict-like
        if isinstance(device, dict) and "identifiers" in device:
            identifiers = device["identifiers"]
            # identifiers should be a set of (domain, identifier) tuples
            for domain, identifier in identifiers:
                if domain == DOMAIN and identifier.startswith(f"{DOMAIN}-"):
                    device_id = identifier.removeprefix(f"{DOMAIN}-")
                    if device_id:
                        return device_id

        # Falls back to coordinator data if device ID not found in device info
        metrics_data = self.coordinator.data.get("metrics", {})
        heatpump_id = metrics_data.get("hpid")
        if heatpump_id is not None:
            return str(heatpump_id)

        return None
