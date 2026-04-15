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
    DHW_CAP_HYSTERESIS_C,
    DHW_COMPRESSOR_STATE_HOT_WATER,
    DHW_EMA_ALPHA,
    DHW_OUTLET_TEMP_THRESHOLD_DELTA_C,
    DHW_SHOWER_DURATION_MIN,
    REQUIRED_METRICS,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.util import dt as dt_util


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

    def test_unknown_pair_estimates_capacity_and_warns(self):
        """An unknown (start, stop) pair estimates capacity based on nearest stop and logs a warning."""
        coordinator = self._make_coordinator()
        values = {
            "tap_water_capacity_target": None,
            "tap_water_start": 99,
            "tap_water_stop": 99,
        }
        with patch("custom_components.qvantum.coordinator._LOGGER") as mock_logger:
            coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] == 7  # Nearest stop=76 -> capacity=7
        mock_logger.debug.assert_called_once()

    def test_unknown_pair_estimates_capacity_mid_range(self):
        """An unknown pair with stop=72 estimates capacity 5 (closest to stop=71)."""
        coordinator = self._make_coordinator()
        values = {
            "tap_water_capacity_target": None,
            "tap_water_start": 55,
            "tap_water_stop": 72,
        }
        coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] == 5

    def test_non_integer_values_leave_none_and_warn(self):
        """String values for tap_water_start/stop cannot match integer mapping keys."""
        coordinator = self._make_coordinator()
        values = {"tap_water_capacity_target": None, "tap_water_start": "55", "tap_water_stop": "70"}
        with patch("custom_components.qvantum.coordinator._LOGGER") as mock_logger:
            coordinator._derive_tap_water_capacity(values)
        assert values["tap_water_capacity_target"] is None
        mock_logger.debug.assert_called_once()

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

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
            mock_now.return_value = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
            coordinator._calculate_heating_power(values)

        assert values["heatingpower"] == 0
        assert coordinator._last_heatingenergy == 100.0

    def test_calculates_power_from_delta(self):
        """After two calls, heatingpower = delta_kWh / delta_s * 3_600_000."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)  # 15 s later

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
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

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
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

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
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

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
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

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
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

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
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


class TestCalculateDhwPower:
    """Tests for _calculate_dhw_power."""

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
            coordinator.data = None  # simulate no prior poll
        return coordinator

    def test_no_previous_sample_writes_zero_power(self):
        """First call (DHW active) records baseline and writes 0 W."""
        coordinator = self._make_coordinator()
        values = {"dhwenergy": 100.0, "bf1_l_min": 5.0}

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
            mock_now.return_value = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
            coordinator._calculate_dhw_power(values)

        assert values["dhwpower"] == 0
        assert coordinator._last_dhwenergy == 100.0

    def test_calculates_power_from_delta(self):
        """After two calls, dhwpower = delta_kWh / delta_s * 3_600_000."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            coordinator._calculate_dhw_power({"dhwenergy": 100.0, "bf1_l_min": 5.0})

            mock_now.return_value = t1
            values = {"dhwenergy": 100.001, "bf1_l_min": 5.0}
            coordinator._calculate_dhw_power(values)

        assert values["dhwpower"] == 240.0

    def test_not_dhw_resets_power_to_zero(self):
        """When bf1_l_min == 0, dhwpower is always 0 regardless of energy."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)
        t1_same = t1  # intentionally same timestamp: this assertion is mode-switch, not time-delta based

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            coordinator._calculate_dhw_power({"dhwenergy": 100.0, "bf1_l_min": 5.0})
            mock_now.return_value = t1
            v1 = {"dhwenergy": 100.001, "bf1_l_min": 5.0}
            coordinator._calculate_dhw_power(v1)
            assert v1["dhwpower"] == 240.0
            coordinator.data = {"values": v1}

            mock_now.return_value = t1_same
            values = {"dhwenergy": 100.001, "bf1_l_min": 0}
            coordinator._calculate_dhw_power(values)

        assert values["dhwpower"] == 0.0

    def test_missing_dhwenergy_skipped(self):
        """If dhwenergy is absent, the method does nothing."""
        coordinator = self._make_coordinator()
        values = {}
        coordinator._calculate_dhw_power(values)
        assert "dhwpower" not in values
        assert coordinator._last_dhwenergy is None

    def test_zero_delta_holds_last_power_while_dhw(self):
        """When bf1_l_min > 0 and counter has not ticked, last computed power is held."""
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 4, 6, 12, 0, 15, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 6, 12, 0, 30, tzinfo=timezone.utc)

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            coordinator._calculate_dhw_power({"dhwenergy": 100.0, "bf1_l_min": 5.0})

            mock_now.return_value = t1
            v1 = {"dhwenergy": 100.001, "bf1_l_min": 5.0}
            coordinator._calculate_dhw_power(v1)
            assert v1["dhwpower"] == 240.0

            coordinator.data = {"values": v1}

            mock_now.return_value = t2
            values = {"dhwenergy": 100.001, "bf1_l_min": 5.0}
            coordinator._calculate_dhw_power(values)

        assert values["dhwpower"] == 240.0


class TestCalculateTapWaterCap:
    """Tests for _calculate_tap_water_cap."""

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
            coordinator.data = None
        return coordinator

    def _make_warmed_up_coordinator(self):
        """Return a coordinator whose warmup window has already elapsed."""
        coordinator = self._make_coordinator()
        coordinator._tap_water_cap_start_time = dt_util.utcnow() - timedelta(seconds=61)
        return coordinator

    @pytest.mark.asyncio
    async def test_restore_dhw_state_populates_attributes(self):
        """async_restore_dhw_state loads persisted EMA values into coordinator state."""
        coordinator = self._make_coordinator()
        stored = {
            "cold_temp": 9.5,
            "flow_lpm": 5.8,
            "tap_water_cap": 4.2,
            "published_cap": 4.2,
            "published_minutes": 25,
        }
        with patch.object(coordinator._dhw_store, "async_load", return_value=stored):
            await coordinator.async_restore_dhw_state()
        assert coordinator._last_shower_cold_temp == 9.5
        assert coordinator._last_shower_flow_lpm == 5.8
        assert coordinator._last_tap_water_cap == 4.2
        assert coordinator._last_published_tap_water_cap == 4.2
        assert coordinator._last_published_tap_water_minutes == 25

    @pytest.mark.asyncio
    async def test_restore_dhw_state_empty_storage_leaves_none(self):
        """With no stored data, EMA attributes remain None after restore."""
        coordinator = self._make_coordinator()
        with patch.object(coordinator._dhw_store, "async_load", return_value=None):
            await coordinator.async_restore_dhw_state()
        assert coordinator._last_shower_cold_temp is None
        assert coordinator._last_shower_flow_lpm is None
        assert coordinator._last_tap_water_cap is None

    @pytest.mark.asyncio
    async def test_restore_dhw_state_storage_error_leaves_none(self):
        """A storage I/O error during restore is swallowed; attributes stay None."""
        coordinator = self._make_coordinator()
        with patch.object(
            coordinator._dhw_store,
            "async_load",
            side_effect=Exception("disk error"),
        ):
            await coordinator.async_restore_dhw_state()
        assert coordinator._last_shower_cold_temp is None
        assert coordinator._last_shower_flow_lpm is None
        assert coordinator._last_tap_water_cap is None

    def test_persist_dhw_state_updates_last_persisted_only_after_schedule_success(self):
        """A successful schedule updates _last_persisted_dhw_state."""
        coordinator = self._make_coordinator()
        coordinator._last_shower_cold_temp = 8.4
        coordinator._last_shower_flow_lpm = 6.8
        coordinator._last_tap_water_cap = 5.0
        coordinator._last_published_tap_water_cap = 5.0
        coordinator._last_published_tap_water_minutes = 30

        with patch.object(
            coordinator._dhw_store, "async_delay_save"
        ) as mock_delay_save:
            coordinator._persist_dhw_state()

        mock_delay_save.assert_called_once()
        assert coordinator._last_persisted_dhw_state == (
            8.4,
            6.8,
            None,  # _last_shower_temp_c not set
            None,  # _last_shower_duration_min not set
            5.0,
            5.0,
            30,
        )

    def test_persist_dhw_state_schedule_error_does_not_update_last_persisted(self):
        """A scheduling failure must not poison _last_persisted_dhw_state."""
        coordinator = self._make_coordinator()
        coordinator._last_persisted_dhw_state = None
        coordinator._last_shower_cold_temp = 8.4
        coordinator._last_shower_flow_lpm = 6.8
        coordinator._last_tap_water_cap = 5.0
        coordinator._last_published_tap_water_cap = 5.0
        coordinator._last_published_tap_water_minutes = 30

        with patch.object(
            coordinator._dhw_store,
            "async_delay_save",
            side_effect=Exception("schedule error"),
        ):
            coordinator._persist_dhw_state()

        assert coordinator._last_persisted_dhw_state is None

    def test_missing_tank_temp_skips(self):
        """When bt30 is absent, tap_water_cap is not written."""
        coordinator = self._make_coordinator()
        values = {"bt33": 8.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values)
        assert "tap_water_cap" not in values
        assert "tap_water_minutes" not in values

    def test_warmup_suppresses_publication_during_flow(self):
        """During active flow, publication holds the last published value."""
        coordinator = self._make_coordinator()
        # First call with no flow: publishes immediately
        values_no_flow = {"bt30": 60.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values_no_flow)
        assert "tap_water_cap" in values_no_flow
        baseline_cap = values_no_flow["tap_water_cap"]
        baseline_minutes = values_no_flow["tap_water_minutes"]

        # Flow starts: 60 s hold begins, first flow poll keeps the previous value
        values_flow1 = {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        coordinator._calculate_tap_water_cap(values_flow1)
        assert values_flow1["tap_water_cap"] == baseline_cap
        assert values_flow1["tap_water_minutes"] == baseline_minutes

        # Subsequent flow poll within 60 s: still retains the previous value
        values_flow2 = {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        coordinator._calculate_tap_water_cap(values_flow2)
        assert values_flow2["tap_water_cap"] == baseline_cap
        assert values_flow2["tap_water_minutes"] == baseline_minutes
        assert coordinator._last_tap_water_cap is not None

    def test_no_flow_always_publishes(self):
        """Without active flow, every poll publishes (no warmup suppression)."""
        coordinator = self._make_coordinator()
        for _ in range(3):
            values = {"bt30": 60.0, "bf1_l_min": 0.0}
            coordinator._calculate_tap_water_cap(values)
            assert "tap_water_cap" in values

    def test_flow_onset_resets_warmup(self):
        """Each new flow onset starts a fresh 60 s hold before publishing."""
        coordinator = self._make_coordinator()
        # No-flow baseline: publishes
        baseline = {"bt30": 60.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(baseline)
        baseline_cap = baseline["tap_water_cap"]

        # Flow starts: retains the previous value while warmup is active
        values = {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        coordinator._calculate_tap_water_cap(values)
        assert values["tap_water_cap"] == baseline_cap
        assert coordinator._tap_water_cap_start_time is not None

        # Flow stops: resets window; next no-flow poll publishes again
        values_stopped = {"bt30": 60.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values_stopped)
        assert "tap_water_cap" in values_stopped
        assert coordinator._tap_water_cap_start_time is None
        # Flow starts again: new 60 s hold, retaining the last published value.
        values2 = {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        coordinator._calculate_tap_water_cap(values2)
        assert values2["tap_water_cap"] == values_stopped["tap_water_cap"]
        assert values2["tap_water_minutes"] == values_stopped["tap_water_minutes"]

    def test_warmup_fallback_minutes_use_current_duration(self):
        """If published minutes are missing, fallback uses current learned duration."""
        coordinator = self._make_coordinator()
        coordinator._last_published_tap_water_cap = 2.0
        coordinator._last_published_tap_water_minutes = None
        coordinator._last_shower_duration_min = 4.9

        values = {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0, "bt34": 45.0}
        coordinator._calculate_tap_water_cap(values)

        # warmup_progress=0 on first active-flow poll, so minutes should be the
        # fallback derived from cap * learned duration (2.0 * 4.9 -> 10), not
        # cap * default duration (2.0 * 6.0 -> 12).
        assert values["tap_water_minutes"] == 10

    def test_warmup_mid_ramp_interpolates_published_values(self):
        """At 30 s into a 60 s warmup window, published values are linearly
        interpolated ≈ halfway between the last-published and newly-calculated values.

        Setup:
          last_published_cap = 2.0 showers, last_published_minutes = 11
          bt30=60°C, cold=8°C, flow=7 L/min, shower_temp=45°C (via EMA)
          → new computed: published_cap=1.4, published_minutes=9
          warmup_progress=0.5 → expected output: 1.7 showers, 10 min
        """
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)

        # Pre-seed the state that would exist before this shower event started.
        coordinator._last_published_tap_water_cap = 2.0
        coordinator._last_published_tap_water_minutes = 11
        # Stable EMA shower temp so calc_shower_temp is predictable.
        coordinator._last_shower_temp_c = 45.0
        # Warmup window started 30 s ago → warmup_progress = 30/60 = 0.5.
        coordinator._tap_water_cap_start_time = t0 - timedelta(seconds=30)

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            values = {"bt30": 60.0, "bf1_l_min": 7.0, "bt33": 8.0, "bt34": 45.0}
            coordinator._calculate_tap_water_cap(values)

        # Arithmetic for the "newly calculated" values at full progress (log model):
        #   minutes = 175/7 * ln((60-8)/(45-8)) = 25 * ln(52/37) ≈ 8.5
        #   showers ≈ 1.42  → published_cap=1.4, published_minutes=9
        new_cap = 1.4
        new_minutes = 9
        last_cap = 2.0
        last_minutes = 11

        expected_cap = round(last_cap + (new_cap - last_cap) * 0.5, 1)  # 1.7
        expected_minutes = round(
            last_minutes + (new_minutes - last_minutes) * 0.5
        )  # 10

        assert values["tap_water_cap"] == pytest.approx(expected_cap, abs=0.05)
        assert values["tap_water_minutes"] == expected_minutes
        # Output must lie strictly between the two endpoints (ramps toward new value).
        assert min(last_cap, new_cap) < values["tap_water_cap"] < max(last_cap, new_cap)
        assert (
            min(last_minutes, new_minutes)
            < values["tap_water_minutes"]
            < max(last_minutes, new_minutes)
        )
        # _last_published_tap_water_cap must NOT be updated during warmup.
        assert coordinator._last_published_tap_water_cap == 2.0

    def test_warmup_ramp_uses_ema_candidate_not_frozen_prior(self):
        """Warmup interpolation target is the EMA candidate, not the frozen prior.

        Regression: before the fix, 'smoothed' was set to _last_tap_water_cap
        (the frozen pre-shower EMA) even during warmup. That made the ramp a no-op
        when _last_published_tap_water_cap ≈ _last_tap_water_cap, and left the EMA
        inflated if a transient raw value had been applied while is_warmup was True.

        Setup:
          _last_tap_water_cap        = 5.0  (e.g. inflated by transient on prev run)
          _last_published_tap_water_cap = 5.0
          _last_published_tap_water_minutes = 30
          bt30=60°C, cold=8°C (default), flow=7 L/min (default), shower_temp=38°C (default)
          → raw_showers = same as test_first_poll_uses_defaults ≈ 2.27
          → EMA candidate = 0.2*2.27 + 0.8*5.0 = 4.454
          warmup_progress = 0.5
          → expected published = round(5.0 + (round(4.454,1) - 5.0) * 0.5, 1)
                                = round(5.0 + (4.5 - 5.0) * 0.5, 1) = round(4.75, 1) = 4.8
        Under the old (broken) code:
          smoothed = _last_tap_water_cap = 5.0
          published_cap = 5.0   → interpolation: 5.0 + (5.0 - 5.0)*0.5 = 5.0 (no-op)
        """
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)

        coordinator._last_tap_water_cap = 5.0
        coordinator._last_published_tap_water_cap = 5.0
        coordinator._last_published_tap_water_minutes = 30
        # No shower-specific EMA values set → defaults apply for cold/flow/shower_temp.
        coordinator._tap_water_cap_start_time = t0 - timedelta(seconds=30)

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            # No flow → not in warmup path, uses defaults
            values = {"bt30": 60.0, "bf1_l_min": 0.0}
            coordinator._calculate_tap_water_cap(values)

        # raw_showers ≈ 2.27, EMA candidate = 0.2*2.27 + 0.8*5.0 = 4.454
        # Without flow the warmup window is NOT active (warmup only applies during flow),
        # so the EMA IS committed and published directly (no interpolation).
        # The output must be the EMA-smoothed value, below the frozen prior of 5.0.
        assert values["tap_water_cap"] < 5.0, (
            "EMA should have pulled the estimate below the inflated prior (5.0)"
        )
        # _last_tap_water_cap must have advanced from 5.0 toward the raw value.
        assert coordinator._last_tap_water_cap < 5.0

    def test_warmup_ramp_converges_toward_ema_candidate_during_flow(self):
        """During active flow warmup, successive polls ramp toward the EMA candidate.

        The previously-broken path: with _last_tap_water_cap pre-seeded, warmup would
        set smoothed=_last_tap_water_cap, making published_cap == _last_published_cap
        and the interpolation a no-op.  Now smoothed is the true EMA candidate so the
        ramp is meaningful.

        Setup (flow active, warmup progress = 0.5):
          _last_tap_water_cap           = 5.0
          _last_published_tap_water_cap = 5.0
          _last_published_tap_water_minutes = 30
          bt30=60°C, cold=10°C (in buffer), flow=7 L/min (in buffer), shower_temp=38°C
          → raw_showers ≈ (175/7)*ln(50/28)/6 ≈ 2.82
          → EMA candidate = 0.2*2.82 + 0.8*5.0 = 4.564  → published_cap = 4.6
          → warmup output = round(5.0 + (4.6 - 5.0) * 0.5, 1) = round(4.8, 1) = 4.8
        Old broken output would have been 5.0 (no-op ramp).
        """
        coordinator = self._make_coordinator()
        t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)

        coordinator._last_tap_water_cap = 5.0
        coordinator._last_published_tap_water_cap = 5.0
        coordinator._last_published_tap_water_minutes = 30
        # Seed a single rolling-buffer entry so calc_flow/cold are predictable.
        coordinator._flow_rolling_buffer = [(t0.timestamp(), 7.0, 10.0)]
        coordinator._tap_water_cap_start_time = t0 - timedelta(seconds=30)

        with patch("custom_components.qvantum.calculations.dt_util.utcnow") as mock_now:
            mock_now.return_value = t0
            values = {"bt30": 60.0, "bf1_l_min": 7.0, "bt33": 10.0}
            coordinator._calculate_tap_water_cap(values)

        # Output must be strictly between old prior (5.0) and the EMA candidate (≈4.6).
        assert values["tap_water_cap"] < 5.0, (
            "Ramp target is the EMA candidate, not the frozen prior — output must be < 5.0"
        )
        assert values["tap_water_cap"] > 4.5, (
            "Output should not have dropped all the way to the raw value"
        )
        # EMA state must NOT advance during warmup.
        assert coordinator._last_tap_water_cap == 5.0

    def test_first_poll_uses_defaults(self):
        """With no prior shower snapshot, defaults are used: bt30=60, cold=8, flow=7."""
        coordinator = self._make_warmed_up_coordinator()
        values = {"bt30": 60.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values)
        # Log model: minutes = 175/7 * ln((60-8)/(38-8)) = 25 * ln(52/30) ≈ 13.7
        # showers = 13.7 / 6 ≈ 2.3
        assert "tap_water_cap" in values
        assert values["tap_water_cap"] == pytest.approx(2.3, abs=0.1)
        assert values["tap_water_minutes"] == 14

    def test_updates_baseline_on_flow(self):
        """When bf1_l_min > 0.1, cold and flow snapshots are EMA-smoothed from their priors."""
        coordinator = self._make_coordinator()
        values = {"bt30": 60.0, "bf1_l_min": 6.5, "bt33": 12.0}
        coordinator._calculate_tap_water_cap(values)
        # cold: 0.2 * 12.0 + 0.8 * 8.0 = 8.8 (EMA from DHW_DEFAULT_COLD_TEMP_C prior)
        assert coordinator._last_shower_cold_temp == pytest.approx(8.8)
        # flow: 0.2 * 6.5 + 0.8 * 7.0 = 6.9 (EMA from DHW_DEFAULT_FLOW_LPM prior)
        assert coordinator._last_shower_flow_lpm == pytest.approx(6.9)

    def test_capacity_decreases_as_tank_drains(self):
        """Capacity decreases as tank_temp drops, reflecting actual hot water consumption."""
        # Full tank: bt30=60°C
        coordinator_full = self._make_warmed_up_coordinator()
        values_full = {"bt30": 60.0, "bf1_l_min": 0.0}
        coordinator_full._calculate_tap_water_cap(values_full)
        cap_full = values_full["tap_water_cap"]

        # Partially drained tank: bt30=45°C
        coordinator_half = self._make_warmed_up_coordinator()
        values_half = {"bt30": 45.0, "bf1_l_min": 0.0}
        coordinator_half._calculate_tap_water_cap(values_half)
        cap_half = values_half["tap_water_cap"]

        # Capacity must decrease as tank drains — this was the key bug: bt34 rising
        # during a shower caused capacity to appear to increase instead of decrease.
        assert cap_full > cap_half

    def test_uses_stored_cold_temp_when_no_flow(self):
        """After flow stops, the no-flow poll uses EMA snapshot values; the result is
        EMA-blended with the preceding flow-active estimate."""
        coordinator = self._make_warmed_up_coordinator()
        # First poll: showering with raw readings (cold=10, flow=6.0)
        # Log model: minutes = 175/6 * ln(50/28) ≈ 16.9; showers ≈ 2.82
        # EMA snapshot stored: cold=0.2*10+0.8*8=8.4, flow=0.2*6+0.8*7=6.8
        coordinator._calculate_tap_water_cap({"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0})
        assert coordinator._last_shower_cold_temp == pytest.approx(8.4)
        assert coordinator._last_shower_flow_lpm == pytest.approx(6.8)
        # Advance past the new warmup window
        coordinator._tap_water_cap_start_time -= timedelta(seconds=61)
        # Second poll: no flow — uses EMA snapshot (cold=8.4, flow=6.8)
        # Log model: minutes = 175/6.8 * ln(51.6/29.6) ≈ 14.3; showers ≈ 2.39
        # EMA blend: 0.2*2.39 + 0.8*2.82 ≈ 2.73 → 2.7 showers, 16 min
        values = {"bt30": 60.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values)
        assert values["tap_water_cap"] == pytest.approx(2.7, abs=0.1)
        assert values["tap_water_minutes"] == 16

    def test_tap_water_minutes_matches_smoothed_capacity(self):
        """tap_water_minutes is derived from the same smoothed tap_water_cap estimate."""
        coordinator = self._make_warmed_up_coordinator()
        # Flow onset resets warmup; advance past it so values are published
        coordinator._calculate_tap_water_cap(
            {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        )
        coordinator._tap_water_cap_start_time -= timedelta(seconds=61)
        values = {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        coordinator._calculate_tap_water_cap(values)
        assert values["tap_water_minutes"] == round(
            values["tap_water_cap"] * DHW_SHOWER_DURATION_MIN
        )

    def test_low_tank_temp_returns_zero(self):
        """When tank_temp - cold_temp < 5, tap_water_cap is set to 0.0."""
        coordinator = self._make_coordinator()
        # default cold = 8, so tank = 12 gives delta = 4 < 5
        values = {"bt30": 12.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values)
        assert values["tap_water_cap"] == 0.0
        assert values["tap_water_minutes"] == 0

    def test_ema_smooths_output(self):
        """Second poll blends toward new value rather than jumping immediately."""
        coordinator = self._make_warmed_up_coordinator()
        # First poll: no prior EMA state → raw value used as-is (about 5.8 with defaults)
        values1 = {"bt30": 60.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values1)
        first = values1["tap_water_cap"]

        # Second poll: lower tank temp
        values2 = {"bt30": 50.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values2)
        second = values2["tap_water_cap"]
        # Log model: raw_second ≈ 1.4 with bt30=50, cold=8, flow=7
        # EMA should stay between the new raw value and the previous reading
        assert second < first  # moved in the right direction
        assert second > 1.4  # did not jump all the way to the raw value

    # ------------------------------------------------------------------
    # Rolling buffer (Phase 1)
    # ------------------------------------------------------------------

    def test_rolling_buffer_populated_during_flow(self):
        """Buffer accumulates entries while flow is active."""
        coordinator = self._make_coordinator()
        coordinator._calculate_tap_water_cap(
            {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        )
        assert len(coordinator._flow_rolling_buffer) == 1
        ts, flow, cold = coordinator._flow_rolling_buffer[0]
        assert flow == 6.0
        assert cold == 10.0

    def test_rolling_buffer_cleared_on_flow_stop(self):
        """Buffer is emptied when flow drops to zero."""
        coordinator = self._make_coordinator()
        coordinator._calculate_tap_water_cap(
            {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        )
        assert len(coordinator._flow_rolling_buffer) == 1
        coordinator._calculate_tap_water_cap({"bt30": 60.0, "bf1_l_min": 0.0})
        assert coordinator._flow_rolling_buffer == []

    def test_rolling_buffer_trims_entries_older_than_60s(self):
        """Entries outside the 60-second window are pruned each poll."""
        coordinator = self._make_coordinator()
        now = dt_util.utcnow()
        # Seed one old entry (65 s ago) that should be trimmed
        old_ts = (now - timedelta(seconds=65)).timestamp()
        coordinator._flow_rolling_buffer = [(old_ts, 5.0, 9.0)]
        # A new poll appends a fresh entry then trims; only the new entry survives
        with patch("homeassistant.util.dt.utcnow", return_value=now):
            coordinator._calculate_tap_water_cap(
                {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
            )
        assert len(coordinator._flow_rolling_buffer) == 1
        assert coordinator._flow_rolling_buffer[0][1] == 6.0

    def test_rolling_buffer_mean_used_for_calc_flow(self):
        """calc_flow during active flow is the mean of buffered readings, not just the last."""
        coordinator = self._make_warmed_up_coordinator()
        now = dt_util.utcnow()
        # Pre-seed buffer with one entry from 10 s ago so the mean is (5.0+8.0)/2=6.5
        ten_s_ago = (now - timedelta(seconds=10)).timestamp()
        coordinator._flow_rolling_buffer = [(ten_s_ago, 5.0, 10.0)]
        # Current poll: flow=8.0 → mean=(5.0+8.0)/2=6.5 is used for calc_flow
        with patch("homeassistant.util.dt.utcnow", return_value=now):
            # Advance past warmup window
            coordinator._tap_water_cap_start_time = now - timedelta(seconds=61)
            values = {"bt30": 60.0, "bf1_l_min": 8.0, "bt33": 10.0}
            coordinator._calculate_tap_water_cap(values)
        assert "tap_water_cap" in values
        # Verify by computing what the result with mean flow=6.5 should be (log model):
        # minutes = 175/6.5 * ln(50/28) = 26.9 * 0.580 ≈ 15.6; showers = 15.6/6 ≈ 2.6
        assert values["tap_water_cap"] == pytest.approx(2.6, abs=0.2)

    # ------------------------------------------------------------------
    # Shower event history (Phase 2)
    # ------------------------------------------------------------------

    def test_event_samples_accumulate_during_flow(self):
        """Samples are accumulated in _shower_event_samples while flow is active."""
        coordinator = self._make_coordinator()
        for _ in range(3):
            coordinator._calculate_tap_water_cap(
                {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0, "bt34": 38.0}
            )
        assert len(coordinator._shower_event_samples) == 3

    def test_event_samples_cleared_after_flow_stops(self):
        """_shower_event_samples is always cleared when flow stops, even for short draws."""
        coordinator = self._make_coordinator()
        coordinator._calculate_tap_water_cap(
            {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
        )
        assert len(coordinator._shower_event_samples) == 1
        coordinator._calculate_tap_water_cap({"bt30": 60.0, "bf1_l_min": 0.0})
        assert coordinator._shower_event_samples == []

    def test_long_shower_creates_event_history_entry(self):
        """A shower lasting ≥1 min creates an entry in _shower_event_history."""
        coordinator = self._make_coordinator()
        start = dt_util.utcnow()
        coordinator._shower_start_time = start - timedelta(minutes=5)
        # Seed one sample so avg calculations have data
        coordinator._shower_event_samples = [
            ((start - timedelta(minutes=4)).timestamp(), 6.0, 10.0, 38.0)
        ]
        # Flow stops
        with patch("homeassistant.util.dt.utcnow", return_value=start):
            coordinator._calculate_tap_water_cap({"bt30": 60.0, "bf1_l_min": 0.0})
        assert len(coordinator._shower_event_history) == 1
        event = coordinator._shower_event_history[0]
        assert event["duration_min"] == pytest.approx(5.0, abs=0.1)
        assert event["avg_flow"] == 6.0
        assert event["avg_cold"] == 10.0
        assert event["avg_outlet_temp"] == 38.0
        assert event["water_used_l"] == pytest.approx(30.0, abs=0.5)

    def test_short_draw_does_not_create_event_history_entry(self):
        """A water draw shorter than 1 minute is NOT recorded in history."""
        coordinator = self._make_coordinator()
        start = dt_util.utcnow()
        coordinator._shower_start_time = start - timedelta(seconds=30)
        coordinator._shower_event_samples = [
            ((start - timedelta(seconds=15)).timestamp(), 4.0, 10.0, None)
        ]
        with patch("homeassistant.util.dt.utcnow", return_value=start):
            coordinator._calculate_tap_water_cap({"bt30": 60.0, "bf1_l_min": 0.0})
        assert coordinator._shower_event_history == []
        assert coordinator._shower_event_samples == []  # samples still cleared

    def test_event_history_capped_at_10_entries(self):
        """_shower_event_history never exceeds 10 entries (oldest is evicted)."""
        coordinator = self._make_coordinator()
        now = dt_util.utcnow()
        # Simulate 11 completed 2-minute showers
        for i in range(11):
            shower_start = now - timedelta(minutes=2)
            coordinator._shower_start_time = shower_start
            coordinator._shower_event_samples = [
                (shower_start.timestamp(), 6.0, 10.0, 38.0)
            ]
            with patch("homeassistant.util.dt.utcnow", return_value=now):
                coordinator._calculate_tap_water_cap({"bt30": 60.0, "bf1_l_min": 0.0})
        assert len(coordinator._shower_event_history) == 10

    def test_event_history_no_outlet_temp_records_none(self):
        """When bt34 is absent, avg_outlet_temp in the history entry is None."""
        coordinator = self._make_coordinator()
        start = dt_util.utcnow()
        coordinator._shower_start_time = start - timedelta(minutes=3)
        coordinator._shower_event_samples = [
            ((start - timedelta(minutes=2)).timestamp(), 6.0, 10.0, None)
        ]
        with patch("homeassistant.util.dt.utcnow", return_value=start):
            coordinator._calculate_tap_water_cap({"bt30": 60.0, "bf1_l_min": 0.0})
        assert len(coordinator._shower_event_history) == 1
        assert coordinator._shower_event_history[0]["avg_outlet_temp"] is None

    # ------------------------------------------------------------------
    # Outlet temperature EMA guard (pipe-flush phase filter)
    # ------------------------------------------------------------------

    def test_outlet_temp_at_threshold_does_not_update_shower_temp_ema(self):
        """bt34 exactly at cold + threshold is treated as pipe-flush and ignored."""
        coordinator = self._make_coordinator()
        cold = 10.0
        # Exactly at the boundary (not strictly above) — must be ignored
        outlet_at_boundary = cold + DHW_OUTLET_TEMP_THRESHOLD_DELTA_C
        coordinator._calculate_tap_water_cap(
            {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": cold, "bt34": outlet_at_boundary}
        )
        assert coordinator._last_shower_temp_c is None  # EMA not updated

    def test_outlet_temp_clearly_warm_updates_shower_temp_ema(self):
        """bt34 strictly above cold + threshold is accepted and the EMA is updated."""
        coordinator = self._make_coordinator()
        cold = 10.0
        # Just above the boundary
        outlet_warm = cold + DHW_OUTLET_TEMP_THRESHOLD_DELTA_C + 0.1
        coordinator._calculate_tap_water_cap(
            {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": cold, "bt34": outlet_warm}
        )
        # EMA should have been seeded from DHW_SHOWER_TEMP_C default toward outlet_warm
        from custom_components.qvantum.const import DHW_EMA_ALPHA, DHW_SHOWER_TEMP_C

        expected = DHW_EMA_ALPHA * outlet_warm + (1 - DHW_EMA_ALPHA) * DHW_SHOWER_TEMP_C
        assert coordinator._last_shower_temp_c == pytest.approx(expected)

    def test_learned_duration_used_in_subsequent_capacity_calculation(self):
        """A >=1 min flow event updates duration EMA, which is then used by subsequent no-flow cap/minute calculations."""
        coordinator = self._make_coordinator()
        start = dt_util.utcnow()

        # Start a flow event to set _shower_start_time and collect a sample.
        with patch("homeassistant.util.dt.utcnow", return_value=start):
            coordinator._calculate_tap_water_cap(
                {"bt30": 60.0, "bf1_l_min": 6.0, "bt33": 10.0}
            )

        # End event after 10 minutes (>= minimum): should update duration EMA.
        end = start + timedelta(minutes=10)
        with patch("homeassistant.util.dt.utcnow", return_value=end):
            coordinator._calculate_tap_water_cap({"bt30": 60.0, "bf1_l_min": 0.0})

        expected_learned_duration = (
            DHW_EMA_ALPHA * 10.0 + (1 - DHW_EMA_ALPHA) * DHW_SHOWER_DURATION_MIN
        )
        assert coordinator._last_shower_duration_min == pytest.approx(
            expected_learned_duration
        )

        # Next no-flow poll must use learned duration for the minute conversion.
        with patch(
            "homeassistant.util.dt.utcnow", return_value=end + timedelta(seconds=1)
        ):
            values = {"bt30": 60.0, "bf1_l_min": 0.0}
            coordinator._calculate_tap_water_cap(values)

        # minutes are computed from full-precision smoothed state, while
        # tap_water_cap is published rounded to 0.1; assert against internal state.
        assert values["tap_water_minutes"] == round(
            coordinator._last_tap_water_cap * coordinator._last_shower_duration_min
        )
        assert values["tap_water_minutes"] != round(
            coordinator._last_tap_water_cap * DHW_SHOWER_DURATION_MIN
        )

    def test_active_flow_near_cold_outlet_temp_uses_fallback_shower_temp(self):
        """During active flow, bt34 near cold inlet is ignored for cap math (pipe-flush guard)."""
        coordinator = self._make_warmed_up_coordinator()
        cold = 10.0
        # At threshold boundary: should be ignored and fallback to default shower temp.
        outlet_at_boundary = cold + DHW_OUTLET_TEMP_THRESHOLD_DELTA_C
        values = {
            "bt30": 60.0,
            "bf1_l_min": 6.0,
            "bt33": cold,
            "bt34": outlet_at_boundary,
        }

        coordinator._calculate_tap_water_cap(values)

        # Expected if fallback shower temp is used (log model):
        # minutes = 175/6 * ln((60-10)/(38-10)) = 29.2 * ln(50/28) ≈ 16.9; showers ≈ 2.8
        assert values["tap_water_cap"] == pytest.approx(2.8, abs=0.2)
        assert values["tap_water_minutes"] == round(
            coordinator._last_tap_water_cap * DHW_SHOWER_DURATION_MIN
        )

        # Additional guard: if raw near-cold bt34 had been used, cap would be huge.
        assert values["tap_water_cap"] < 20

    # ------------------------------------------------------------------
    # Hysteresis / zero-mode deadband (borderline tank-vs-shower-temp zone)
    # ------------------------------------------------------------------

    def test_hysteresis_entry_outputs_zero(self):
        """With no prior published estimate, hysteresis entry outputs 0."""
        coordinator = self._make_warmed_up_coordinator()
        coordinator._last_shower_temp_c = 45.0

        entry_threshold = 45.0 - DHW_CAP_HYSTERESIS_C
        borderline = {"bt30": entry_threshold, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(borderline)

        assert borderline["tap_water_cap"] == 0.0
        assert borderline["tap_water_minutes"] == 0
        assert coordinator._tap_water_cap_zero_mode is True

    def test_hysteresis_entry_resets_published_state_to_zero(self):
        """Force-zero output also updates published state to avoid stale baselines."""
        coordinator = self._make_warmed_up_coordinator()
        coordinator._last_shower_temp_c = 45.0
        coordinator._last_published_tap_water_cap = 2.8
        coordinator._last_published_tap_water_minutes = 18

        values = {"bt30": 44.7, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values)

        assert values["tap_water_cap"] == 0.0
        assert values["tap_water_minutes"] == 0
        assert coordinator._last_published_tap_water_cap == 0.0
        assert coordinator._last_published_tap_water_minutes == 0

    def test_hysteresis_remains_in_zero_mode_until_exit_threshold_cleared(self):
        """While in zero_mode, output stays 0 until tank clears shower_temp + hysteresis."""
        coordinator = self._make_warmed_up_coordinator()
        coordinator._last_shower_temp_c = 45.0

        # Drive into zero mode.
        coordinator._calculate_tap_water_cap({"bt30": 44.5, "bf1_l_min": 0.0})
        assert coordinator._tap_water_cap_zero_mode is True

        # Tank rises to shower_temp but not yet above exit threshold:
        # still inside deadband → must remain 0.
        still_borderline = {"bt30": 45.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(still_borderline)
        assert still_borderline["tap_water_cap"] == 0.0
        assert coordinator._tap_water_cap_zero_mode is True

        # Tank clears exit threshold (shower_temp + hysteresis):
        # zero_mode must be cleared and a fresh raw-seeded cap produced.
        # Use +2°C above shower_temp so the log-model result rounds to > 0.
        exit_temp = 45.0 + DHW_CAP_HYSTERESIS_C + 2.0
        cleared = {"bt30": exit_temp, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(cleared)
        assert coordinator._tap_water_cap_zero_mode is False
        assert cleared["tap_water_cap"] > 0.0

    def test_hysteresis_entry_resets_ema_so_recovery_starts_from_raw(self):
        """On the first poll entering hold mode, the EMA is reset to None so
        the first valid recovery poll seeds from raw rather than blending with
        the stale pre-hold value."""
        coordinator = self._make_warmed_up_coordinator()
        coordinator._last_shower_temp_c = 45.0
        # Prime a high EMA value.
        coordinator._last_tap_water_cap = 8.0
        coordinator._last_published_tap_water_cap = 8.0
        coordinator._last_published_tap_water_minutes = 48

        # Enter hold mode.
        coordinator._calculate_tap_water_cap({"bt30": 44.0, "bf1_l_min": 0.0})
        assert coordinator._tap_water_cap_zero_mode is True
        # EMA must be cleared on entry.
        assert coordinator._last_tap_water_cap is None

        # Exit hold mode: calculate with tank well above exit threshold.
        coordinator._last_shower_temp_c = 45.0
        exit_temp = 45.0 + DHW_CAP_HYSTERESIS_C + 1.0
        values = {"bt30": exit_temp, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values)
        # First recovery poll has no prior EMA → raw value used directly.
        # With default cold=8, flow=7 and tank=exit_temp the raw is modest;
        # it must NOT be blended with the stale 8.0.
        assert values["tap_water_cap"] < 6.0

    def test_near_shower_temp_low_headroom_gives_small_estimate(self):
        """When tank is only slightly above shower temp, the log model naturally
        returns a very small estimate without any additional correction factor."""
        coordinator = self._make_warmed_up_coordinator()
        coordinator._last_shower_temp_c = 47.4
        coordinator._last_shower_cold_temp = 8.2
        coordinator._last_shower_flow_lpm = 7.0
        coordinator._last_shower_duration_min = 6.7
        coordinator._last_tap_water_cap = None

        values = {"bt30": 49.0, "bf1_l_min": 0.0}
        coordinator._calculate_tap_water_cap(values)

        # Log model: minutes = 175/7 * ln((49-8.2)/(47.4-8.2)) = 25 * ln(1.04) ≈ 1
        # Near-threshold capacity is inherently small without needing a correction factor.
        assert values["tap_water_minutes"] <= 5
        assert values["tap_water_cap"] <= 0.8

    def test_active_flow_uses_learned_shower_temp_not_raw_outlet(self):
        """A temporary bt34 spike should not be used directly for capacity math."""
        coordinator = self._make_warmed_up_coordinator()
        coordinator._last_shower_temp_c = 45.0
        coordinator._last_shower_cold_temp = 9.0
        coordinator._last_shower_flow_lpm = 6.0
        coordinator._last_shower_duration_min = 6.7
        coordinator._last_tap_water_cap = None

        # Active flow with very high outlet reading (spiky bt34).
        values = {"bt30": 59.0, "bf1_l_min": 6.0, "bt33": 9.0, "bt34": 53.0}
        coordinator._calculate_tap_water_cap(values)

        # With learned shower temp around 45°C (EMA: 0.2*53+0.8*45=46.6),
        # log-model estimate ≈ 8 min. With raw bt34=53 it would be ≈ 4 min.
        # The EMA protection keeps the estimate notably higher than the spiked case.
        assert values["tap_water_minutes"] >= 6

    # ------------------------------------------------------------------
    # DHW reheating floor
    # ------------------------------------------------------------------

    def test_reheat_floor_compressor_state(self):
        """When compressor_state==8 (DHW mode), minutes is floored at one shower duration."""
        coordinator = self._make_warmed_up_coordinator()
        # Tank almost depleted: log model would return <<1 min without floor.
        coordinator._last_shower_temp_c = 47.2
        coordinator._last_shower_cold_temp = 9.4
        coordinator._last_shower_flow_lpm = 6.5
        coordinator._last_shower_duration_min = 5.4
        coordinator._last_tap_water_cap = None

        values = {
            "bt30": 49.0,
            "bf1_l_min": 0.0,
            "compressor_state": DHW_COMPRESSOR_STATE_HOT_WATER,
        }
        coordinator._calculate_tap_water_cap(values)

        assert values["tap_water_minutes"] >= round(DHW_SHOWER_DURATION_MIN)

    def test_reheat_floor_relay_l1(self):
        """When picpin_relay_heat_l1 is active, minutes is floored at one shower duration."""
        coordinator = self._make_warmed_up_coordinator()
        coordinator._last_shower_temp_c = 47.2
        coordinator._last_shower_cold_temp = 9.4
        coordinator._last_shower_flow_lpm = 6.5
        coordinator._last_shower_duration_min = 5.4
        coordinator._last_tap_water_cap = None

        values = {
            "bt30": 49.0,
            "bf1_l_min": 0.0,
            "picpin_relay_heat_l1": True,
        }
        coordinator._calculate_tap_water_cap(values)

        assert values["tap_water_minutes"] >= round(DHW_SHOWER_DURATION_MIN)

    def test_no_reheat_floor_without_signals(self):
        """Without reheating signals, log model returns its raw (low) estimate."""
        coordinator = self._make_warmed_up_coordinator()
        coordinator._last_shower_temp_c = 47.2
        coordinator._last_shower_cold_temp = 9.4
        coordinator._last_shower_flow_lpm = 6.5
        coordinator._last_shower_duration_min = 5.4
        coordinator._last_tap_water_cap = None

        values = {
            "bt30": 49.0,
            "bf1_l_min": 0.0,
            "compressor_state": 0,
            "picpin_relay_heat_l1": False,
            "picpin_relay_heat_l2": False,
            "picpin_relay_heat_l3": False,
        }
        coordinator._calculate_tap_water_cap(values)

        # Without reheating, tank at 49°C vs shower at 47.2°C gives ≈ 1 min raw.
        # EMA is None so raw is used directly; result must be below the floor.
        assert values["tap_water_minutes"] < round(DHW_SHOWER_DURATION_MIN)
