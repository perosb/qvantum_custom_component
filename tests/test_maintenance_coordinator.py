"""Tests for Qvantum maintenance coordinator."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from custom_components.qvantum.maintenance_coordinator import (
    QvantumMaintenanceCoordinator,
)


class TestQvantumMaintenanceCoordinator:
    """Test the maintenance coordinator that handles firmware updates and access level monitoring."""

    @pytest.fixture
    def mock_config_entry(self):
        """Mock ConfigEntry instance."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.domain = "qvantum"
        entry.unique_id = "test_unique_id"
        return entry

    @pytest.fixture
    def mock_main_coordinator(self):
        """Mock main coordinator with device data."""
        coordinator = MagicMock()
        coordinator.data = {
            "device": {
                "id": "test_device_123",
                "model": "QE-6",
                "vendor": "Qvantum",
                "device_metadata": {
                    "display_fw_version": "1.3.6",
                    "cc_fw_version": "140",
                    "inv_fw_version": "140",
                },
            }
        }
        coordinator._device = coordinator.data["device"]
        return coordinator

    @pytest_asyncio.fixture
    async def maintenance_coordinator(
        self, hass, mock_config_entry, mock_main_coordinator
    ):
        """Create maintenance coordinator instance."""
        # Set up hass.data with API instance
        from custom_components.qvantum.const import DOMAIN

        mock_api = MagicMock()
        hass.data[DOMAIN] = mock_api

        # Patch frame.report_usage to avoid frame helper issues in tests
        with patch("homeassistant.helpers.frame.report_usage"):
            coordinator = QvantumMaintenanceCoordinator(
                hass=hass,
                config_entry=mock_config_entry,
                main_coordinator=mock_main_coordinator,
            )
        return coordinator

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_no_device(
        self, maintenance_coordinator, mock_main_coordinator
    ):
        """Test firmware check when no device is available."""
        mock_main_coordinator._device = None

        result = await maintenance_coordinator.async_check_firmware_updates()

        assert result == {}

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_no_device_id(
        self, maintenance_coordinator, mock_main_coordinator
    ):
        """Test firmware check when device has no ID."""
        mock_main_coordinator._device = {"model": "QE-6"}

        result = await maintenance_coordinator.async_check_firmware_updates()

        assert result == {}

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_initial_versions(
        self, maintenance_coordinator, mock_main_coordinator
    ):
        """Test firmware check with initial version detection."""
        # Mock the API call
        maintenance_coordinator.api.get_device_metadata = AsyncMock(
            return_value={
                "device_metadata": {
                    "display_fw_version": "1.3.6",
                    "cc_fw_version": "140",
                    "inv_fw_version": "140",
                }
            }
        )
        maintenance_coordinator.api.get_access_level = AsyncMock(
            return_value={
                "readAccessLevel": 20,
                "writeAccessLevel": 20,
                "expiresAt": "2026-01-26T18:35:29.768Z",
            }
        )

        result = await maintenance_coordinator.async_check_firmware_updates()

        assert result["device_id"] == "test_device_123"
        assert result["firmware_versions"]["display_fw_version"] == "1.3.6"
        assert result["firmware_versions"]["cc_fw_version"] == "140"
        assert result["firmware_versions"]["inv_fw_version"] == "140"
        assert result["access_level"]["expiresAt"] == "2026-01-26T18:35:29.768Z"
        assert result["firmware_changed"] is False
        assert "last_check" in result

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_version_change(
        self, maintenance_coordinator, mock_main_coordinator
    ):
        """Test firmware check with version changes detected."""
        # Set initial versions
        maintenance_coordinator._last_firmware_versions = {
            "display_fw_version": "1.3.5",
            "cc_fw_version": "139",
            "inv_fw_version": "139",
        }

        # Mock the API call with updated versions
        maintenance_coordinator.api.get_device_metadata = AsyncMock(
            return_value={
                "device_metadata": {
                    "display_fw_version": "1.3.6",
                    "cc_fw_version": "140",
                    "inv_fw_version": "140",
                }
            }
        )
        maintenance_coordinator.api.get_access_level = AsyncMock(
            return_value={
                "readAccessLevel": 20,
                "writeAccessLevel": 20,
                "expiresAt": "2026-01-26T18:35:29.768Z",
            }
        )

        with patch.object(
            maintenance_coordinator,
            "_create_firmware_update_notifications",
            new_callable=AsyncMock,
        ) as mock_notifications:
            result = await maintenance_coordinator.async_check_firmware_updates()

            assert result["device_id"] == "test_device_123"
            assert result["firmware_changed"] is True
            assert len(result["firmware_versions"]) == 3

            # Verify notifications were called
            mock_notifications.assert_called_once()
            call_args = mock_notifications.call_args[0]
            assert call_args[0] == "test_device_123"
            firmware_changes = call_args[1]
            assert len(firmware_changes) == 3  # Three firmware changes

            # Verify the correct components changed
            expected_changes = {
                "display_fw_version": {"from_version": "1.3.5", "to_version": "1.3.6"},
                "cc_fw_version": {"from_version": "139", "to_version": "140"},
                "inv_fw_version": {"from_version": "139", "to_version": "140"},
            }

            for change in firmware_changes:
                component = change["component"]
                assert component in expected_changes
                assert (
                    change["from_version"]
                    == expected_changes[component]["from_version"]
                )
                assert change["to_version"] == expected_changes[component]["to_version"]

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_api_error(
        self, maintenance_coordinator
    ):
        """Test firmware check with API authentication error."""
        maintenance_coordinator.api.get_device_metadata = AsyncMock(
            side_effect=Exception("API Error")
        )

        with pytest.raises(Exception):
            await maintenance_coordinator.async_check_firmware_updates()

    @pytest.mark.asyncio
    async def test_create_firmware_update_notifications(
        self, maintenance_coordinator, mock_main_coordinator
    ):
        """Test creating firmware update notifications."""
        firmware_changes = [
            {
                "component": "display_fw_version",
                "from_version": "1.3.5",
                "to_version": "1.3.6",
            }
        ]

        with (
            patch.object(
                maintenance_coordinator.hass.services,
                "async_call",
                new_callable=AsyncMock,
            ) as mock_async_call,
            patch.object(
                maintenance_coordinator,
                "_update_device_registry_firmware_versions",
                new_callable=AsyncMock,
            ) as mock_update_registry,
        ):
            await maintenance_coordinator._create_firmware_update_notifications(
                "test_device_123", firmware_changes
            )

            # Verify notification was created
            mock_async_call.assert_called_once()
            call_args = mock_async_call.call_args
            assert call_args[0][0] == "persistent_notification"  # domain
            assert call_args[0][1] == "create"  # service
            service_data = call_args[0][2]  # service data
            assert "Qvantum Firmware Updated" in service_data["title"]
            assert "1.3.5 → 1.3.6" in service_data["message"]

            # Verify device registry was updated
            mock_update_registry.assert_called_once_with("test_device_123")

    @pytest.mark.asyncio
    async def test_update_device_registry_firmware_versions_success(
        self, maintenance_coordinator
    ):
        """Test successful device registry firmware version update."""
        # Set up firmware coordinator data
        maintenance_coordinator.data = {
            "firmware_versions": {
                "display_fw_version": "1.3.6",
                "cc_fw_version": "140",
                "inv_fw_version": "140",
            }
        }

        # Mock device registry
        mock_device_registry = MagicMock()
        mock_device_entry = MagicMock()
        mock_device_entry.id = "device_id_123"
        mock_device_entry.identifiers = {("qvantum", "qvantum-test_device_123")}
        mock_device_registry.devices.values.return_value = [mock_device_entry]
        mock_device_registry.async_update_device = MagicMock()

        with patch(
            "custom_components.qvantum.maintenance_coordinator.async_get",
            return_value=mock_device_registry,
        ):
            await maintenance_coordinator._update_device_registry_firmware_versions(
                "test_device_123"
            )

            # Verify device registry was updated
            mock_device_registry.async_update_device.assert_called_once_with(
                "device_id_123", sw_version="1.3.6/140/140"
            )

    @pytest.mark.asyncio
    async def test_update_device_registry_firmware_versions_no_device(
        self, maintenance_coordinator
    ):
        """Test device registry update when device is not found."""
        # Mock device registry with no matching device
        mock_device_registry = MagicMock()
        mock_device_registry.devices.values.return_value = []

        with patch(
            "custom_components.qvantum.maintenance_coordinator.async_get",
            return_value=mock_device_registry,
        ):
            await maintenance_coordinator._update_device_registry_firmware_versions(
                "test_device_123"
            )

            # Verify no update was attempted
            mock_device_registry.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_device_registry_firmware_versions_incomplete_data(
        self, maintenance_coordinator
    ):
        """Test device registry update with incomplete firmware data."""
        # Set up incomplete firmware coordinator data
        maintenance_coordinator.data = {
            "firmware_versions": {
                "display_fw_version": "1.3.6",
                "cc_fw_version": "140",
                # Missing inv_fw_version
            }
        }

        # Mock device registry
        mock_device_registry = MagicMock()
        mock_device_entry = MagicMock()
        mock_device_entry.id = "device_id_123"
        mock_device_registry.devices.values.return_value = [mock_device_entry]

        with patch(
            "custom_components.qvantum.maintenance_coordinator.async_get",
            return_value=mock_device_registry,
        ):
            await maintenance_coordinator._update_device_registry_firmware_versions(
                "test_device_123"
            )

            # Verify no update was attempted due to incomplete data
            mock_device_registry.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_device_registry_firmware_versions_error(
        self, maintenance_coordinator
    ):
        """Test device registry update with error handling."""
        # Set up firmware coordinator data
        maintenance_coordinator.data = {
            "firmware_versions": {
                "display_fw_version": "1.3.6",
                "cc_fw_version": "140",
                "inv_fw_version": "140",
            }
        }

        # Mock device registry to raise an exception
        mock_device_registry = MagicMock()
        mock_device_registry.devices.values.side_effect = Exception("Registry error")

        with patch(
            "custom_components.qvantum.maintenance_coordinator.async_get",
            return_value=mock_device_registry,
        ):
            # Should not raise exception, just log error
            await maintenance_coordinator._update_device_registry_firmware_versions(
                "test_device_123"
            )
