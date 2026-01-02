"""Tests for Qvantum firmware coordinator."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from custom_components.qvantum.firmware_coordinator import (
    QvantumFirmwareUpdateCoordinator,
)


class TestQvantumFirmwareUpdateCoordinator:
    """Test the firmware update coordinator."""

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
    async def firmware_coordinator(
        self, hass, mock_config_entry, mock_main_coordinator
    ):
        """Create firmware coordinator instance."""
        # Set up hass.data with API instance
        from custom_components.qvantum.const import DOMAIN

        mock_api = MagicMock()
        hass.data[DOMAIN] = mock_api

        # Patch frame.report_usage to avoid frame helper issues in tests
        with patch("homeassistant.helpers.frame.report_usage"):
            coordinator = QvantumFirmwareUpdateCoordinator(
                hass=hass,
                config_entry=mock_config_entry,
                main_coordinator=mock_main_coordinator,
            )
        return coordinator

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_no_device(
        self, firmware_coordinator, mock_main_coordinator
    ):
        """Test firmware check when no device is available."""
        mock_main_coordinator._device = None

        result = await firmware_coordinator.async_check_firmware_updates()

        assert result == {}

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_no_device_id(
        self, firmware_coordinator, mock_main_coordinator
    ):
        """Test firmware check when device has no ID."""
        mock_main_coordinator._device = {"model": "QE-6"}

        result = await firmware_coordinator.async_check_firmware_updates()

        assert result == {}

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_initial_versions(
        self, firmware_coordinator, mock_main_coordinator
    ):
        """Test firmware check with initial version detection."""
        # Mock the API call
        firmware_coordinator.api.get_device_metadata = AsyncMock(
            return_value={
                "device_metadata": {
                    "display_fw_version": "1.3.6",
                    "cc_fw_version": "140",
                    "inv_fw_version": "140",
                }
            }
        )

        result = await firmware_coordinator.async_check_firmware_updates()

        assert result["device_id"] == "test_device_123"
        assert result["firmware_versions"]["display_fw_version"] == "1.3.6"
        assert result["firmware_versions"]["cc_fw_version"] == "140"
        assert result["firmware_versions"]["inv_fw_version"] == "140"
        assert result["firmware_changed"] is False
        assert "last_check" in result

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_version_change(
        self, firmware_coordinator, mock_main_coordinator
    ):
        """Test firmware check with version changes detected."""
        # Set initial versions
        firmware_coordinator._last_firmware_versions = {
            "display_fw_version": "1.3.5",
            "cc_fw_version": "139",
            "inv_fw_version": "139",
        }

        # Mock the API call with updated versions
        firmware_coordinator.api.get_device_metadata = AsyncMock(
            return_value={
                "device_metadata": {
                    "display_fw_version": "1.3.6",
                    "cc_fw_version": "140",
                    "inv_fw_version": "140",
                }
            }
        )

        with patch.object(
            firmware_coordinator,
            "_create_firmware_update_notifications",
            new_callable=AsyncMock,
        ) as mock_notifications:
            result = await firmware_coordinator.async_check_firmware_updates()

            assert result["device_id"] == "test_device_123"
            assert result["firmware_changed"] is True
            assert len(result["firmware_versions"]) == 3

            # Verify notifications were called
            mock_notifications.assert_called_once()
            call_args = mock_notifications.call_args[0]
            assert call_args[0] == "test_device_123"
            assert len(call_args[1]) == 3  # Three firmware changes

    @pytest.mark.asyncio
    async def test_async_check_firmware_updates_api_error(self, firmware_coordinator):
        """Test firmware check with API authentication error."""
        firmware_coordinator.api.get_device_metadata = AsyncMock(
            side_effect=Exception("API Error")
        )

        with pytest.raises(Exception):
            await firmware_coordinator.async_check_firmware_updates()

    @pytest.mark.asyncio
    async def test_create_firmware_update_notifications(
        self, firmware_coordinator, mock_main_coordinator
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
            patch(
                "custom_components.qvantum.firmware_coordinator.async_create",
                new_callable=AsyncMock,
            ) as mock_async_create,
            patch.object(
                firmware_coordinator,
                "_update_device_registry_firmware_versions",
                new_callable=AsyncMock,
            ) as mock_update_registry,
        ):
            await firmware_coordinator._create_firmware_update_notifications(
                "test_device_123", firmware_changes
            )

            # Verify notification was created
            mock_async_create.assert_called_once()
            call_args = mock_async_create.call_args
            assert "Qvantum Firmware Updated" in call_args[1]["title"]
            assert (
                "1.3.5 → 1.3.6" in call_args[0][1]
            )  # message is second positional arg

            # Verify device registry was updated
            mock_update_registry.assert_called_once_with("test_device_123")

    @pytest.mark.asyncio
    async def test_update_device_registry_firmware_versions_success(
        self, firmware_coordinator
    ):
        """Test successful device registry firmware version update."""
        # Set up firmware coordinator data
        firmware_coordinator.data = {
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
            "custom_components.qvantum.firmware_coordinator.async_get",
            return_value=mock_device_registry,
        ):
            await firmware_coordinator._update_device_registry_firmware_versions(
                "test_device_123"
            )

            # Verify device registry was updated
            mock_device_registry.async_update_device.assert_called_once_with(
                "device_id_123", sw_version="1.3.6/140/140"
            )

    @pytest.mark.asyncio
    async def test_update_device_registry_firmware_versions_no_device(
        self, firmware_coordinator
    ):
        """Test device registry update when device is not found."""
        # Mock device registry with no matching device
        mock_device_registry = MagicMock()
        mock_device_registry.devices.values.return_value = []

        with patch(
            "custom_components.qvantum.firmware_coordinator.async_get",
            return_value=mock_device_registry,
        ):
            await firmware_coordinator._update_device_registry_firmware_versions(
                "test_device_123"
            )

            # Verify no update was attempted
            mock_device_registry.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_device_registry_firmware_versions_incomplete_data(
        self, firmware_coordinator
    ):
        """Test device registry update with incomplete firmware data."""
        # Set up incomplete firmware coordinator data
        firmware_coordinator.data = {
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
            "custom_components.qvantum.firmware_coordinator.async_get",
            return_value=mock_device_registry,
        ):
            await firmware_coordinator._update_device_registry_firmware_versions(
                "test_device_123"
            )

            # Verify no update was attempted due to incomplete data
            mock_device_registry.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_device_registry_firmware_versions_error(
        self, firmware_coordinator
    ):
        """Test device registry update with error handling."""
        # Set up firmware coordinator data
        firmware_coordinator.data = {
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
            "custom_components.qvantum.firmware_coordinator.async_get",
            return_value=mock_device_registry,
        ):
            # Should not raise exception, just log error
            await firmware_coordinator._update_device_registry_firmware_versions(
                "test_device_123"
            )
