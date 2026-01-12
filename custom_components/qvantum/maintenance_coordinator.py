"""QvantumMaintenanceCoordinator for firmware and access level monitoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import QvantumDataUpdateCoordinator
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.helpers.device_registry import async_get

from .api import APIAuthError
from .const import DOMAIN, FIRMWARE_KEYS
import traceback

_LOGGER = logging.getLogger(__name__)


class QvantumMaintenanceCoordinator(DataUpdateCoordinator):
    """Qvantum maintenance coordinator that checks for firmware changes and access level every 2 hours."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        main_coordinator: "QvantumDataUpdateCoordinator",
    ) -> None:
        """Initialize firmware coordinator."""
        self.api = hass.data[DOMAIN]
        self.main_coordinator = main_coordinator
        self._last_firmware_versions = {}

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} Firmware ({config_entry.unique_id})",
            update_method=self.async_check_firmware_updates,
            update_interval=timedelta(hours=2),  # Check every 2 hours
        )

    async def async_check_firmware_updates(self):
        """Check for firmware updates by fetching device metadata and comparing versions."""
        try:
            # Get the device from the main coordinator
            device = self.main_coordinator._device
            if not device:
                _LOGGER.debug("No device available for firmware check")
                return {}

            device_id = device.get("id")
            if not device_id:
                _LOGGER.debug("No device ID available for firmware check")
                return {}

            # Fetch fresh device metadata
            metadata = await self.api.get_device_metadata(device_id)

            if not metadata or "device_metadata" not in metadata:
                _LOGGER.debug("No device metadata available for firmware check")
                return {}

            # Fetch access level
            access_level = await self.api.get_access_level(device_id)

            current_versions = metadata["device_metadata"]
            firmware_changed = False

            # Compare firmware versions
            firmware_changes = []
            for fw_key in FIRMWARE_KEYS:
                if fw_key in current_versions:
                    current_version = current_versions[fw_key]
                    last_version = self._last_firmware_versions.get(fw_key)

                    if last_version is not None and current_version != last_version:
                        _LOGGER.info(
                            "Firmware %s updated from %s to %s for device %s",
                            fw_key,
                            last_version,
                            current_version,
                            device_id,
                        )
                        firmware_changes.append(
                            {
                                "component": fw_key,
                                "from_version": last_version,
                                "to_version": current_version,
                            }
                        )
                        firmware_changed = True
                    elif last_version is None:
                        _LOGGER.debug(
                            "Initial firmware %s version detected: %s for device %s",
                            fw_key,
                            current_version,
                            device_id,
                        )

                    # Update stored version
                    self._last_firmware_versions[fw_key] = current_version

            # Create notifications for firmware changes
            if firmware_changes:
                await self._create_firmware_update_notifications(
                    device_id, firmware_changes
                )
                _LOGGER.info("Firmware update detected for device %s", device_id)
            else:
                _LOGGER.debug("No firmware changes detected for device %s", device_id)

            return {
                "device_id": device_id,
                "firmware_versions": current_versions,
                "access_level": access_level,
                "firmware_changed": firmware_changed,
                "last_check": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }

        except APIAuthError as err:
            _LOGGER.error("Authentication error during firmware check: %s", err)
            raise UpdateFailed(err) from err
        except Exception as err:
            _LOGGER.error("Error checking firmware updates: %s", err)
            stack_trace = traceback.format_exc()
            raise UpdateFailed(
                f"Error checking firmware updates: {err}\nStack trace:\n{stack_trace}"
            ) from err

    async def _create_firmware_update_notifications(
        self, device_id: str, firmware_changes: list[dict[str, str]]
    ) -> None:
        """Create persistent notifications for firmware updates and update device registry."""
        try:
            # Get device info for better notification context
            device_info = self.main_coordinator.data.get("device", {})
            device_name = device_info.get("model", f"Device {device_id}")

            # Update device registry with new firmware versions
            await self._update_device_registry_firmware_versions(device_id)

            # Create a notification for each firmware component that changed
            for change in firmware_changes:
                component = change["component"]
                from_version = change["from_version"]
                to_version = change["to_version"]

                # Create human-readable component name
                component_names = {
                    "display_fw_version": "Display",
                    "cc_fw_version": "Control Center",
                    "inv_fw_version": "Inverter",
                }
                component_name = component_names.get(component, component)

                # Create unique notification ID
                notification_id = f"qvantum_firmware_update_{device_id}_{component}"

                message = f"""
**Qvantum Firmware Update Detected**

Device: {device_name} ({device_id})
Component: {component_name}
Version: {from_version} → {to_version}

The firmware has been automatically updated. No action is required.
"""

                # Use service call instead of direct async_create to avoid import issues
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "message": message,
                        "title": "Qvantum Firmware Updated",
                        "notification_id": notification_id,
                    },
                )

                _LOGGER.info(
                    "Created firmware update notification for %s %s: %s → %s",
                    device_name,
                    component_name,
                    from_version,
                    to_version,
                )

        except Exception as err:
            _LOGGER.error("Error creating firmware update notification: %s", err)

    async def _update_device_registry_firmware_versions(self, device_id: str) -> None:
        """Update the device registry with current firmware versions."""
        try:
            device_registry = async_get(self.hass)

            # Find the device entry
            device_entry = None
            for device in device_registry.devices.values():
                if (DOMAIN, f"qvantum-{device_id}") in device.identifiers:
                    device_entry = device
                    break

            if not device_entry:
                _LOGGER.debug("Device entry not found for device %s", device_id)
                return

            # Get current firmware versions from the firmware coordinator data
            firmware_versions = (
                self.data.get("firmware_versions", {}) if self.data else {}
            )
            display_version = firmware_versions.get("display_fw_version")
            cc_version = firmware_versions.get("cc_fw_version")
            inv_version = firmware_versions.get("inv_fw_version")

            if display_version and cc_version and inv_version:
                new_sw_version = f"{display_version}/{cc_version}/{inv_version}"

                # Update the device registry entry
                device_registry.async_update_device(
                    device_entry.id, sw_version=new_sw_version
                )

                _LOGGER.info(
                    "Updated device registry firmware version for device %s to %s",
                    device_id,
                    new_sw_version,
                )
            else:
                _LOGGER.debug(
                    "Incomplete firmware version data for device %s", device_id
                )

        except Exception as err:
            _LOGGER.error("Error updating device registry firmware versions: %s", err)
