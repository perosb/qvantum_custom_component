"""Tests for Qvantum integration setup."""

import sys
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
)

# Mock HA imports after importing real functions
# sys.modules['custom_components.qvantum'] = MagicMock()


class TestIntegrationSetup:
    """Test the integration setup functions."""

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

        result = await async_unload_entry(hass, mock_config_entry)

        assert result is True
        hass.config_entries.async_unload_platforms.assert_called_once()
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

        # Mock firmware coordinator with device data
        mock_firmware_coordinator = MagicMock()
        mock_main_coordinator = MagicMock()
        mock_main_coordinator._device = {"id": "test_device_123"}
        mock_firmware_coordinator.main_coordinator = mock_main_coordinator

        # Add runtime_data to config entry
        mock_config_entry.runtime_data = MagicMock()
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

        mock_firmware_coordinator = MagicMock()
        mock_main_coordinator = MagicMock()
        mock_main_coordinator._device = {"id": "test_device_123"}
        mock_firmware_coordinator.main_coordinator = mock_main_coordinator

        mock_config_entry.runtime_data = MagicMock()
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
    async def test_async_update_listener(self, hass, mock_config_entry):
        hass.config_entries.async_reload = AsyncMock()

        await _async_update_listener(hass, mock_config_entry)

        hass.config_entries.async_reload.assert_called_once_with(mock_config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_device_metadata_fails(self, hass, mock_config_entry):
        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "device123"})

        mock_coordinator = MagicMock()
        mock_coordinator.data = {"device": {}}
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=[None, None])

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
        config_entry = MagicMock(version=6, minor_version=0)

        result = await async_migrate_entry(hass, config_entry)

        assert result is False

    @pytest.mark.asyncio
    async def test_async_migrate_entry_from_v4_to_v5(self, hass, mock_config_entry):
        config_entry = MagicMock(version=4, minor_version=0, entry_id="test")

        with patch(
            "custom_components.qvantum.async_migrate_entries",
            new_callable=AsyncMock,
        ) as mock_migrate:
            with patch.object(hass.config_entries, "async_update_entry") as mock_update:
                result = await async_migrate_entry(hass, config_entry)

                assert result is True
                mock_migrate.assert_called_once()
                mock_update.assert_called_once_with(config_entry, version=5)

    @pytest.mark.asyncio
    async def test_async_migrate_entry_legacy(self, hass, mock_config_entry):
        config_entry = MagicMock(version=1, minor_version=0, entry_id="test")

        with patch(
            "custom_components.qvantum.async_migrate_entries",
            new_callable=AsyncMock,
        ) as mock_migrate:
            with patch.object(hass.config_entries, "async_update_entry") as mock_update:
                result = await async_migrate_entry(hass, config_entry)

                assert result is True
                assert mock_migrate.call_count == 2
                mock_update.assert_called_once_with(config_entry, version=5)

                # Verify both migration calls were made with correct arguments
                assert len(mock_migrate.call_args_list) == 2

                first_call_args = mock_migrate.call_args_list[0].args
                _, first_entry_id, first_migration_fn = first_call_args
                assert first_entry_id == config_entry.entry_id
                assert callable(first_migration_fn)

                second_call_args = mock_migrate.call_args_list[1].args
                _, second_entry_id, second_migration_fn = second_call_args
                assert second_entry_id == config_entry.entry_id
                assert callable(second_migration_fn)
                assert second_entry_id == config_entry.entry_id
                assert callable(second_migration_fn)
