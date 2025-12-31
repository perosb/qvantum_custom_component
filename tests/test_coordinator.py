"""Tests for Qvantum coordinator functions."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.qvantum.coordinator import (
    handle_setting_update_response,
    QvantumDataUpdateCoordinator,
)
from custom_components.qvantum.const import (
    DEFAULT_ENABLED_METRICS,
    DEFAULT_DISABLED_METRICS,
    DOMAIN,
)


class TestHandleSettingUpdateResponse:
    """Test the handle_setting_update_response function."""

    @pytest.mark.asyncio
    async def test_handle_setting_update_success(self):
        """Test successful setting update response handling."""
        coordinator = MagicMock()
        coordinator.data = {"settings": {"old_key": "old_value"}}
        coordinator.async_set_updated_data = MagicMock()
        coordinator.async_refresh = AsyncMock()

        api_response = {"status": "APPLIED"}

        await handle_setting_update_response(
            api_response, coordinator, "settings", "new_key", "new_value"
        )

        assert coordinator.data["settings"]["new_key"] == "new_value"
        coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
        # No immediate refresh to avoid overwriting the update

    @pytest.mark.asyncio
    async def test_handle_setting_update_no_response(self):
        """Test setting update with no response."""
        coordinator = MagicMock()

        await handle_setting_update_response(
            None, coordinator, "settings", "key", "value"
        )

        # Should not update data or refresh
        coordinator.async_set_updated_data.assert_not_called()
        coordinator.async_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_setting_update_wrong_status(self):
        """Test setting update with wrong status."""
        coordinator = MagicMock()

        api_response = {"status": "FAILED"}

        await handle_setting_update_response(
            api_response, coordinator, "settings", "key", "value"
        )

        # Should not update data or refresh
        coordinator.async_set_updated_data.assert_not_called()
        coordinator.async_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_setting_update_no_data_section(self):
        """Test setting update with no data section."""
        coordinator = MagicMock()
        coordinator.async_refresh = AsyncMock()

        api_response = {"status": "APPLIED"}

        await handle_setting_update_response(
            api_response, coordinator, None, "key", "value"
        )

        # Should not update data or refresh
        coordinator.async_set_updated_data.assert_not_called()
        # No refresh called

    @pytest.mark.asyncio
    async def test_handle_setting_update_no_key(self):
        """Test setting update with no key."""
        coordinator = MagicMock()
        coordinator.async_refresh = AsyncMock()

        api_response = {"status": "APPLIED"}

        await handle_setting_update_response(
            api_response, coordinator, "settings", None, "value"
        )

        # Should not update data or refresh
        coordinator.async_set_updated_data.assert_not_called()
        # No refresh called


class TestQvantumDataUpdateCoordinator:
    """Test the QvantumDataUpdateCoordinator class."""

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @patch("homeassistant.helpers.device_registry.async_get", new_callable=AsyncMock)
    @patch("homeassistant.helpers.entity_registry.async_get", new_callable=AsyncMock)
    async def test_get_enabled_metrics_with_device_found(
        self, mock_entity_async_get, mock_device_async_get, mock_super_init
    ):
        """Test _get_enabled_metrics when device is found in registry."""
        # Create mock registries
        mock_device_registry = MagicMock()
        mock_entity_registry = MagicMock()
        mock_device_async_get.return_value = mock_device_registry
        mock_entity_async_get.return_value = mock_entity_registry

        # Mock device
        mock_device = MagicMock()
        mock_device.id = "device_id_123"
        mock_device.identifiers = {(DOMAIN, "qvantum-test_device")}
        mock_device_registry.devices.values.return_value = [mock_device]

        # Mock entities
        mock_entity1 = MagicMock()
        mock_entity1.device_id = "device_id_123"
        mock_entity1.disabled_by = None
        mock_entity1.unique_id = "qvantum_bt1_test_device"

        mock_entity2 = MagicMock()
        mock_entity2.device_id = "device_id_123"
        mock_entity2.disabled_by = None
        mock_entity2.unique_id = "qvantum_bt2_test_device"

        mock_entity3 = MagicMock()  # Disabled entity
        mock_entity3.device_id = "device_id_123"
        mock_entity3.disabled_by = "user"
        mock_entity3.unique_id = "qvantum_bt3_test_device"

        mock_entity_registry.entities.values.return_value = [
            mock_entity1,
            mock_entity2,
            mock_entity3,
        ]

        # Create mock hass
        mock_hass = MagicMock()
        mock_api = MagicMock()
        mock_hass.data = {DOMAIN: mock_api}

        # Create coordinator with mocked __init__
        mock_super_init.return_value = None
        config_entry = MagicMock()
        config_entry.options.get.return_value = 30  # Mock scan interval
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = await coordinator._get_enabled_metrics("test_device")

        assert "bt1" in result
        assert "bt2" in result
        assert "bt3" not in result  # Disabled entity should not be included

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @patch("homeassistant.helpers.device_registry.async_get", new_callable=AsyncMock)
    @patch("homeassistant.helpers.entity_registry.async_get", new_callable=AsyncMock)
    async def test_get_enabled_metrics_no_device_found(
        self, mock_entity_async_get, mock_device_async_get, mock_super_init
    ):
        """Test _get_enabled_metrics when no device is found in registry."""
        # Create mock registries
        mock_device_registry = MagicMock()
        mock_entity_registry = MagicMock()
        mock_device_async_get.return_value = mock_device_registry
        mock_entity_async_get.return_value = mock_entity_registry
        mock_device_registry.devices.values.return_value = []

        # Create mock hass
        mock_hass = MagicMock()
        mock_api = MagicMock()
        mock_hass.data = {DOMAIN: mock_api}

        # Create coordinator with mocked __init__
        mock_super_init.return_value = None
        config_entry = MagicMock()
        config_entry.options.get.return_value = 30  # Mock scan interval
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = await coordinator._get_enabled_metrics("test_device")

        # Should return DEFAULT_ENABLED_METRICS
        assert result == DEFAULT_ENABLED_METRICS

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @patch("homeassistant.helpers.device_registry.async_get", new_callable=AsyncMock)
    @patch("homeassistant.helpers.entity_registry.async_get", new_callable=AsyncMock)
    async def test_get_enabled_metrics_no_matching_entities(
        self, mock_entity_async_get, mock_device_async_get, mock_super_init
    ):
        """Test _get_enabled_metrics when device exists but no matching entities."""
        # Create mock registries
        mock_device_registry = MagicMock()
        mock_entity_registry = MagicMock()
        mock_device_async_get.return_value = mock_device_registry
        mock_entity_async_get.return_value = mock_entity_registry

        # Mock device
        mock_device = MagicMock()
        mock_device.id = "device_id_123"
        mock_device.identifiers = {(DOMAIN, "qvantum-test_device")}
        mock_device_registry.devices.values.return_value = [mock_device]

        # Mock entities that don't match our criteria
        mock_entity = MagicMock()
        mock_entity.device_id = "device_id_123"
        mock_entity.disabled_by = None
        mock_entity.unique_id = "other_prefix_test_device"  # Wrong prefix

        mock_entity_registry.entities.values.return_value = [mock_entity]

        # Create mock hass
        mock_hass = MagicMock()
        mock_api = MagicMock()
        mock_hass.data = {DOMAIN: mock_api}

        # Create coordinator with mocked __init__
        mock_super_init.return_value = None
        config_entry = MagicMock()
        config_entry.options.get.return_value = 30  # Mock scan interval
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = await coordinator._get_enabled_metrics("test_device")

        # Should return empty list since no matching entities
        assert result == []
