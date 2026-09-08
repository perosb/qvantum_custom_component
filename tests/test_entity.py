"""Tests for qvantum entity helpers."""

from unittest.mock import MagicMock, patch

from custom_components.qvantum.entity import (
    QvantumAccessMixin,
    QvantumEntity,
    async_get_qvantum_device_entry,
    cleanup_disabled_entities,
)
from custom_components.qvantum.const import DOMAIN


class DummyAccessEntity(QvantumAccessMixin):
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._write_access_warning_logged = False


def test_has_write_access_maintenance_entity():
    """Non-QvantumDataUpdateCoordinator should allow write access."""
    non_qvantum_coordinator = MagicMock()
    entity = DummyAccessEntity(non_qvantum_coordinator)

    assert entity._has_write_access is True


def test_has_write_access_denied_in_modbus_mode():
    """Modbus writes stay off until the option is enabled."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.modbus_enabled = True
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator.config_entry.data = {}
    coordinator.config_entry.runtime_data = MagicMock()
    coordinator.config_entry.runtime_data.maintenance_coordinator = None

    class DummyModbusWriteEntity(QvantumAccessMixin):
        def __init__(self, coordinator):
            self.coordinator = coordinator
            self._write_access_warning_logged = False

        def _local_write_available(self):
            return True

    entity = DummyModbusWriteEntity(coordinator)
    assert entity._has_write_access is False


def test_has_write_access_allowed_when_modbus_write_enabled():
    """Direct holding writes are available when Modbus writing is on."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator
    from custom_components.qvantum.const import CONF_MODBUS_TCP, CONF_MODBUS_WRITE

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.modbus_enabled = True
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {
        CONF_MODBUS_TCP: True,
        CONF_MODBUS_WRITE: True,
    }
    coordinator.config_entry.data = {}
    coordinator.config_entry.runtime_data = MagicMock()

    entity = DummyAccessEntity(coordinator)
    entity._metric_key = "indoor_temperature_target"
    assert entity._has_write_access is True


def test_has_write_access_denied_for_smartcontrol_in_modbus_mode():
    """SmartControl has no holding-register write."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator
    from custom_components.qvantum.const import CONF_MODBUS_TCP, CONF_MODBUS_WRITE

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.modbus_enabled = True
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {
        CONF_MODBUS_TCP: True,
        CONF_MODBUS_WRITE: True,
    }
    coordinator.config_entry.data = {}

    entity = DummyAccessEntity(coordinator)
    entity._metric_key = "use_adaptive"
    assert entity._has_write_access is False


def test_has_write_access_denies_http_only_entity_when_cloud_unavailable():
    """HTTP-backed Qvantum entities must not become writable during an outage."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator
    from custom_components.qvantum.const import CONF_MODBUS_TCP, CONF_MODBUS_WRITE

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {
        CONF_MODBUS_TCP: True,
        CONF_MODBUS_WRITE: True,
    }
    coordinator.config_entry.data = {}
    coordinator.config_entry.runtime_data = MagicMock()
    coordinator.config_entry.runtime_data.maintenance_coordinator = None

    entity = QvantumEntity.__new__(QvantumEntity)
    entity.coordinator = coordinator
    entity._write_access_warning_logged = False
    entity._metric_key = "extra_tap_water"

    assert entity._has_write_access is False


def test_has_write_access_allows_modbus_metric_when_cloud_unavailable():
    """Holding-register writes stay available when Modbus writing is enabled."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator
    from custom_components.qvantum.const import CONF_MODBUS_TCP, CONF_MODBUS_WRITE

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {
        CONF_MODBUS_TCP: True,
        CONF_MODBUS_WRITE: True,
    }
    coordinator.config_entry.data = {}
    coordinator.config_entry.runtime_data = MagicMock()
    coordinator.config_entry.runtime_data.maintenance_coordinator = None
    coordinator.modbus_enabled = True

    entity = QvantumEntity.__new__(QvantumEntity)
    entity.coordinator = coordinator
    entity._write_access_warning_logged = False
    entity._metric_key = "room_temp_external"

    assert entity._has_write_access is True


def test_has_write_access_treats_empty_maintenance_data_as_outage():
    """An empty maintenance payload is an outage, not a write denial."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.config_entry = MagicMock()
    maintenance_coordinator = MagicMock()
    maintenance_coordinator.data = {}
    coordinator.config_entry.runtime_data = MagicMock(
        maintenance_coordinator=maintenance_coordinator
    )

    class DummyModbusWriteEntity(QvantumAccessMixin):
        def __init__(self, coordinator):
            self.coordinator = coordinator
            self._write_access_warning_logged = False

        def _local_write_available(self):
            return True

    entity = DummyModbusWriteEntity(coordinator)
    assert entity._has_write_access is True


def test_has_write_access_treats_cleared_access_level_as_outage():
    """Stale firmware data with a cleared access_level uses the local fallback."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.config_entry = MagicMock()
    maintenance_coordinator = MagicMock()
    maintenance_coordinator.data = {
        "firmware_versions": {"display_fw_version": "1.3.6"},
    }
    coordinator.config_entry.runtime_data = MagicMock(
        maintenance_coordinator=maintenance_coordinator
    )

    class DummyModbusWriteEntity(QvantumAccessMixin):
        def __init__(self, coordinator):
            self.coordinator = coordinator
            self._write_access_warning_logged = False

        def _local_write_available(self):
            return True

    entity = DummyModbusWriteEntity(coordinator)
    assert entity._has_write_access is True


def test_has_write_access_denies_without_data():
    """Missing runtime_data should deny write access and log warning once."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.runtime_data = MagicMock()
    coordinator.config_entry.runtime_data.maintenance_coordinator = None

    entity = DummyAccessEntity(coordinator)
    assert entity._has_write_access is False


def test_has_write_access_enabled_when_write_level_sufficient():
    """Write access should be granted with writeAccessLevel >=20."""
    coordinator = MagicMock()
    config_entry = MagicMock()
    maintenance_coordinator = MagicMock()
    maintenance_coordinator.data = {"access_level": {"writeAccessLevel": 20}}
    config_entry.runtime_data = MagicMock(maintenance_coordinator=maintenance_coordinator)
    coordinator.config_entry = config_entry

    entity = DummyAccessEntity(coordinator)
    assert entity._has_write_access is True


def test_has_write_access_uses_live_access_level_on_qvantum_coordinator():
    """A successful cloud check still gates writes on writeAccessLevel."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.config_entry = MagicMock()
    maintenance_coordinator = MagicMock()
    maintenance_coordinator.data = {"access_level": {"writeAccessLevel": 20}}
    coordinator.config_entry.runtime_data = MagicMock(
        maintenance_coordinator=maintenance_coordinator
    )

    entity = DummyAccessEntity(coordinator)
    assert entity._has_write_access is True

    maintenance_coordinator.data = {"access_level": {"writeAccessLevel": 10}}
    assert entity._has_write_access is False

    maintenance_coordinator.data = {"access_level": 0}
    assert entity._has_write_access is False


def test_resolve_device_id_from_identifier():
    """QvantumEntity should resolve device id from identifiers."""
    dummy = QvantumEntity.__new__(QvantumEntity)
    dummy.coordinator = MagicMock()
    device = {"identifiers": {(DOMAIN, "qvantum-test_device_123")}}

    assert dummy._resolve_device_id(device) == "test_device_123"


def test_resolve_device_id_from_coordinator_values():
    """Fallback to coordinator data for hpid."""
    dummy = QvantumEntity.__new__(QvantumEntity)
    dummy.coordinator = MagicMock()
    dummy.coordinator.data = {"values": {"hpid": "test_device_456"}}
    device = {}

    assert dummy._resolve_device_id(device) == "test_device_456"


def test_async_get_qvantum_device_entry_looks_up_by_identifier():
    """Device lookup must use the config-entry-scoped identifier helper."""
    hass = MagicMock()
    mock_registry = MagicMock()
    mock_device = MagicMock()
    mock_registry.async_get_device_by_identifier.return_value = mock_device

    with patch(
        "homeassistant.helpers.device_registry.async_get", return_value=mock_registry
    ):
        assert (
            async_get_qvantum_device_entry(hass, "pump-1", "entry-1") is mock_device
        )

    mock_registry.async_get_device_by_identifier.assert_called_once_with(
        (DOMAIN, "qvantum-pump-1"), "entry-1"
    )
    assert async_get_qvantum_device_entry(hass, None, "entry-1") is None
    assert async_get_qvantum_device_entry(hass, "pump-1", None) is None


def test_cleanup_disabled_entities_removes_unsupported_metrics():
    """Stale entities for a known device are removed; current metrics stay."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.device_id = "pump-1"
    coordinator.config_entry.entry_id = "entry-1"

    device_entry = MagicMock()
    device_entry.id = "ha-device-1"

    keep = MagicMock()
    keep.domain = "sensor"
    keep.unique_id = "qvantum_bt1_pump-1"
    keep.entity_id = "sensor.qvantum_bt1"

    stale = MagicMock()
    stale.domain = "sensor"
    stale.unique_id = "qvantum_obsolete_pump-1"
    stale.entity_id = "sensor.qvantum_obsolete"

    other_domain = MagicMock()
    other_domain.domain = "switch"
    other_domain.unique_id = "qvantum_obsolete_pump-1"
    other_domain.entity_id = "switch.qvantum_obsolete"

    mock_entity_registry = MagicMock()
    mock_entity_registry.entities.get_entries_for_device_id.return_value = [
        keep,
        stale,
        other_domain,
    ]

    with (
        patch(
            "custom_components.qvantum.entity.async_get_qvantum_device_entry",
            return_value=device_entry,
        ),
        patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_entity_registry,
        ),
    ):
        cleanup_disabled_entities(hass, coordinator, {"bt1"}, "sensor")

    mock_entity_registry.async_remove.assert_called_once_with("sensor.qvantum_obsolete")


def test_cleanup_disabled_entities_skips_when_device_missing():
    """Cleanup is a no-op when the heat pump is not in the device registry."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.device_id = "pump-1"
    coordinator.config_entry.entry_id = "entry-1"
    mock_entity_registry = MagicMock()

    with (
        patch(
            "custom_components.qvantum.entity.async_get_qvantum_device_entry",
            return_value=None,
        ),
        patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_entity_registry,
        ),
    ):
        cleanup_disabled_entities(hass, coordinator, {"bt1"}, "sensor")

    mock_entity_registry.async_remove.assert_not_called()
