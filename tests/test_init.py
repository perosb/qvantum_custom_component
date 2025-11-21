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
    async def test_async_setup_entry_success(self, hass, mock_config_entry, mock_api, mock_coordinator):
        """Test successful setup of the integration."""
        # Mock the coordinator creation and device data
        mock_coordinator.data = {
            "device": {
                "id": "test_device",
                "model": "QE-6",
                "vendor": "Qvantum",
                "device_metadata": {"uptime_hours": 100}
            },
            "metrics": {},
            "settings": {}
        }

        with patch('custom_components.qvantum.QvantumAPI', return_value=mock_api), \
             patch('custom_components.qvantum.QvantumDataUpdateCoordinator', return_value=mock_coordinator), \
             patch('custom_components.qvantum.services.async_setup_services'):

            result = await async_setup_entry(hass, mock_config_entry)

            assert result is True

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_device_data(self, hass, mock_config_entry, mock_api, mock_coordinator):
        """Test setup failure when no device data is available."""
        with patch('custom_components.qvantum.QvantumAPI', return_value=mock_api), \
             patch('custom_components.qvantum.QvantumDataUpdateCoordinator', return_value=mock_coordinator), \
             patch('custom_components.qvantum.services.async_setup_services'):

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