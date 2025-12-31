"""Interfaces with the Qvantum Heat Pump api select entities."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Select."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    sensors = []
    sensors.append(QvantumSelectEntity(coordinator, "use_adaptive", device))

    async_add_entities(sensors)

    _LOGGER.debug("Setting up platform SELECT")


class QvantumSelectEntity(CoordinatorEntity, SelectEntity):
    """Select entity for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_has_entity_name = True

        match self._metric_key:
            case "use_adaptive":
                self._attr_options = [
                    "-1",
                    "0",
                    "1",
                    "2",
                ]  # Translation keys that will be translated by HA
                self._attr_icon = "mdi:leaf"

    def _is_valid_mode(self, mode, valid_modes: set) -> bool:
        """Check if a mode value is valid."""
        try:
            return int(mode) in valid_modes
        except (TypeError, ValueError):
            return False

    def _log_mode_warning(
        self, condition_desc: str, fallback_desc: str, smart_sh_mode, smart_dhw_mode
    ):
        """Log a warning for mode validation issues."""
        _LOGGER.warning(
            "%s for %s (hpid=%s): smart_sh_mode=%s, smart_dhw_mode=%s. %s.",
            condition_desc,
            self._metric_key,
            self._hpid,
            smart_sh_mode,
            smart_dhw_mode,
            fallback_desc,
        )

    async def async_select_option(self, option: str) -> None:
        """Update the current value."""
        # Map option to smart control modes
        mode_value = int(option)
        sh_mode = mode_value
        dhw_mode = mode_value

        response = await self.coordinator.api.set_smartcontrol(
            self._hpid, sh_mode, dhw_mode
        )
        # Handle response for use_adaptive
        use_adaptive_value = option != "-1"
        await handle_setting_update_response(
            response, self.coordinator, "metrics", self._metric_key, use_adaptive_value
        )

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        if not self.coordinator.data:
            return None

        metrics = self.coordinator.data.get("metrics", {})
        use_adaptive = metrics.get(self._metric_key)

        _LOGGER.debug(
            "Current option calculation for %s: use_adaptive=%s",
            self._metric_key,
            use_adaptive,
        )

        if use_adaptive is False:
            return "-1"  # Off

        smart_sh_mode = metrics.get("smart_sh_mode")
        smart_dhw_mode = metrics.get("smart_dhw_mode")
        _LOGGER.debug(
            "Determining current option: smart_sh_mode=%s, smart_dhw_mode=%s",
            smart_sh_mode,
            smart_dhw_mode,
        )

        # Normal, consistent cases where both modes match
        if smart_sh_mode == 0 and smart_dhw_mode == 0:
            return "0"  # Eco
        if smart_sh_mode == 1 and smart_dhw_mode == 1:
            return "1"  # Balanced
        if smart_sh_mode == 2 and smart_dhw_mode == 2:
            return "2"  # Comfort

        # Handle inconsistent or partially missing modes while use_adaptive is enabled.
        # Prefer a valid smart_sh_mode, then smart_dhw_mode, and log a warning so that
        # the inconsistency can be diagnosed rather than silently reporting "Off".
        valid_modes = {0, 1, 2}

        sh_valid = self._is_valid_mode(smart_sh_mode, valid_modes)
        dhw_valid = self._is_valid_mode(smart_dhw_mode, valid_modes)

        if sh_valid and dhw_valid and smart_sh_mode != smart_dhw_mode:
            self._log_mode_warning(
                "Mismatched SmartControl modes",
                "Falling back to smart_sh_mode",
                smart_sh_mode,
                smart_dhw_mode,
            )
            return str(smart_sh_mode)

        if sh_valid and not dhw_valid:
            self._log_mode_warning(
                "Missing or invalid smart_dhw_mode",
                "Falling back to smart_sh_mode",
                smart_sh_mode,
                smart_dhw_mode,
            )
            return str(smart_sh_mode)

        if dhw_valid and not sh_valid:
            self._log_mode_warning(
                "Missing or invalid smart_sh_mode",
                "Falling back to smart_dhw_mode",
                smart_sh_mode,
                smart_dhw_mode,
            )
            return str(smart_dhw_mode)

        _LOGGER.warning(
            "Unable to determine SmartControl mode for %s (hpid=%s): "
            "use_adaptive=True but smart_sh_mode=%s, smart_dhw_mode=%s. "
            "Reporting Off as a fallback.",
            self._metric_key,
            self._hpid,
            smart_sh_mode,
            smart_dhw_mode,
        )
        return "-1"  # Off (fallback when data is inconsistent/invalid)

    @property
    def available(self):
        """Check if data is available."""
        if not self.coordinator.data:
            return False

        return (
            self._metric_key in self.coordinator.data.get("metrics", {})
            and self.coordinator.data.get("metrics", {}).get(self._metric_key)
            is not None
        )
