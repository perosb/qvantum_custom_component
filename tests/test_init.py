"""Tests for Qvantum integration setup."""

import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Import real functions before mocking
from custom_components.qvantum import async_setup_entry, async_unload_entry
from custom_components.qvantum.api import QvantumAPI

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
        # Setup hass.data
        hass.data["qvantum"] = {mock_config_entry.entry_id: MagicMock()}

        result = await async_unload_entry(hass, mock_config_entry)

        assert result is True
        hass.config_entries.async_unload_platforms.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_unload_entry_with_firmware_notifications(
        self, hass, mock_config_entry
    ):
        """Test unloading the integration clears firmware notifications."""
        # Setup mock platforms
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        # Setup hass.data
        hass.data["qvantum"] = {mock_config_entry.entry_id: MagicMock()}

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
