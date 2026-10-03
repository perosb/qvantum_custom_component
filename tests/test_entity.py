"""Tests for qvantum entity helpers."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.qvantum.entity import (
    QvantumAccessMixin,
    QvantumEntity,
    async_get_qvantum_device_entry,
    cleanup_disabled_entities,
    finalize_platform_setup,
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


def test_coordinator_available_reflects_last_update_success():
    """An entity is unavailable while the coordinator's last poll failed."""
    coordinator = MagicMock()
    coordinator.last_update_success = False
    entity = DummyAccessEntity(coordinator)

    assert entity._coordinator_available is False

    coordinator.last_update_success = True
    assert entity._coordinator_available is True


def test_coordinator_available_defaults_true_when_attribute_missing():
    """Coordinators without the attribute (tests, __new__) default available."""
    entity = DummyAccessEntity(object())

    assert entity._coordinator_available is True


class _WriteAccessStub(QvantumAccessMixin):
    """Minimal entity whose write access is fixed for guard tests."""

    def __init__(self, *, allowed: bool) -> None:
        self.coordinator = None
        self._write_access_warning_logged = False
        self._allowed = allowed

    @property
    def _has_write_access(self) -> bool:
        return self._allowed


def test_require_write_access_raises_when_denied():
    """Write methods must refuse to run without write access."""
    from homeassistant.exceptions import HomeAssistantError

    entity = _WriteAccessStub(allowed=False)

    with pytest.raises(HomeAssistantError, match="Write access is not enabled"):
        entity._require_write_access()


def test_require_write_access_passes_when_allowed():
    """Write methods are allowed to run with write access."""
    entity = _WriteAccessStub(allowed=True)

    entity._require_write_access()


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


def test_async_get_qvantum_device_entry_ignores_other_config_entry_device():
    """A device owned by another config entry must not be returned for ours.

    Simulate HA's scoped identifier lookup: the device exists under a foreign
    entry_id, so async_get_device_by_identifier returns None for our entry.
    """
    hass = MagicMock()
    mock_registry = MagicMock()
    foreign_device = MagicMock()
    foreign_device.id = "foreign-ha-device"

    def scoped_lookup(identifier, config_entry_id):
        # Device is registered only under another config entry.
        if config_entry_id == "other-entry":
            return foreign_device
        return None

    mock_registry.async_get_device_by_identifier.side_effect = scoped_lookup

    with patch(
        "homeassistant.helpers.device_registry.async_get", return_value=mock_registry
    ):
        assert async_get_qvantum_device_entry(hass, "pump-1", "entry-1") is None
        assert (
            async_get_qvantum_device_entry(hass, "pump-1", "other-entry")
            is foreign_device
        )

    mock_registry.async_get_device_by_identifier.assert_any_call(
        (DOMAIN, "qvantum-pump-1"), "entry-1"
    )


def test_cleanup_disabled_entities_ignores_other_config_entry_device():
    """Cleanup must not touch entities when the heat pump belongs to another entry."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.device_id = "pump-1"
    coordinator.config_entry.entry_id = "entry-1"
    # Prefer public config_entry; also clear private alias so helper stays consistent.
    coordinator._config_entry = None

    mock_registry = MagicMock()

    def scoped_lookup(identifier, config_entry_id):
        # Same identifier exists, but only under a foreign config entry.
        if config_entry_id == "other-entry":
            return MagicMock(id="foreign-device")
        return None

    mock_registry.async_get_device_by_identifier.side_effect = scoped_lookup
    mock_entity_registry = MagicMock()

    with (
        patch(
            "homeassistant.helpers.device_registry.async_get",
            return_value=mock_registry,
        ),
        patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_entity_registry,
        ),
    ):
        cleanup_disabled_entities(hass, coordinator, {"bt1"}, "sensor")

    mock_registry.async_get_device_by_identifier.assert_called_once_with(
        (DOMAIN, "qvantum-pump-1"), "entry-1"
    )
    mock_entity_registry.async_remove.assert_not_called()


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

    mock_entity_registry.entities.get_entries_for_device_id.assert_called_once_with(
        "ha-device-1", True
    )
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


def test_finalize_platform_setup_registers_disables_and_cleans():
    """The shared platform tail adds, disables by default, and prunes."""
    hass = MagicMock()
    coordinator = MagicMock()
    async_add_entities = MagicMock()
    entities = [MagicMock()]

    with (
        patch("custom_components.qvantum.entity.disable_entities_by_default") as disable,
        patch("custom_components.qvantum.entity.cleanup_disabled_entities") as cleanup,
    ):
        finalize_platform_setup(
            hass,
            coordinator,
            async_add_entities,
            entities,
            {"bt1"},
            "sensor",
            disable_by_default=True,
        )

    async_add_entities.assert_called_once_with(entities)
    disable.assert_called_once_with(hass, entities)
    cleanup.assert_called_once_with(hass, coordinator, {"bt1"}, "sensor")


def test_finalize_platform_setup_skips_disable_by_default():
    """Platforms whose entities are enabled by default skip the disable pass."""
    with (
        patch("custom_components.qvantum.entity.disable_entities_by_default") as disable,
        patch("custom_components.qvantum.entity.cleanup_disabled_entities"),
    ):
        finalize_platform_setup(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            [],
            set(),
            "switch",
        )

    disable.assert_not_called()
