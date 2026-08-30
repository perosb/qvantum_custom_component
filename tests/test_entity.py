"""Tests for qvantum entity helpers."""

from unittest.mock import MagicMock

from custom_components.qvantum.entity import QvantumAccessMixin, QvantumEntity
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
    """Modbus mode has no writes in this phase, even if cloud access is missing."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator

    coordinator = QvantumDataUpdateCoordinator.__new__(QvantumDataUpdateCoordinator)
    coordinator.modbus_enabled = True
    coordinator.config_entry = MagicMock()
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


def test_has_write_access_denies_modbus_metric_in_modbus_mode():
    """Holding-register writes are still gated off in Modbus mode for now."""
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

    assert entity._has_write_access is False


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
