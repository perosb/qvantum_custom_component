"""Tests for Qvantum integration setup."""

import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Import real functions before mocking
from custom_components.qvantum import (
    async_setup_entry,
    async_unload_entry,
    async_remove_config_entry_device,
    async_migrate_entry,
    PLATFORMS,
    _async_update_listener,
    _async_modbus_unit,
    _coordinator_device,
    _has_required_device,
    _device_sw_version,
    _modbus_link_settings,
    _async_sync_extra_hot_water_service,
)

# Mock HA imports after importing real functions
# sys.modules['custom_components.qvantum'] = MagicMock()


class TestSetupDeviceRequirements:
    """Unit tests for setup device-identity helpers."""

    def test_modbus_link_settings_from_options(self):
        entry = MagicMock()
        entry.options = {
            "modbus_tcp": True,
            "modbus_host": "hp.local",
            "modbus_port": 1502,
            "modbus_unit_id": 7,
        }
        entry.data = {}
        assert _modbus_link_settings(entry) == (True, "hp.local", 1502, 7)

    @pytest.mark.asyncio
    async def test_sync_extra_hot_water_registers_for_cloud_entry(self, hass):
        cloud = MagicMock()
        cloud.entry_id = "cloud"
        cloud.options = {}
        cloud.data = {}
        hass.config_entries.async_entries = MagicMock(return_value=[cloud])
        hass.services.has_service = MagicMock(return_value=False)
        with patch(
            "custom_components.qvantum.async_setup_services", new_callable=AsyncMock
        ) as setup:
            await _async_sync_extra_hot_water_service(hass)
        setup.assert_awaited_once_with(hass)
        hass.services.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_extra_hot_water_removes_when_only_modbus_remains(self, hass):
        modbus = MagicMock()
        modbus.entry_id = "modbus"
        modbus.options = {"modbus_tcp": True}
        modbus.data = {}
        hass.config_entries.async_entries = MagicMock(return_value=[modbus])
        hass.services.has_service = MagicMock(return_value=True)
        with patch(
            "custom_components.qvantum.async_setup_services", new_callable=AsyncMock
        ) as setup:
            await _async_sync_extra_hot_water_service(hass)
        setup.assert_not_called()
        hass.services.async_remove.assert_called_once_with("qvantum", "extra_hot_water")

    def test_async_modbus_unit_http_mode_skips_shared_connection(self, hass):
        entry = MagicMock()
        entry.options = {}
        entry.data = {}
        assert _async_modbus_unit(hass, entry) is None

    def test_async_modbus_unit_asks_home_assistant_modbus(self, hass):
        """Borrow a unit via the lazy HA modbus import, without loading serial deps."""
        entry = MagicMock()
        entry.options = {"modbus_tcp": True, "modbus_host": "hp.local"}
        entry.data = {}
        unit = MagicMock()
        get_unit = MagicMock(return_value=unit)
        fake_modbus = types.ModuleType("homeassistant.components.modbus")
        fake_modbus.async_get_unit = get_unit
        with patch.dict(sys.modules, {"homeassistant.components.modbus": fake_modbus}):
            result = _async_modbus_unit(hass, entry)
        assert result is unit
        assert get_unit.call_args.args[0] is hass
        assert get_unit.call_args.args[1] is entry
        assert get_unit.call_args.args[3] == 1

    def test_has_required_device_http_needs_metadata(self):
        assert _has_required_device({"id": "abc"}, require_metadata=True) is False
        assert (
            _has_required_device(
                {"id": "abc", "device_metadata": {"display_fw_version": "1"}},
                require_metadata=True,
            )
            is True
        )

    def test_has_required_device_modbus_needs_id_only(self):
        assert _has_required_device({}, require_metadata=False) is False
        assert _has_required_device({"id": "abc"}, require_metadata=False) is True

    def test_device_sw_version_omits_empty(self):
        assert _device_sw_version({}) is None
        assert _device_sw_version({"display_fw_version": "1.0"}) == "1.0//"

    def test_coordinator_device_handles_missing_or_invalid_payload(self):
        coordinator = MagicMock()
        coordinator.data = None
        assert _coordinator_device(coordinator) == {}

        coordinator.data = "not-a-dict"
        assert _coordinator_device(coordinator) == {}

        coordinator.data = {"device": "not-a-dict"}
        assert _coordinator_device(coordinator) == {}

        coordinator.data = {"device": {"id": "abc"}}
        assert _coordinator_device(coordinator) == {"id": "abc"}


class TestIntegrationSetup:
    """Test the integration setup functions."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_cloud_requires_credentials(
        self, hass, mock_config_entry
    ):
        """Cloud setup must fail fast when username or password is missing."""
        mock_config_entry.data = {}
        mock_config_entry.options = {}
        with pytest.raises(KeyError):
            await async_setup_entry(hass, mock_config_entry)

    @pytest.mark.asyncio
    async def test_async_setup_entry_success(
        self, hass, mock_config_entry, mock_api, mock_coordinator
    ):
        """Test successful setup of the integration."""
        # Mock the coordinator creation and device data
        mock_coordinator.data = {
            "device": {
                "id": "test_device",
                "model": "QE-6",
                "vendor": "Qvantum",
                "device_metadata": {"uptime_hours": 100},
            },
            "metrics": {},
            "settings": {},
        }

        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_config_entry_first_refresh = AsyncMock()

        mock_config_entry.add_update_listener = MagicMock()

        with (
            patch("custom_components.qvantum.QvantumAPI", return_value=mock_api),
            patch(
                "custom_components.qvantum.QvantumDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.qvantum.QvantumMaintenanceCoordinator",
                return_value=mock_firmware_coordinator,
            ),
            patch("custom_components.qvantum.services.async_setup_services"),
        ):
            result = await async_setup_entry(hass, mock_config_entry)

            assert result is True
            # Verify that all platforms are forwarded
            hass.config_entries.async_forward_entry_setups.assert_called_once_with(
                mock_config_entry, PLATFORMS
            )
            mock_config_entry.add_update_listener.assert_called_once_with(
                _async_update_listener
            )

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_device_data(
        self, hass, mock_config_entry, mock_api, mock_coordinator
    ):
        """Test setup failure when no device data is available."""
        with (
            patch("custom_components.qvantum.QvantumAPI", return_value=mock_api),
            patch(
                "custom_components.qvantum.QvantumDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.qvantum.QvantumMaintenanceCoordinator",
                return_value=MagicMock(),
            ),
            patch("custom_components.qvantum.services.async_setup_services"),
        ):
            # Mock missing device data
            mock_api.get_primary_device = AsyncMock(return_value=None)

            result = await async_setup_entry(hass, mock_config_entry)

            assert result is False

    @pytest.mark.asyncio
    async def test_async_unload_entry(self, hass, mock_config_entry):
        """Test unloading the integration."""
        # Setup mock platforms
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        # Setup hass.data as API object
        mock_api = MagicMock()
        mock_api.close = AsyncMock()
        hass.data["qvantum"] = mock_api
        mock_config_entry.runtime_data = None

        result = await async_unload_entry(hass, mock_config_entry)

        assert result is True
        hass.config_entries.async_unload_platforms.assert_called_once()
        mock_api.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_unload_entry_shuts_down_coordinators_before_api_close(
        self, hass, mock_config_entry
    ):
        """Coordinators must stop before API close to avoid Modbus/session races."""
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        mock_api = MagicMock()
        mock_api.close = AsyncMock()
        hass.data["qvantum"] = mock_api

        call_order: list[str] = []

        mock_main_coordinator = MagicMock()
        mock_main_coordinator._device = {"id": "test_device_123"}

        async def shutdown_main():
            call_order.append("main_shutdown")

        async def shutdown_maint():
            call_order.append("maint_shutdown")

        async def close_api():
            call_order.append("api_close")

        mock_main_coordinator.async_shutdown = AsyncMock(side_effect=shutdown_main)
        mock_maint_coordinator = MagicMock()
        mock_maint_coordinator.async_shutdown = AsyncMock(side_effect=shutdown_maint)
        mock_api.close = AsyncMock(side_effect=close_api)

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_main_coordinator
        mock_config_entry.runtime_data.maintenance_coordinator = mock_maint_coordinator

        with patch("custom_components.qvantum.async_dismiss", new_callable=AsyncMock):
            result = await async_unload_entry(hass, mock_config_entry)

        assert result is True
        assert call_order == ["maint_shutdown", "main_shutdown", "api_close"]
        mock_main_coordinator.async_shutdown.assert_awaited_once()
        mock_maint_coordinator.async_shutdown.assert_awaited_once()
        mock_api.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_unload_entry_with_firmware_notifications(
        self, hass, mock_config_entry
    ):
        """Test unloading the integration clears firmware notifications."""
        # Setup mock platforms
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        mock_api = MagicMock()
        mock_api.close = AsyncMock()
        hass.data["qvantum"] = mock_api

        # Mock main coordinator with device data (used for notification cleanup)
        mock_main_coordinator = MagicMock()
        mock_main_coordinator._device = {"id": "test_device_123"}
        mock_main_coordinator.async_shutdown = AsyncMock()
        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_shutdown = AsyncMock()

        # Add runtime_data to config entry
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_main_coordinator
        mock_config_entry.runtime_data.maintenance_coordinator = (
            mock_firmware_coordinator
        )

        with patch(
            "custom_components.qvantum.async_dismiss", new_callable=AsyncMock
        ) as mock_async_dismiss:
            result = await async_unload_entry(hass, mock_config_entry)

            assert result is True
            hass.config_entries.async_unload_platforms.assert_called_once()
            mock_api.close.assert_awaited_once()
            mock_main_coordinator.async_shutdown.assert_awaited_once()
            mock_firmware_coordinator.async_shutdown.assert_awaited_once()

            # Verify async_dismiss was called for each firmware component
            expected_calls = [
                "qvantum_firmware_update_test_device_123_display_fw_version",
                "qvantum_firmware_update_test_device_123_cc_fw_version",
                "qvantum_firmware_update_test_device_123_inv_fw_version",
            ]
            assert mock_async_dismiss.call_count == 3
            # Ensure exact matching - all expected notifications called exactly once
            actual_calls = [call[0][1] for call in mock_async_dismiss.call_args_list]
            assert set(actual_calls) == set(expected_calls)

    @pytest.mark.asyncio
    async def test_async_unload_entry_with_nonawaitable_dismiss(
        self, hass, mock_config_entry
    ):
        """Test unloading handles non-awaitable async_dismiss safely."""
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        mock_main_coordinator = MagicMock()
        mock_main_coordinator._device = {"id": "test_device_123"}
        mock_main_coordinator.async_shutdown = AsyncMock()
        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_shutdown = AsyncMock()

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_main_coordinator
        mock_config_entry.runtime_data.maintenance_coordinator = (
            mock_firmware_coordinator
        )

        with patch(
            "custom_components.qvantum.async_dismiss", MagicMock(return_value=None)
        ):
            result = await async_unload_entry(hass, mock_config_entry)

            assert result is True
            hass.config_entries.async_unload_platforms.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_update_listener_reloads_without_runtime(
        self, hass, mock_config_entry
    ):
        """Missing runtime_data still forces a full reload."""
        hass.config_entries.async_reload = AsyncMock()
        mock_config_entry.runtime_data = None

        await _async_update_listener(hass, mock_config_entry)

        hass.config_entries.async_reload.assert_called_once_with(
            mock_config_entry.entry_id
        )

    @pytest.mark.asyncio
    async def test_async_update_listener_applies_interval_without_reload(
        self, hass, mock_config_entry
    ):
        """Interval-only option changes must not tear down Modbus."""
        hass.config_entries.async_reload = AsyncMock()

        mock_api = MagicMock()
        mock_api._modbus_tcp = True
        mock_api._modbus_host = "Qvantum-HP"
        mock_api._modbus_port = 502
        mock_api._modbus_unit_id = 1

        mock_coordinator = MagicMock()
        mock_coordinator.modbus_enabled = True
        mock_coordinator.api = mock_api
        mock_coordinator.poll_interval = 15
        mock_coordinator.apply_poll_interval = MagicMock(return_value=True)

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.options = {
            "modbus_tcp": True,
            "modbus_host": "Qvantum-HP",
            "modbus_scan_interval": 10,
        }
        mock_config_entry.data = {}
        mock_config_entry.title = "Qvantum"

        await _async_update_listener(hass, mock_config_entry)

        hass.config_entries.async_reload.assert_not_called()
        mock_coordinator.apply_poll_interval.assert_called_once_with(mock_config_entry)

    @pytest.mark.asyncio
    async def test_async_update_listener_reloads_on_modbus_host_change(
        self, hass, mock_config_entry
    ):
        """Changing Modbus host still requires a full reload."""
        hass.config_entries.async_reload = AsyncMock()

        mock_api = MagicMock()
        mock_api._modbus_tcp = True
        mock_api._modbus_host = "old-host"
        mock_api._modbus_port = 502
        mock_api._modbus_unit_id = 1

        mock_coordinator = MagicMock()
        mock_coordinator.modbus_enabled = True
        mock_coordinator.api = mock_api
        mock_coordinator.apply_poll_interval = MagicMock()

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.options = {
            "modbus_tcp": True,
            "modbus_host": "new-host",
            "modbus_scan_interval": 10,
        }
        mock_config_entry.data = {}

        await _async_update_listener(hass, mock_config_entry)

        hass.config_entries.async_reload.assert_called_once_with(
            mock_config_entry.entry_id
        )
        mock_coordinator.apply_poll_interval.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_update_listener_reloads_on_modbus_toggle(
        self, hass, mock_config_entry
    ):
        """Enabling/disabling Modbus still requires a full reload."""
        hass.config_entries.async_reload = AsyncMock()

        mock_api = MagicMock()
        mock_api._modbus_tcp = False
        mock_api._modbus_host = "Qvantum-HP"
        mock_api._modbus_port = 502
        mock_api._modbus_unit_id = 1

        mock_coordinator = MagicMock()
        mock_coordinator.modbus_enabled = False
        mock_coordinator.api = mock_api
        mock_coordinator.apply_poll_interval = MagicMock()

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.options = {
            "modbus_tcp": True,
            "modbus_host": "Qvantum-HP",
        }
        mock_config_entry.data = {}

        await _async_update_listener(hass, mock_config_entry)

        hass.config_entries.async_reload.assert_called_once_with(
            mock_config_entry.entry_id
        )
        mock_coordinator.apply_poll_interval.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_update_listener_reloads_on_modbus_port_change(
        self, hass, mock_config_entry
    ):
        """Changing Modbus port still requires a full reload."""
        hass.config_entries.async_reload = AsyncMock()

        mock_api = MagicMock()
        mock_api._modbus_tcp = True
        mock_api._modbus_host = "Qvantum-HP"
        mock_api._modbus_port = 502
        mock_api._modbus_unit_id = 1

        mock_coordinator = MagicMock()
        mock_coordinator.modbus_enabled = True
        mock_coordinator.api = mock_api
        mock_coordinator.apply_poll_interval = MagicMock()

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.options = {
            "modbus_tcp": True,
            "modbus_host": "Qvantum-HP",
            "modbus_port": 1502,
        }
        mock_config_entry.data = {}

        await _async_update_listener(hass, mock_config_entry)

        hass.config_entries.async_reload.assert_called_once_with(
            mock_config_entry.entry_id
        )
        mock_coordinator.apply_poll_interval.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_setup_entry_modbus_without_metadata_succeeds(
        self, hass, mock_config_entry, mock_api, mock_coordinator
    ):
        """Modbus setup should succeed with a device id even if cloud metadata is missing."""
        mock_config_entry.options = {"modbus_tcp": True}
        mock_coordinator.data = {
            "device": {
                "id": "test_device_123",
                "model": "QE-6",
                "vendor": "Qvantum",
            },
            "values": {},
        }
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_restore_dhw_state = AsyncMock()
        mock_config_entry.add_update_listener = MagicMock()

        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_config_entry_first_refresh = AsyncMock()

        with (
            patch(
                "custom_components.qvantum._async_modbus_unit", return_value=MagicMock()
            ),
            patch("custom_components.qvantum.QvantumAPI", return_value=mock_api),
            patch(
                "custom_components.qvantum.QvantumDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.qvantum.QvantumMaintenanceCoordinator",
                return_value=mock_firmware_coordinator,
            ),
            patch("custom_components.qvantum.services.async_setup_services"),
        ):
            result = await async_setup_entry(hass, mock_config_entry)

        assert result is True
        hass.config_entries.async_forward_entry_setups.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_setup_entry_modbus_continues_when_maintenance_http_down(
        self, hass, mock_config_entry, mock_api, mock_coordinator
    ):
        """A down cloud API must not block Modbus setup at firmware-check time."""
        from homeassistant.exceptions import ConfigEntryNotReady

        mock_config_entry.options = {"modbus_tcp": True}
        mock_coordinator.data = {
            "device": {
                "id": "test_device_123",
                "model": "QE-6",
                "vendor": "Qvantum",
                "device_metadata": {"display_fw_version": "1.0"},
            },
            "values": {},
        }
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_restore_dhw_state = AsyncMock()
        mock_config_entry.add_update_listener = MagicMock()

        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryNotReady("HTTP API down")
        )

        with (
            patch(
                "custom_components.qvantum._async_modbus_unit", return_value=MagicMock()
            ),
            patch("custom_components.qvantum.QvantumAPI", return_value=mock_api),
            patch(
                "custom_components.qvantum.QvantumDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.qvantum.QvantumMaintenanceCoordinator",
                return_value=mock_firmware_coordinator,
            ),
            patch("custom_components.qvantum.services.async_setup_services"),
        ):
            result = await async_setup_entry(hass, mock_config_entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_async_setup_entry_modbus_continues_when_maintenance_times_out(
        self, hass, mock_config_entry, mock_api, mock_coordinator
    ):
        """A hung cloud firmware check must not stall Modbus setup."""
        mock_config_entry.options = {"modbus_tcp": True}
        mock_coordinator.data = {
            "device": {
                "id": "test_device_123",
                "model": "QE-6",
                "vendor": "Qvantum",
                "device_metadata": {"display_fw_version": "1.0"},
            },
            "values": {},
        }
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_restore_dhw_state = AsyncMock()
        mock_config_entry.add_update_listener = MagicMock()

        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=TimeoutError()
        )

        with (
            patch(
                "custom_components.qvantum._async_modbus_unit", return_value=MagicMock()
            ),
            patch("custom_components.qvantum.QvantumAPI", return_value=mock_api),
            patch(
                "custom_components.qvantum.QvantumDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.qvantum.QvantumMaintenanceCoordinator",
                return_value=mock_firmware_coordinator,
            ),
            patch("custom_components.qvantum.services.async_setup_services"),
        ):
            result = await async_setup_entry(hass, mock_config_entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_async_setup_entry_requests_shared_modbus_unit(
        self, hass, mock_config_entry, mock_api, mock_coordinator
    ):
        """Modbus setup borrows a unit from Home Assistant instead of opening TCP."""
        mock_config_entry.options = {
            "modbus_tcp": True,
            "modbus_host": "hp.local",
            "modbus_port": 502,
            "modbus_unit_id": 1,
        }
        mock_coordinator.data = {
            "device": {"id": "test_device_123", "model": "QE-6", "vendor": "Qvantum"},
            "values": {},
        }
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_restore_dhw_state = AsyncMock()
        mock_config_entry.add_update_listener = MagicMock()
        unit = MagicMock()
        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_config_entry_first_refresh = AsyncMock()

        with (
            patch(
                "custom_components.qvantum._async_modbus_unit", return_value=unit
            ) as get_unit,
            patch("custom_components.qvantum.QvantumAPI", return_value=mock_api) as api_ctor,
            patch(
                "custom_components.qvantum.QvantumDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.qvantum.QvantumMaintenanceCoordinator",
                return_value=mock_firmware_coordinator,
            ),
            patch("custom_components.qvantum.services.async_setup_services"),
        ):
            result = await async_setup_entry(hass, mock_config_entry)

        assert result is True
        get_unit.assert_called_once_with(hass, mock_config_entry)
        assert api_ctor.call_args.kwargs["modbus_unit"] is unit
        assert api_ctor.call_args.kwargs["modbus_tcp"] is True
        assert api_ctor.call_args.kwargs["modbus_host"] == "hp.local"

    @pytest.mark.asyncio
    async def test_async_setup_entry_modbus_conflict_raises_not_ready(
        self, hass, mock_config_entry
    ):
        """Conflicting link settings on a shared Modbus endpoint fail setup."""
        from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

        mock_config_entry.options = {"modbus_tcp": True, "modbus_host": "hp.local"}

        with patch(
            "custom_components.qvantum._async_modbus_unit",
            side_effect=HomeAssistantError("already in use"),
        ):
            with pytest.raises(ConfigEntryNotReady, match="already in use"):
                await async_setup_entry(hass, mock_config_entry)

    @pytest.mark.asyncio
    async def test_async_setup_entry_http_timeout_raises_not_ready(
        self, hass, mock_config_entry, mock_api, mock_coordinator
    ):
        """HTTP mode must surface a hung firmware check as ConfigEntryNotReady."""
        from homeassistant.exceptions import ConfigEntryNotReady

        mock_config_entry.options = {}
        mock_coordinator.data = {
            "device": {
                "id": "test_device_123",
                "model": "QE-6",
                "vendor": "Qvantum",
                "device_metadata": {"display_fw_version": "1.0"},
            },
            "values": {},
        }
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_restore_dhw_state = AsyncMock()

        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=TimeoutError()
        )

        with (
            patch("custom_components.qvantum.QvantumAPI", return_value=mock_api),
            patch(
                "custom_components.qvantum.QvantumDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.qvantum.QvantumMaintenanceCoordinator",
                return_value=mock_firmware_coordinator,
            ),
            patch("custom_components.qvantum.services.async_setup_services"),
        ):
            with pytest.raises(ConfigEntryNotReady, match="timed out"):
                await async_setup_entry(hass, mock_config_entry)

    @pytest.mark.asyncio
    async def test_async_setup_entry_http_not_ready_propagates(
        self, hass, mock_config_entry, mock_api, mock_coordinator
    ):
        """HTTP mode must not swallow ConfigEntryNotReady from firmware check."""
        from homeassistant.exceptions import ConfigEntryNotReady

        mock_config_entry.options = {}
        mock_coordinator.data = {
            "device": {
                "id": "test_device_123",
                "model": "QE-6",
                "vendor": "Qvantum",
                "device_metadata": {"display_fw_version": "1.0"},
            },
            "values": {},
        }
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_restore_dhw_state = AsyncMock()

        mock_firmware_coordinator = MagicMock()
        mock_firmware_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryNotReady("HTTP API down")
        )

        with (
            patch("custom_components.qvantum.QvantumAPI", return_value=mock_api),
            patch(
                "custom_components.qvantum.QvantumDataUpdateCoordinator",
                return_value=mock_coordinator,
            ),
            patch(
                "custom_components.qvantum.QvantumMaintenanceCoordinator",
                return_value=mock_firmware_coordinator,
            ),
            patch("custom_components.qvantum.services.async_setup_services"),
        ):
            with pytest.raises(ConfigEntryNotReady, match="HTTP API down"):
                await async_setup_entry(hass, mock_config_entry)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_device_metadata_fails(self, hass, mock_config_entry):
        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "device123"})

        mock_coordinator = MagicMock()
        mock_coordinator.data = {"device": {}}
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=[None, None])
        mock_coordinator.async_restore_dhw_state = AsyncMock()

        with patch("custom_components.qvantum.QvantumAPI", return_value=mock_api), \
            patch("custom_components.qvantum.QvantumDataUpdateCoordinator", return_value=mock_coordinator), \
            patch("custom_components.qvantum.QvantumMaintenanceCoordinator", return_value=MagicMock()), \
            patch("custom_components.qvantum.services.async_setup_services"):
            result = await async_setup_entry(hass, mock_config_entry)

        assert result is False


    @pytest.mark.asyncio
    async def test_async_remove_config_entry_device(self, hass, mock_config_entry):
        result = await async_remove_config_entry_device(
            hass, mock_config_entry, MagicMock()
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_async_migrate_entry_non_legacy(self, hass, mock_config_entry):
        config_entry = MagicMock(version=7, minor_version=0)

        result = await async_migrate_entry(hass, config_entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_async_migrate_entry_future_version(self, hass, mock_config_entry):
        config_entry = MagicMock(version=99, minor_version=0)

        result = await async_migrate_entry(hass, config_entry)

        assert result is False

    @pytest.mark.asyncio
    async def test_async_migrate_entry_from_v4_to_v5(self, hass, mock_config_entry):
        config_entry = MagicMock(version=4, minor_version=0, entry_id="test")

        mock_ent_reg = MagicMock()
        mock_ent_reg.entities.values.return_value = []

        with patch(
            "custom_components.qvantum.async_migrate_entries",
            new_callable=AsyncMock,
        ) as mock_migrate:
            with patch(
                "custom_components.qvantum.async_get_entity_registry",
                return_value=mock_ent_reg,
            ):
                with patch.object(
                    hass.config_entries, "async_update_entry"
                ) as mock_update:
                    result = await async_migrate_entry(hass, config_entry)

                    assert result is True
                    # v5 numbers, v6, v7 (v5 sensors now uses entity registry directly)
                    assert mock_migrate.call_count == 3
                    mock_update.assert_called_once_with(config_entry, version=7)

    @pytest.mark.asyncio
    async def test_async_migrate_entry_legacy(self, hass, mock_config_entry):
        config_entry = MagicMock(version=1, minor_version=0, entry_id="test")

        mock_ent_reg = MagicMock()
        mock_ent_reg.entities.values.return_value = []

        with patch(
            "custom_components.qvantum.async_migrate_entries",
            new_callable=AsyncMock,
        ) as mock_migrate:
            with patch(
                "custom_components.qvantum.async_get_entity_registry",
                return_value=mock_ent_reg,
            ):
                with patch.object(
                    hass.config_entries, "async_update_entry"
                ) as mock_update:
                    result = await async_migrate_entry(hass, config_entry)

                    assert result is True
                    # v1, v5 numbers, v6, v7 (v5 sensors now uses entity registry directly)
                    assert mock_migrate.call_count == 4
                    mock_update.assert_called_once_with(config_entry, version=7)

                    # Verify migration calls were made with correct arguments
                    assert len(mock_migrate.call_args_list) == 4

                    first_call_args = mock_migrate.call_args_list[0].args
                    _, first_entry_id, first_migration_fn = first_call_args
                    assert first_entry_id == config_entry.entry_id
                    assert callable(first_migration_fn)

                    second_call_args = mock_migrate.call_args_list[1].args
                    _, second_entry_id, second_migration_fn = second_call_args
                    assert second_entry_id == config_entry.entry_id
                    assert callable(second_migration_fn)


class TestMigrateToV5Callbacks:
    """Test the v5 migration callback functions directly by capturing them."""

    def _make_entity_entry(self, domain, unique_id, entity_id=None):
        """Build a minimal mock entity entry."""
        entry = MagicMock()
        entry.domain = domain
        entry.unique_id = unique_id
        entry.entity_id = entity_id or f"{domain}.qvantum_test"
        return entry

    async def _capture_v5_callbacks(self, hass, version=4):
        """Run async_migrate_entry for a version-*version* entry and return the
        list of callback functions passed to async_migrate_entries."""
        config_entry = MagicMock(version=version, minor_version=0, entry_id="test")
        captured = []

        async def capture_migrate(h, entry_id, fn):
            captured.append(fn)

        mock_ent_reg = MagicMock()
        mock_ent_reg.entities.values.return_value = []

        with patch(
            "custom_components.qvantum.async_migrate_entries",
            side_effect=capture_migrate,
        ):
            with patch(
                "custom_components.qvantum.async_get_entity_registry",
                return_value=mock_ent_reg,
            ):
                with patch.object(hass.config_entries, "async_update_entry"):
                    await async_migrate_entry(hass, config_entry)

        return captured

    # ------------------------------------------------------------------
    # migrate_to_v5_number_unique_ids  (first v5 pass, index 0 from v4)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_v5_number_cb_renames_dhw_normal_start(self, hass):
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]  # first pass: numbers

        entry = self._make_entity_entry(
            "number", "qvantum_dhw_normal_start_1011074250800138"
        )
        result = cb(entry)

        assert result == {
            "new_unique_id": "qvantum_number_tap_water_start_1011074250800138"
        }

    @pytest.mark.asyncio
    async def test_v5_number_cb_renames_dhw_normal_stop(self, hass):
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]

        entry = self._make_entity_entry(
            "number", "qvantum_dhw_normal_stop_1011074250800138"
        )
        result = cb(entry)

        assert result == {
            "new_unique_id": "qvantum_number_tap_water_stop_1011074250800138"
        }

    @pytest.mark.asyncio
    async def test_v5_number_cb_prefixes_plain_number_entity(self, hass):
        """A number entity without a dhw rename still gets the qvantum_number_ prefix."""
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]

        entry = self._make_entity_entry(
            "number", "qvantum_tap_water_cap_1011074250800138"
        )
        result = cb(entry)

        assert result == {
            "new_unique_id": "qvantum_number_tap_water_cap_1011074250800138"
        }

    @pytest.mark.asyncio
    async def test_v5_number_cb_skips_already_prefixed(self, hass):
        """Already-prefixed entries must not be modified."""
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]

        entry = self._make_entity_entry(
            "number", "qvantum_number_tap_water_start_1011074250800138"
        )
        result = cb(entry)

        assert result is None

    @pytest.mark.asyncio
    async def test_v5_number_cb_skips_non_number_domains(self, hass):
        """Sensor entities must be ignored by the number pre-migration pass."""
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]

        entry = self._make_entity_entry(
            "sensor", "qvantum_dhw_normal_start_1011074250800138"
        )
        result = cb(entry)

        assert result is None

    # ------------------------------------------------------------------
    # v5 sensor entity migration via entity registry (direct, not callback)
    # ------------------------------------------------------------------

    def _make_ent_reg_entry(
        self, domain, unique_id, entity_id=None, config_entry_id="test"
    ):
        """Create a minimal mock entity registry entry."""
        entry = MagicMock()
        entry.domain = domain
        entry.unique_id = unique_id
        entry.entity_id = entity_id or f"{domain}.qvantum_test"
        entry.config_entry_id = config_entry_id
        return entry

    async def _run_v5_sensor_migration(self, hass, entities):
        """Run async_migrate_entry for v4 with a mocked entity registry
        and return the registry mock."""
        mock_ent_reg = MagicMock()
        mock_ent_reg.entities.values.return_value = entities

        config_entry = MagicMock(version=4, minor_version=0, entry_id="test")
        with patch(
            "custom_components.qvantum.async_migrate_entries", new_callable=AsyncMock
        ):
            with patch(
                "custom_components.qvantum.async_get_entity_registry",
                return_value=mock_ent_reg,
            ):
                with patch.object(hass.config_entries, "async_update_entry"):
                    await async_migrate_entry(hass, config_entry)
        return mock_ent_reg

    @pytest.mark.asyncio
    async def test_v5_sensor_renames_dhw_normal_start(self, hass):
        """Sensor with dhw_normal_start unique_id is renamed to tap_water_start."""
        entity = self._make_ent_reg_entry(
            "sensor",
            "qvantum_dhw_normal_start_1011074250800138",
            entity_id="sensor.qvantum_hot_water_tank_lower_limit",
        )
        ent_reg = await self._run_v5_sensor_migration(hass, [entity])

        ent_reg.async_update_entity.assert_called_once_with(
            "sensor.qvantum_hot_water_tank_lower_limit",
            new_unique_id="qvantum_tap_water_start_1011074250800138",
        )
        ent_reg.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_v5_sensor_renames_dhw_normal_stop(self, hass):
        """Sensor with dhw_normal_stop unique_id is renamed to tap_water_stop."""
        entity = self._make_ent_reg_entry(
            "sensor",
            "qvantum_dhw_normal_stop_1011074250800138",
            entity_id="sensor.qvantum_hot_water_tank_upper_limit",
        )
        ent_reg = await self._run_v5_sensor_migration(hass, [entity])

        ent_reg.async_update_entity.assert_called_once_with(
            "sensor.qvantum_hot_water_tank_upper_limit",
            new_unique_id="qvantum_tap_water_stop_1011074250800138",
        )
        ent_reg.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_v5_sensor_skips_number_domain(self, hass):
        """Number entities are skipped by the sensor rename pass."""
        entity = self._make_ent_reg_entry(
            "number", "qvantum_dhw_normal_start_1011074250800138"
        )
        ent_reg = await self._run_v5_sensor_migration(hass, [entity])

        ent_reg.async_update_entity.assert_not_called()
        ent_reg.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_v5_sensor_no_change_when_no_dhw_key(self, hass):
        """Sensors without dhw_normal keys are left untouched."""
        entity = self._make_ent_reg_entry("sensor", "qvantum_bt1_1011074250800138")
        ent_reg = await self._run_v5_sensor_migration(hass, [entity])

        ent_reg.async_update_entity.assert_not_called()
        ent_reg.async_remove.assert_not_called()

    # ------------------------------------------------------------------
    # Collision scenario: stale sensor removed when target unique_id taken
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_v5_sensor_removes_orphan_when_target_uid_taken(self, hass):
        """When both a stale entity (dhw_normal_start) and an active entity
        already holding the target unique_id (tap_water_start) exist, the stale
        entity is removed rather than causing a ValueError."""
        device_id = "1011074250800138"

        # Active sensor already has the target unique_id
        active = self._make_ent_reg_entry(
            "sensor",
            f"qvantum_tap_water_start_{device_id}",
            entity_id="sensor.qvantum_hot_water_tank_lower_temperature_limit",
        )
        # Stale/orphaned sensor still has the old dhw unique_id
        stale = self._make_ent_reg_entry(
            "sensor",
            f"qvantum_dhw_normal_start_{device_id}",
            entity_id="sensor.qvantum_hot_water_tank_lower_limit",
        )

        ent_reg = await self._run_v5_sensor_migration(hass, [active, stale])

        # The orphan must be removed, not renamed
        ent_reg.async_remove.assert_called_once_with(
            "sensor.qvantum_hot_water_tank_lower_limit"
        )
        # The active entity must remain in the registry and not be removed
        assert active.entity_id not in [
            call.args[0] for call in ent_reg.async_remove.call_args_list
        ]
        # The active entity must not be touched
        ent_reg.async_update_entity.assert_not_called()
