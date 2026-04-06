"""Tests for Qvantum coordinator functions."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.qvantum.coordinator import (
    handle_setting_update_response,
    QvantumDataUpdateCoordinator,
)
from custom_components.qvantum.const import (
    CONF_MODBUS_TCP,
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    REQUIRED_METRICS,
)
from homeassistant.const import CONF_SCAN_INTERVAL


def _modbus_options_get(key, default=None):
    """Simulate config_entry.options.get for a Modbus-enabled entry with 120 s scan interval."""
    if key == CONF_MODBUS_TCP:
        return True
    if key == CONF_SCAN_INTERVAL:
        return 120
    return default


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
        config_entry.options.get.side_effect = lambda key, default=None: 30 if key == CONF_SCAN_INTERVAL else default
        config_entry.data.get.return_value = False  # Mock data.get to return False for modbus
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = coordinator._get_enabled_metrics("test_device")

        # Should return DEFAULT_ENABLED_HTTP_METRICS plus REQUIRED_METRICS
        expected_metrics = set(DEFAULT_ENABLED_HTTP_METRICS) | set(REQUIRED_METRICS)
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
        config_entry.options.get.side_effect = lambda key, default=None: 30 if key == CONF_SCAN_INTERVAL else default
        config_entry.data.get.return_value = False  # Mock data.get to return False for modbus
        config_entry.unique_id = "test_device"
        coordinator = QvantumDataUpdateCoordinator(mock_hass, config_entry)
        coordinator.hass = mock_hass

        result = coordinator._get_enabled_metrics("test_device")

        # Should return DEFAULT_ENABLED_HTTP_METRICS plus REQUIRED_METRICS since no matching entities found
        expected_metrics = set(DEFAULT_ENABLED_HTTP_METRICS) | set(REQUIRED_METRICS)
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
        mock_entity.unique_id = "qvantum_inputcurrent1_test_device"
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

        assert "inputcurrent1" not in result

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

    @patch("custom_components.qvantum.coordinator.dt_util.utcnow")
    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_modbus_fetches_tap_stop_when_extra_tap_water_on(
        self, mock_super_init, mock_utcnow
    ):
        """Test that tap_stop is fetched via HTTP when extra_tap_water is on in Modbus mode."""
        mock_super_init.return_value = None
        fixed_now = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = fixed_now

        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "test_device_123"})
        mock_api.get_metrics = AsyncMock(
            return_value={"metrics": {"hpid": "test_device_123"}}
        )
        mock_api.get_settings = AsyncMock(
            return_value={"settings": [{"name": "extra_tap_water", "value": "on"}]}
        )
        mock_api.get_http_metrics = AsyncMock(
            return_value={"metrics": {"tap_stop": 9999}}
        )

        mock_hass = MagicMock()
        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": MagicMock(),
            "entity_registry": MagicMock(),
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options.get.side_effect = _modbus_options_get
        mock_config_entry.data = {}
        mock_config_entry.unique_id = "test_device_123"

        coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
        coordinator.api = mock_api
        coordinator.hass = mock_hass

        result = await coordinator.async_update_data()

        mock_api.get_http_metrics.assert_called_once_with(
            "test_device_123", ["tap_stop"]
        )
        assert result["values"]["tap_stop"] == 9999
        assert coordinator._last_tap_stop_fetch == fixed_now

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_modbus_uses_cached_tap_stop_within_poll_interval(
        self, mock_super_init
    ):
        """Test that the cached tap_stop value is returned when within the poll interval."""
        mock_super_init.return_value = None

        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "test_device_123"})
        mock_api.get_metrics = AsyncMock(
            return_value={"metrics": {"hpid": "test_device_123"}}
        )
        mock_api.get_settings = AsyncMock(
            return_value={"settings": [{"name": "extra_tap_water", "value": "on"}]}
        )
        mock_api.get_http_metrics = AsyncMock(
            return_value={"metrics": {"tap_stop": 9999}}
        )

        mock_hass = MagicMock()
        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": MagicMock(),
            "entity_registry": MagicMock(),
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options.get.side_effect = _modbus_options_get
        mock_config_entry.data = {}
        mock_config_entry.unique_id = "test_device_123"

        coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
        coordinator.api = mock_api
        coordinator.hass = mock_hass

        # First call: fetches from HTTP and caches
        result1 = await coordinator.async_update_data()
        assert result1["values"]["tap_stop"] == 9999
        mock_api.get_http_metrics.assert_called_once()

        # Second call within interval: should NOT fetch again but still return cached value
        result2 = await coordinator.async_update_data()
        assert result2["values"]["tap_stop"] == 9999
        mock_api.get_http_metrics.assert_called_once()  # still only one call total

    @patch("custom_components.qvantum.coordinator.dt_util.utcnow")
    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_modbus_refetches_tap_stop_after_interval_elapsed(
        self, mock_super_init, mock_utcnow
    ):
        """Test that tap_stop is re-fetched after DEFAULT_SCAN_INTERVAL seconds have elapsed."""
        mock_super_init.return_value = None
        fixed_now = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = fixed_now

        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "test_device_123"})
        mock_api.get_metrics = AsyncMock(
            return_value={"metrics": {"hpid": "test_device_123"}}
        )
        mock_api.get_settings = AsyncMock(
            return_value={"settings": [{"name": "extra_tap_water", "value": "on"}]}
        )
        mock_api.get_http_metrics = AsyncMock(
            return_value={"metrics": {"tap_stop": 7777}}
        )

        mock_hass = MagicMock()
        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": MagicMock(),
            "entity_registry": MagicMock(),
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options.get.side_effect = _modbus_options_get
        mock_config_entry.data = {}
        mock_config_entry.unique_id = "test_device_123"

        coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
        coordinator.api = mock_api
        coordinator.hass = mock_hass

        # Simulate a previous fetch older than DEFAULT_SCAN_INTERVAL
        coordinator._last_tap_stop_fetch = fixed_now - timedelta(
            seconds=DEFAULT_SCAN_INTERVAL + 1
        )
        coordinator._cached_tap_stop = 1234  # stale cached value

        result = await coordinator.async_update_data()

        mock_api.get_http_metrics.assert_called_once_with(
            "test_device_123", ["tap_stop"]
        )
        assert result["values"]["tap_stop"] == 7777
        assert coordinator._last_tap_stop_fetch == fixed_now

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_modbus_skips_tap_stop_when_extra_tap_water_off(
        self, mock_super_init
    ):
        """Test that tap_stop is NOT fetched via HTTP when extra_tap_water is off in Modbus mode."""
        mock_super_init.return_value = None

        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "test_device_123"})
        mock_api.get_metrics = AsyncMock(
            return_value={"metrics": {"hpid": "test_device_123"}}
        )
        mock_api.get_settings = AsyncMock(
            return_value={"settings": [{"name": "extra_tap_water", "value": "off"}]}
        )
        mock_api.get_http_metrics = AsyncMock()

        mock_hass = MagicMock()
        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": MagicMock(),
            "entity_registry": MagicMock(),
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options.get.side_effect = _modbus_options_get
        mock_config_entry.data = {}
        mock_config_entry.unique_id = "test_device_123"

        coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
        coordinator.api = mock_api
        coordinator.hass = mock_hass

        await coordinator.async_update_data()

        mock_api.get_http_metrics.assert_not_called()

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_modbus_skips_tap_stop_when_extra_tap_water_absent(
        self, mock_super_init
    ):
        """Test that tap_stop is NOT fetched via HTTP when extra_tap_water is absent in Modbus mode."""
        mock_super_init.return_value = None

        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "test_device_123"})
        mock_api.get_metrics = AsyncMock(
            return_value={"metrics": {"hpid": "test_device_123"}}
        )
        mock_api.get_settings = AsyncMock(return_value={"settings": []})
        mock_api.get_http_metrics = AsyncMock()

        mock_hass = MagicMock()
        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": MagicMock(),
            "entity_registry": MagicMock(),
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options.get.side_effect = _modbus_options_get
        mock_config_entry.data = {}
        mock_config_entry.unique_id = "test_device_123"

        coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
        coordinator.api = mock_api
        coordinator.hass = mock_hass

        await coordinator.async_update_data()

        mock_api.get_http_metrics.assert_not_called()


class TestHpStatusPostProcessing:
    """Tests for hp_status post-processing via compressor_state."""

    def _make_coordinator(self, mock_super_init, metrics, modbus=True):
        mock_super_init.return_value = None

        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "test_device_123"})
        mock_api.get_metrics = AsyncMock(return_value={"metrics": metrics})
        mock_api.get_settings = AsyncMock(return_value={"settings": []})

        mock_hass = MagicMock()
        mock_hass.data = {
            DOMAIN: mock_api,
            "device_registry": MagicMock(),
            "entity_registry": MagicMock(),
        }

        mock_config_entry = MagicMock()
        if modbus:
            mock_config_entry.options.get.side_effect = _modbus_options_get
        else:
            mock_config_entry.options.get.return_value = None
        mock_config_entry.data = {}
        mock_config_entry.unique_id = "test_device_123"

        coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
        coordinator.api = mock_api
        coordinator.hass = mock_hass
        return coordinator

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_hp_status_derived_hot_water(self, mock_super_init):
        """compressor_state=4 (Hot water) maps to hp_status=2."""
        coordinator = self._make_coordinator(
            mock_super_init, {"hp_status": 0, "compressor_state": 4}
        )
        result = await coordinator.async_update_data()
        assert result["values"]["hp_status"] == 2

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_hp_status_derived_heating(self, mock_super_init):
        """compressor_state=2 (Heating) maps to hp_status=3."""
        coordinator = self._make_coordinator(
            mock_super_init, {"hp_status": 0, "compressor_state": 2}
        )
        result = await coordinator.async_update_data()
        assert result["values"]["hp_status"] == 3

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_hp_status_derived_defrost(self, mock_super_init):
        """compressor_state=9 (Defrost DHW passive) maps to hp_status=1."""
        coordinator = self._make_coordinator(
            mock_super_init, {"hp_status": 0, "compressor_state": 9}
        )
        result = await coordinator.async_update_data()
        assert result["values"]["hp_status"] == 1

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_hp_status_not_overridden_when_nonzero(self, mock_super_init):
        """hp_status is not changed when it is already nonzero."""
        coordinator = self._make_coordinator(
            mock_super_init, {"hp_status": 3, "compressor_state": 4}
        )
        result = await coordinator.async_update_data()
        assert result["values"]["hp_status"] == 3

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_hp_status_derived_cooling(self, mock_super_init):
        """compressor_state=3 (Cooling) maps to hp_status=4."""
        coordinator = self._make_coordinator(
            mock_super_init, {"hp_status": 0, "compressor_state": 3}
        )
        result = await coordinator.async_update_data()
        assert result["values"]["hp_status"] == 4

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_hp_status_derived_cooling_alias(self, mock_super_init):
        """compressor_state=7 (Cooling alias) maps to hp_status=4."""
        coordinator = self._make_coordinator(
            mock_super_init, {"hp_status": 0, "compressor_state": 7}
        )
        result = await coordinator.async_update_data()
        assert result["values"]["hp_status"] == 4

    @patch("homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__")
    @pytest.mark.asyncio
    async def test_hp_status_stays_zero_without_compressor_state(self, mock_super_init):
        """hp_status stays 0 when compressor_state is absent."""
        coordinator = self._make_coordinator(mock_super_init, {"hp_status": 0})
        result = await coordinator.async_update_data()
        assert result["values"]["hp_status"] == 0


class TestDeriveTapWaterCapacity:
    """Tests for _derive_tap_water_capacity."""

    def _make_coordinator(self):
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            mock_hass = MagicMock()
            mock_hass.data = {DOMAIN: MagicMock()}
            mock_config_entry = MagicMock()
            mock_config_entry.options.get.side_effect = lambda key, default=None: (
                default
            )
            mock_config_entry.data = {}
            mock_config_entry.unique_id = "test_device_123"
            coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
        return coordinator

    def test_known_mapping_sets_capacity(self):
        """(start=55, stop=70) maps to capacity 4."""
        coordinator = self._make_coordinator()
        values = {
            "tap_water_capacity_target": None,
            "tap_water_start": 55,
            "tap_water_stop": 70,
        }
        coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] == 4

    def test_all_known_mappings(self):
        """Every entry in TAP_WATER_CAPACITY_MAPPINGS is derived correctly."""
        from custom_components.qvantum.const import TAP_WATER_CAPACITY_MAPPINGS

        coordinator = self._make_coordinator()
        for (start, stop), expected in TAP_WATER_CAPACITY_MAPPINGS.items():
            values = {
                "tap_water_capacity_target": None,
                "tap_water_start": start,
                "tap_water_stop": stop,
            }
            coordinator._derive_tap_water_capacity(values)
            assert values["tap_water_capacity_target"] == expected, (
                f"Expected capacity {expected} for start={start} stop={stop}"
            )

    def test_no_override_when_already_set(self):
        """Existing tap_water_capacity_target is not overwritten."""
        coordinator = self._make_coordinator()
        values = {
            "tap_water_capacity_target": 7,
            "tap_water_start": 55,
            "tap_water_stop": 70,
        }
        coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] == 7

    def test_missing_tap_start_leaves_none(self):
        """Missing tap_water_start leaves capacity unchanged."""
        coordinator = self._make_coordinator()
        values = {"tap_water_capacity_target": None, "tap_water_stop": 70}
        coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] is None

    def test_missing_tap_stop_leaves_none(self):
        """Missing tap_water_stop leaves capacity unchanged."""
        coordinator = self._make_coordinator()
        values = {"tap_water_capacity_target": None, "tap_water_start": 55}
        coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] is None

    def test_unknown_pair_leaves_none_and_warns(self):
        """An unknown (start, stop) pair leaves capacity as None and logs a warning."""
        coordinator = self._make_coordinator()
        values = {
            "tap_water_capacity_target": None,
            "tap_water_start": 99,
            "tap_water_stop": 99,
        }
        with patch("custom_components.qvantum.coordinator._LOGGER") as mock_logger:
            coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] is None
        mock_logger.warning.assert_called_once()

    def test_non_integer_values_leave_none_and_warn(self):
        """String values for tap_water_start/stop cannot match integer mapping keys."""
        coordinator = self._make_coordinator()
        values = {"tap_water_capacity_target": None, "tap_water_start": "55", "tap_water_stop": "70"}
        with patch("custom_components.qvantum.coordinator._LOGGER") as mock_logger:
            coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] is None
        mock_logger.warning.assert_called_once()

    def test_capacity_key_absent_treated_as_none(self):
        """tap_water_capacity_target absent from dict is treated the same as None."""
        coordinator = self._make_coordinator()
        values = {"tap_water_start": 55, "tap_water_stop": 70}
        coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] == 4


class TestCalculateHeatingPower:
    """Tests for _calculate_heating_power."""

    def _make_coordinator(self):
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ):
            mock_hass = MagicMock()
            mock_hass.data = {DOMAIN: MagicMock()}
            mock_config_entry = MagicMock()
            mock_config_entry.options.get.side_effect = lambda key, default=None: default
            mock_config_entry.data = {}
            mock_config_entry.unique_id = "test_device_123"
            coordinator = QvantumDataUpdateCoordinator(mock_hass, mock_config_entry)
            coordinator.data = None  # simulate no prior poll
        return coordinator

    def test_no_previous_sample_writes_zero_power(self):
        """First call (heating active) records baseline and writes 0 W — no prior delta."""
        coordinator = self._make_coordinator()
        values = {"heatingenergy": 100.0, "hp_status": 3}

        with patch("custom_components.qvantum.coordinator.dt_util.utcnow") as mock_now:
            mock_now.return_value = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
            coordinator._calculate_heating_power(values)

        assert values["heatingpower"] == 0
        assert coordinator._last_heatingenergy == 100.0

    def test_calculates_power_from_delta(self):
        """After two calls, heatingpower = delta_kWh / delta_s * 3_600_000."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)  # 15 s later

        with patch("custom_components.qvantum.coordinator.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            coordinator._calculate_heating_power({"heatingenergy": 100.0, "hp_status": 3})

            mock_now.return_value = t1
            values = {"heatingenergy": 100.001, "hp_status": 3}  # +0.001 kWh in 15 s
            coordinator._calculate_heating_power(values)

        # 0.001 kWh / 15 s * 3_600_000 = 240.0 W
        assert values["heatingpower"] == 240.0

    def test_negative_delta_clamped_to_zero(self):
        """A negative delta (counter reset) produces 0 W, not a negative value."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)

        with patch("custom_components.qvantum.coordinator.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            coordinator._calculate_heating_power({"heatingenergy": 100.0, "hp_status": 3})

            mock_now.return_value = t1
            values = {"heatingenergy": 90.0, "hp_status": 3}  # counter reset
            coordinator._calculate_heating_power(values)

        assert values["heatingpower"] == 0.0

    def test_not_heating_resets_power_to_zero(self):
        """When hp_status != 3, heatingpower is always 0 regardless of energy."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)

        with patch("custom_components.qvantum.coordinator.dt_util.utcnow") as mock_now:
            # Establish a prior computed power
            mock_now.return_value = t0
            coordinator._calculate_heating_power({"heatingenergy": 100.0, "hp_status": 3})
            mock_now.return_value = t1
            v1 = {"heatingenergy": 100.001, "hp_status": 3}
            coordinator._calculate_heating_power(v1)
            assert v1["heatingpower"] == 240.0
            coordinator.data = {"values": v1}

            # Heat pump switches to DHW (hp_status=2) — power must reset
            mock_now.return_value = t1
            values = {"heatingenergy": 100.001, "hp_status": 2}
            coordinator._calculate_heating_power(values)

        assert values["heatingpower"] == 0.0

    def test_missing_heatingenergy_skipped(self):
        """If heatingenergy is absent, the method does nothing."""
        coordinator = self._make_coordinator()
        values = {}
        coordinator._calculate_heating_power(values)
        assert "heatingpower" not in values
        assert coordinator._last_heatingenergy is None

    def test_zero_delta_holds_last_power_while_heating(self):
        """When hp_status==3 and counter has not ticked, last computed power is held."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 6, 12, 0, 30, tzinfo=timezone.utc)

        with patch("custom_components.qvantum.coordinator.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            coordinator._calculate_heating_power({"heatingenergy": 100.0, "hp_status": 3})

            mock_now.return_value = t1
            v1 = {"heatingenergy": 100.001, "hp_status": 3}
            coordinator._calculate_heating_power(v1)  # computes 240 W
            assert v1["heatingpower"] == 240.0

            # Simulate coordinator.data updated with the previous poll result
            coordinator.data = {"values": v1}

            # Counter unchanged — should hold 240 W, not reset to 0
            mock_now.return_value = t2
            values = {"heatingenergy": 100.001, "hp_status": 3}
            coordinator._calculate_heating_power(values)

        assert values["heatingpower"] == 240.0

    def test_updates_tracking_state_after_each_call(self):
        """_last_heatingenergy and _last_heatingenergy_time are updated each call."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)

        with patch("custom_components.qvantum.coordinator.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            coordinator._calculate_heating_power({"heatingenergy": 50.0, "hp_status": 3})
            assert coordinator._last_heatingenergy == 50.0
            assert coordinator._last_heatingenergy_time == t0

            mock_now.return_value = t1
            coordinator._calculate_heating_power({"heatingenergy": 51.0, "hp_status": 3})
            assert coordinator._last_heatingenergy == 51.0
            assert coordinator._last_heatingenergy_time == t1

    def test_time_reference_only_advances_on_energy_change(self):
        """Time denominator is the gap between counter increments, not poll intervals.

        With 0.1 kWh counter resolution polled at 16 s intervals, the naive
        calculation gives 0.1 * 3 600 000 / 16 ≈ 22 500 W.  The correct value
        uses the full ~48 s accumulation period to produce ≈ 7 500 W.
        """
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)

        with patch("custom_components.qvantum.coordinator.dt_util.utcnow") as mock_now:
            # Poll 1: establish baseline
            mock_now.return_value = t0
            coordinator._calculate_heating_power({"heatingenergy": 100.0, "hp_status": 3})

            # Poll 2 (16 s): counter unchanged — time reference must NOT advance
            mock_now.return_value = t0 + timedelta(seconds=16)
            values = {"heatingenergy": 100.0, "hp_status": 3}
            coordinator._calculate_heating_power(values)
            assert values["heatingpower"] == 0.0
            assert coordinator._last_heatingenergy_time == t0  # not advanced

            # Poll 3 (32 s): counter unchanged
            mock_now.return_value = t0 + timedelta(seconds=32)
            values = {"heatingenergy": 100.0, "hp_status": 3}
            coordinator._calculate_heating_power(values)
            assert coordinator._last_heatingenergy_time == t0  # still not advanced

            # Poll 4 (48 s): 0.1 kWh increment — measured over full 48 s window
            mock_now.return_value = t0 + timedelta(seconds=48)
            values = {"heatingenergy": 100.1, "hp_status": 3}
            coordinator._calculate_heating_power(values)

        # 0.1 kWh / 48 s * 3 600 000 = 7 500 W  (not 22 500 W from 0.1 / 16 s)
        assert values["heatingpower"] == 7500.0
