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
