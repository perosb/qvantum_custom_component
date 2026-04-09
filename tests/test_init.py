"""Tests for Qvantum integration setup."""

import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Import real functions before mocking
from custom_components.qvantum import (
    async_setup_entry,
    async_unload_entry,
    async_remove_config_entry_device,
    async_migrate_entry,
    PLATFORMS,
    _async_update_listener,
)

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

        mock_config_entry.add_update_listener = MagicMock()

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
            # Verify that all platforms are forwarded
            hass.config_entries.async_forward_entry_setups.assert_called_once_with(
                mock_config_entry, PLATFORMS
            )
            mock_config_entry.add_update_listener.assert_called_once_with(
                _async_update_listener
            )

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
        # Setup hass.data as API object
        mock_api = MagicMock()
        mock_api.close = AsyncMock()
        hass.data["qvantum"] = mock_api

        result = await async_unload_entry(hass, mock_config_entry)

        assert result is True
        hass.config_entries.async_unload_platforms.assert_called_once()
        mock_api.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_unload_entry_with_firmware_notifications(
        self, hass, mock_config_entry
    ):
        """Test unloading the integration clears firmware notifications."""
        # Setup mock platforms
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        mock_api = MagicMock()
        mock_api.close = AsyncMock()
        hass.data["qvantum"] = mock_api

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
            mock_api.close.assert_awaited_once()

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

    @pytest.mark.asyncio
    async def test_async_unload_entry_with_nonawaitable_dismiss(
        self, hass, mock_config_entry
    ):
        """Test unloading handles non-awaitable async_dismiss safely."""
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        mock_firmware_coordinator = MagicMock()
        mock_main_coordinator = MagicMock()
        mock_main_coordinator._device = {"id": "test_device_123"}
        mock_firmware_coordinator.main_coordinator = mock_main_coordinator

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.maintenance_coordinator = (
            mock_firmware_coordinator
        )

        with patch(
            "custom_components.qvantum.async_dismiss", MagicMock(return_value=None)
        ):
            result = await async_unload_entry(hass, mock_config_entry)

            assert result is True
            hass.config_entries.async_unload_platforms.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_update_listener(self, hass, mock_config_entry):
        hass.config_entries.async_reload = AsyncMock()

        await _async_update_listener(hass, mock_config_entry)

        hass.config_entries.async_reload.assert_called_once_with(mock_config_entry.entry_id)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_device_metadata_fails(self, hass, mock_config_entry):
        mock_api = MagicMock()
        mock_api.get_primary_device = AsyncMock(return_value={"id": "device123"})

        mock_coordinator = MagicMock()
        mock_coordinator.data = {"device": {}}
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=[None, None])

        with patch("custom_components.qvantum.QvantumAPI", return_value=mock_api), \
            patch("custom_components.qvantum.QvantumDataUpdateCoordinator", return_value=mock_coordinator), \
            patch("custom_components.qvantum.QvantumMaintenanceCoordinator", return_value=MagicMock()), \
            patch("custom_components.qvantum.services.async_setup_services"):
            result = await async_setup_entry(hass, mock_config_entry)

        assert result is False


    @pytest.mark.asyncio
    async def test_async_remove_config_entry_device(self, hass, mock_config_entry):
        result = await async_remove_config_entry_device(
            hass, mock_config_entry, MagicMock()
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_async_migrate_entry_non_legacy(self, hass, mock_config_entry):
        config_entry = MagicMock(version=7, minor_version=0)

        result = await async_migrate_entry(hass, config_entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_async_migrate_entry_future_version(self, hass, mock_config_entry):
        config_entry = MagicMock(version=99, minor_version=0)

        result = await async_migrate_entry(hass, config_entry)

        assert result is False

    @pytest.mark.asyncio
    async def test_async_migrate_entry_from_v4_to_v5(self, hass, mock_config_entry):
        config_entry = MagicMock(version=4, minor_version=0, entry_id="test")

        mock_ent_reg = MagicMock()
        mock_ent_reg.entities.values.return_value = []

        with patch(
            "custom_components.qvantum.async_migrate_entries",
            new_callable=AsyncMock,
        ) as mock_migrate:
            with patch(
                "custom_components.qvantum.async_get_entity_registry",
                return_value=mock_ent_reg,
            ):
                with patch.object(
                    hass.config_entries, "async_update_entry"
                ) as mock_update:
                    result = await async_migrate_entry(hass, config_entry)

                    assert result is True
                    # v5 numbers, v6, v7 (v5 sensors now uses entity registry directly)
                    assert mock_migrate.call_count == 3
                    mock_update.assert_called_once_with(config_entry, version=7)

    @pytest.mark.asyncio
    async def test_async_migrate_entry_legacy(self, hass, mock_config_entry):
        config_entry = MagicMock(version=1, minor_version=0, entry_id="test")

        mock_ent_reg = MagicMock()
        mock_ent_reg.entities.values.return_value = []

        with patch(
            "custom_components.qvantum.async_migrate_entries",
            new_callable=AsyncMock,
        ) as mock_migrate:
            with patch(
                "custom_components.qvantum.async_get_entity_registry",
                return_value=mock_ent_reg,
            ):
                with patch.object(
                    hass.config_entries, "async_update_entry"
                ) as mock_update:
                    result = await async_migrate_entry(hass, config_entry)

                    assert result is True
                    # v1, v5 numbers, v6, v7 (v5 sensors now uses entity registry directly)
                    assert mock_migrate.call_count == 4
                    mock_update.assert_called_once_with(config_entry, version=7)

                    # Verify migration calls were made with correct arguments
                    assert len(mock_migrate.call_args_list) == 4

                    first_call_args = mock_migrate.call_args_list[0].args
                    _, first_entry_id, first_migration_fn = first_call_args
                    assert first_entry_id == config_entry.entry_id
                    assert callable(first_migration_fn)

                    second_call_args = mock_migrate.call_args_list[1].args
                    _, second_entry_id, second_migration_fn = second_call_args
                    assert second_entry_id == config_entry.entry_id
                    assert callable(second_migration_fn)


class TestMigrateToV5Callbacks:
    """Test the v5 migration callback functions directly by capturing them."""

    def _make_entity_entry(self, domain, unique_id, entity_id=None):
        """Build a minimal mock entity entry."""
        entry = MagicMock()
        entry.domain = domain
        entry.unique_id = unique_id
        entry.entity_id = entity_id or f"{domain}.qvantum_test"
        return entry

    async def _capture_v5_callbacks(self, hass, version=4):
        """Run async_migrate_entry for a version-*version* entry and return the
        list of callback functions passed to async_migrate_entries."""
        config_entry = MagicMock(version=version, minor_version=0, entry_id="test")
        captured = []

        async def capture_migrate(h, entry_id, fn):
            captured.append(fn)

        mock_ent_reg = MagicMock()
        mock_ent_reg.entities.values.return_value = []

        with patch(
            "custom_components.qvantum.async_migrate_entries",
            side_effect=capture_migrate,
        ):
            with patch(
                "custom_components.qvantum.async_get_entity_registry",
                return_value=mock_ent_reg,
            ):
                with patch.object(hass.config_entries, "async_update_entry"):
                    await async_migrate_entry(hass, config_entry)

        return captured

    # ------------------------------------------------------------------
    # migrate_to_v5_number_unique_ids  (first v5 pass, index 0 from v4)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_v5_number_cb_renames_dhw_normal_start(self, hass):
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]  # first pass: numbers

        entry = self._make_entity_entry(
            "number", "qvantum_dhw_normal_start_1011074250800138"
        )
        result = cb(entry)

        assert result == {
            "new_unique_id": "qvantum_number_tap_water_start_1011074250800138"
        }

    @pytest.mark.asyncio
    async def test_v5_number_cb_renames_dhw_normal_stop(self, hass):
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]

        entry = self._make_entity_entry(
            "number", "qvantum_dhw_normal_stop_1011074250800138"
        )
        result = cb(entry)

        assert result == {
            "new_unique_id": "qvantum_number_tap_water_stop_1011074250800138"
        }

    @pytest.mark.asyncio
    async def test_v5_number_cb_prefixes_plain_number_entity(self, hass):
        """A number entity without a dhw rename still gets the qvantum_number_ prefix."""
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]

        entry = self._make_entity_entry(
            "number", "qvantum_tap_water_cap_1011074250800138"
        )
        result = cb(entry)

        assert result == {
            "new_unique_id": "qvantum_number_tap_water_cap_1011074250800138"
        }

    @pytest.mark.asyncio
    async def test_v5_number_cb_skips_already_prefixed(self, hass):
        """Already-prefixed entries must not be modified."""
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]

        entry = self._make_entity_entry(
            "number", "qvantum_number_tap_water_start_1011074250800138"
        )
        result = cb(entry)

        assert result is None

    @pytest.mark.asyncio
    async def test_v5_number_cb_skips_non_number_domains(self, hass):
        """Sensor entities must be ignored by the number pre-migration pass."""
        callbacks = await self._capture_v5_callbacks(hass, version=4)
        cb = callbacks[0]

        entry = self._make_entity_entry(
            "sensor", "qvantum_dhw_normal_start_1011074250800138"
        )
        result = cb(entry)

        assert result is None

    # ------------------------------------------------------------------
    # v5 sensor entity migration via entity registry (direct, not callback)
    # ------------------------------------------------------------------

    def _make_ent_reg_entry(
        self, domain, unique_id, entity_id=None, config_entry_id="test"
    ):
        """Create a minimal mock entity registry entry."""
        entry = MagicMock()
        entry.domain = domain
        entry.unique_id = unique_id
        entry.entity_id = entity_id or f"{domain}.qvantum_test"
        entry.config_entry_id = config_entry_id
        return entry

    async def _run_v5_sensor_migration(self, hass, entities):
        """Run async_migrate_entry for v4 with a mocked entity registry
        and return the registry mock."""
        mock_ent_reg = MagicMock()
        mock_ent_reg.entities.values.return_value = entities

        config_entry = MagicMock(version=4, minor_version=0, entry_id="test")
        with patch(
            "custom_components.qvantum.async_migrate_entries", new_callable=AsyncMock
        ):
            with patch(
                "custom_components.qvantum.async_get_entity_registry",
                return_value=mock_ent_reg,
            ):
                with patch.object(hass.config_entries, "async_update_entry"):
                    await async_migrate_entry(hass, config_entry)
        return mock_ent_reg

    @pytest.mark.asyncio
    async def test_v5_sensor_renames_dhw_normal_start(self, hass):
        """Sensor with dhw_normal_start unique_id is renamed to tap_water_start."""
        entity = self._make_ent_reg_entry(
            "sensor",
            "qvantum_dhw_normal_start_1011074250800138",
            entity_id="sensor.qvantum_hot_water_tank_lower_limit",
        )
        ent_reg = await self._run_v5_sensor_migration(hass, [entity])

        ent_reg.async_update_entity.assert_called_once_with(
            "sensor.qvantum_hot_water_tank_lower_limit",
            new_unique_id="qvantum_tap_water_start_1011074250800138",
        )
        ent_reg.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_v5_sensor_renames_dhw_normal_stop(self, hass):
        """Sensor with dhw_normal_stop unique_id is renamed to tap_water_stop."""
        entity = self._make_ent_reg_entry(
            "sensor",
            "qvantum_dhw_normal_stop_1011074250800138",
            entity_id="sensor.qvantum_hot_water_tank_upper_limit",
        )
        ent_reg = await self._run_v5_sensor_migration(hass, [entity])

        ent_reg.async_update_entity.assert_called_once_with(
            "sensor.qvantum_hot_water_tank_upper_limit",
            new_unique_id="qvantum_tap_water_stop_1011074250800138",
        )
        ent_reg.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_v5_sensor_skips_number_domain(self, hass):
        """Number entities are skipped by the sensor rename pass."""
        entity = self._make_ent_reg_entry(
            "number", "qvantum_dhw_normal_start_1011074250800138"
        )
        ent_reg = await self._run_v5_sensor_migration(hass, [entity])

        ent_reg.async_update_entity.assert_not_called()
        ent_reg.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_v5_sensor_no_change_when_no_dhw_key(self, hass):
        """Sensors without dhw_normal keys are left untouched."""
        entity = self._make_ent_reg_entry("sensor", "qvantum_bt1_1011074250800138")
        ent_reg = await self._run_v5_sensor_migration(hass, [entity])

        ent_reg.async_update_entity.assert_not_called()
        ent_reg.async_remove.assert_not_called()

    # ------------------------------------------------------------------
    # Collision scenario: stale sensor removed when target unique_id taken
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_v5_sensor_removes_orphan_when_target_uid_taken(self, hass):
        """When both a stale entity (dhw_normal_start) and an active entity
        already holding the target unique_id (tap_water_start) exist, the stale
        entity is removed rather than causing a ValueError."""
        device_id = "1011074250800138"

        # Active sensor already has the target unique_id
        active = self._make_ent_reg_entry(
            "sensor",
            f"qvantum_tap_water_start_{device_id}",
            entity_id="sensor.qvantum_hot_water_tank_lower_temperature_limit",
        )
        # Stale/orphaned sensor still has the old dhw unique_id
        stale = self._make_ent_reg_entry(
            "sensor",
            f"qvantum_dhw_normal_start_{device_id}",
            entity_id="sensor.qvantum_hot_water_tank_lower_limit",
        )

        ent_reg = await self._run_v5_sensor_migration(hass, [active, stale])

        # The orphan must be removed, not renamed
        ent_reg.async_remove.assert_called_once_with(
            "sensor.qvantum_hot_water_tank_lower_limit"
        )
        # The active entity must remain in the registry and not be removed
        assert active.entity_id not in [
            call.args[0] for call in ent_reg.async_remove.call_args_list
        ]
        # The active entity must not be touched
        ent_reg.async_update_entity.assert_not_called()
