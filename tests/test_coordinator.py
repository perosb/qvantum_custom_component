"""Tests for Qvantum coordinator functions."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.qvantum.coordinator import (
    handle_setting_update_response,
    QvantumDataUpdateCoordinator,
)
from custom_components.qvantum.const import (
    CONF_MODBUS_TCP,
    DEFAULT_ENABLED_METRICS,
    DOMAIN,
    REQUIRED_METRICS,
)


class TestHandleSettingUpdateResponse:
    """Test the handle_setting_update_response function."""

    @pytest.mark.asyncio
    async def test_handle_setting_update_success(self):
        """Test successful setting update response handling."""
        coordinator = MagicMock()
        coordinator.data = {"values": {"old_key": "old_value"}}
        coordinator.async_set_updated_data = MagicMock()
        coordinator.async_refresh = AsyncMock()

        api_response = {"status": "APPLIED"}

        await handle_setting_update_response(
            api_response, coordinator, "values", "new_key", "new_value"
        )

        assert coordinator.data["values"]["new_key"] == "new_value"
        coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
        # No immediate refresh to avoid overwriting the update

    @pytest.mark.asyncio
    async def test_handle_setting_update_no_response(self):
        """Test setting update with no response."""
        coordinator = MagicMock()

        await handle_setting_update_response(
            None, coordinator, "values", "key", "value"
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

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    def test_get_enabled_metrics_with_device_found(self, mock_super_init):
        """Test _get_enabled_metrics when device is found in registry."""
        # Create mock hass with registries and API
        mock_hass = MagicMock()
        mock_device_registry = MagicMock()
        mock_entity_registry = MagicMock()
        mock_api = MagicMock()

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
        mock_entity3.unique_id = "qvantum_bt4_test_device"

        mock_entity_registry.entities.values.return_value = [
            mock_entity1,
            mock_entity2,
            mock_entity3,
        ]

        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": mock_device_registry,
            "entity_registry": mock_entity_registry,
        }

        # Create coordinator with mocked __init__
        mock_super_init.return_value = None
        config_entry = MagicMock()
        config_entry.options.get.return_value = 30  # Mock scan interval
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = coordinator._get_enabled_metrics("test_device")

        assert "bt1" in result
        assert "bt2" in result
        assert "bt4" not in result  # Disabled entity should not be included

        # Verify that all REQUIRED_METRICS are always included
        for metric in REQUIRED_METRICS:
            assert metric in result

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    def test_get_enabled_metrics_respects_disabled_http_metric(self, mock_super_init):
        """Test _get_enabled_metrics does not re-enable disabled HTTP metrics."""
        mock_hass = MagicMock()
        mock_device_registry = MagicMock()
        mock_entity_registry = MagicMock()
        mock_api = MagicMock()

        mock_device = MagicMock()
        mock_device.id = "device_id_123"
        mock_device.identifiers = {(DOMAIN, "qvantum-test_device")}
        mock_device_registry.devices.values.return_value = [mock_device]

        # Disabled HTTP metric in entity registry already; should stay out of final metrics
        mock_entity_disabled = MagicMock()
        mock_entity_disabled.device_id = "device_id_123"
        mock_entity_disabled.disabled_by = "user"
        mock_entity_disabled.unique_id = "qvantum_calc_suppy_cpr_test_device"

        mock_entity_registry.entities.values.return_value = [mock_entity_disabled]

        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": mock_device_registry,
            "entity_registry": mock_entity_registry,
        }

        mock_super_init.return_value = None
        config_entry = MagicMock()
        config_entry.options.get.side_effect = lambda key, default=None: False if key == CONF_MODBUS_TCP else 30
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = coordinator._get_enabled_metrics("test_device")

        assert "calc_suppy_cpr" not in result
        for metric in REQUIRED_METRICS:
            assert metric in result

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    def test_get_enabled_metrics_no_device_found(self, mock_super_init):
        """Test _get_enabled_metrics when no device is found in registry."""
        # Create mock hass with empty registries
        mock_hass = MagicMock()
        mock_device_registry = MagicMock()
        mock_api = MagicMock()
        mock_device_registry.devices.values.return_value = []

        mock_hass.data = {DOMAIN: mock_api, "device_registry": mock_device_registry}

        # Create coordinator with mocked __init__
        mock_super_init.return_value = None
        config_entry = MagicMock()
        config_entry.options.get.return_value = 30  # Mock scan interval
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = coordinator._get_enabled_metrics("test_device")

        # Should return DEFAULT_ENABLED_METRICS plus REQUIRED_METRICS
        expected_metrics = set(DEFAULT_ENABLED_METRICS) | set(REQUIRED_METRICS)
        assert set(result) == expected_metrics

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    def test_get_enabled_metrics_no_matching_entities(self, mock_super_init):
        """Test _get_enabled_metrics when device exists but no matching entities."""
        # Create mock hass with registries
        mock_hass = MagicMock()
        mock_device_registry = MagicMock()
        mock_entity_registry = MagicMock()
        mock_api = MagicMock()

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

        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": mock_device_registry,
            "entity_registry": mock_entity_registry,
        }

        # Create coordinator with mocked __init__
        mock_super_init.return_value = None
        config_entry = MagicMock()
        config_entry.options.get.return_value = 30  # Mock scan interval
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = coordinator._get_enabled_metrics("test_device")

        # Should return DEFAULT_ENABLED_METRICS plus REQUIRED_METRICS since no matching entities found
        expected_metrics = set(DEFAULT_ENABLED_METRICS) | set(REQUIRED_METRICS)
        assert set(result) == expected_metrics

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    def test_get_enabled_metrics_inspects_new_default_metrics(self, mock_super_init):
        """Test _get_enabled_metrics includes new defaults when registry has existing metrics."""
        mock_hass = MagicMock()
        mock_device_registry = MagicMock()
        mock_entity_registry = MagicMock()
        mock_api = MagicMock()

        mock_device = MagicMock()
        mock_device.id = "device_id_123"
        mock_device.identifiers = {(DOMAIN, "qvantum-test_device")}
        mock_device_registry.devices.values.return_value = [mock_device]

        mock_entity = MagicMock()
        mock_entity.device_id = "device_id_123"
        mock_entity.disabled_by = None
        mock_entity.unique_id = "qvantum_bt1_test_device"

        mock_entity_registry.entities.values.return_value = [mock_entity]

        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": mock_device_registry,
            "entity_registry": mock_entity_registry,
        }

        mock_super_init.return_value = None
        config_entry = MagicMock()
        config_entry.options.get.return_value = 30
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = coordinator._get_enabled_metrics("test_device")

        assert "bt1" in result
        assert "compressor_state" in result

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    def test_get_enabled_metrics_modbus_excludes_http_disabled_metrics(self, mock_super_init):
        """Test that Modbus mode ignores HTTP-only disabled metrics."""
        mock_super_init.return_value = None

        # Add device and entity for an HTTP-only disabled metric
        mock_device_registry = MagicMock()
        mock_device = MagicMock()
        mock_device.id = "device_id_123"
        mock_device.identifiers = {(DOMAIN, "qvantum-test_device")}
        mock_device_registry.devices.values.return_value = [mock_device]

        mock_entity_registry = MagicMock()
        mock_entity = MagicMock()
        mock_entity.device_id = "device_id_123"
        mock_entity.unique_id = "qvantum_gp2_speed_test_device"
        mock_entity.disabled_by = None
        mock_entity_registry.entities.values.return_value = [mock_entity]

        mock_hass = MagicMock()
        mock_hass.data = {
            DOMAIN: MagicMock(),
            "device_registry": mock_device_registry,
            "entity_registry": mock_entity_registry,
        }

        config_entry = MagicMock()
        config_entry.options.get.side_effect = lambda key, default=None: (
            True if key == CONF_MODBUS_TCP else default
        )
        config_entry.data = {}
        config_entry.unique_id = "test_device"

        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = coordinator._get_enabled_metrics("test_device")

        assert "gp2_speed" not in result

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    def test_poll_interval_modbus_enabled_in_data(self, mock_super_init):
        """Test that modbus in config_entry.data sets fast poll interval."""
        mock_super_init.return_value = None

        mock_hass = MagicMock()
        config_entry = MagicMock()
        config_entry.options.get.side_effect = lambda key, default=None: default
        config_entry.data = {CONF_MODBUS_TCP: True}

        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)

        assert coordinator.poll_interval == 15

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_async_update_data_modbus_tap_water_mapping(self, mock_super_init):
        """Test modbus dhw_normal_* keys are mapped to tap_water_* and tap_stop."""
        mock_super_init.return_value = None

        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "test_device_123"})
        mock_api.get_metrics = AsyncMock(
            return_value={
                "metrics": {
                    "hpid": "test_device_123",
                    "tap_water_start": 52,
                    "tap_water_stop": 62,
                    "tap_stop": 62,
                }
            }
        )
        mock_api.get_settings = AsyncMock(return_value={"settings": []})

        mock_hass = MagicMock()
        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": MagicMock(),
            "entity_registry": MagicMock(),
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options.get.return_value = 120
        coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
        coordinator.api = mock_api
        coordinator.hass = mock_hass

        result = await coordinator.async_update_data()

        assert result["values"]["tap_water_start"] == 52
        assert result["values"]["tap_water_stop"] == 62
        assert result["values"]["tap_stop"] == 62
        assert "dhw_normal_start" not in result["values"]
        assert "dhw_normal_stop" not in result["values"]

    def test_process_settings_data_invalid_list(self):
        data = {"settings": "not-a-list"}

        result = QvantumDataUpdateCoordinator._process_settings_data(None, data)

        assert result == {}

    def test_process_settings_data_invalid_items(self):
        data = {"settings": ["bad", {"name": "a"}, {"value": 1}, {"name": "a", "value": 1}]}

        result = QvantumDataUpdateCoordinator._process_settings_data(None, data)

        assert result == {"a": 1}

