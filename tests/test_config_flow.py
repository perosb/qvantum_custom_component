"""Tests for Qvantum config flow."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.qvantum.config_flow import (
    QvantumConfigFlow,
    CannotConnect,
    InvalidAuth,
    validate_input,
)
from custom_components.qvantum.const import (
    DEFAULT_MODBUS_SCAN_INTERVAL,
    MIN_MODBUS_SCAN_INTERVAL,
)


class TestValidateInput:
    """Test the validate_input function."""

    @pytest.mark.asyncio
    async def test_validate_input_success(self, hass):
        """Test validate_input with successful authentication."""
        with patch(
            "custom_components.qvantum.config_flow.QvantumAPI"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api_class.return_value = mock_api
            mock_api.authenticate = AsyncMock()
            mock_api.get_primary_device = AsyncMock(
                return_value={"vendor": "Qvantum", "model": "QE-6", "serial": "12345"}
            )
            mock_api.close = AsyncMock()

            result = await validate_input(
                hass, {"username": "test@example.com", "password": "testpass"}
            )

            assert result == {"title": "Qvantum QE-6 (12345)", "serial": "12345"}
            mock_api.authenticate.assert_called_once()
            mock_api.get_primary_device.assert_called_once()
            mock_api.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_input_omits_missing_serial_from_title(self, hass):
        """Title should not include (None) when the device has no serial."""
        with patch(
            "custom_components.qvantum.config_flow.QvantumAPI"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api_class.return_value = mock_api
            mock_api.authenticate = AsyncMock()
            mock_api.get_primary_device = AsyncMock(
                return_value={"vendor": "Qvantum", "model": "QE-6"}
            )
            mock_api.close = AsyncMock()

            result = await validate_input(
                hass, {"username": "test@example.com", "password": "testpass"}
            )

            assert result == {"title": "Qvantum QE-6", "serial": None}

    @pytest.mark.asyncio
    async def test_validate_input_defaults_vendor_model_without_serial(self, hass):
        """Missing vendor/model/serial should fall back to a plain Qvantum title."""
        with patch(
            "custom_components.qvantum.config_flow.QvantumAPI"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api_class.return_value = mock_api
            mock_api.authenticate = AsyncMock()
            mock_api.get_primary_device = AsyncMock(
                return_value={"vendor": None, "model": None, "serial": None}
            )
            mock_api.close = AsyncMock()

            result = await validate_input(
                hass, {"username": "test@example.com", "password": "testpass"}
            )

            assert result == {"title": "Qvantum", "serial": None}

    @pytest.mark.asyncio
    async def test_validate_input_auth_error(self, hass):
        """Test validate_input with authentication error."""
        from custom_components.qvantum.api import APIAuthError

        with patch(
            "custom_components.qvantum.config_flow.QvantumAPI"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api_class.return_value = mock_api
            mock_api.authenticate = AsyncMock(
                side_effect=APIAuthError(None, "Auth failed")
            )
            mock_api.close = AsyncMock()

            with pytest.raises(InvalidAuth):
                await validate_input(
                    hass, {"username": "test@example.com", "password": "testpass"}
                )

            mock_api.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_input_connection_error(self, hass):
        """Test validate_input with connection error."""
        from custom_components.qvantum.api import APIConnectionError

        with patch(
            "custom_components.qvantum.config_flow.QvantumAPI"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api_class.return_value = mock_api
            mock_api.authenticate = AsyncMock()
            mock_api.get_primary_device = AsyncMock(
                side_effect=APIConnectionError(None, "Connection failed")
            )
            mock_api.close = AsyncMock()

            with pytest.raises(CannotConnect):
                await validate_input(
                    hass, {"username": "test@example.com", "password": "testpass"}
                )

            mock_api.close.assert_called_once()


class TestQvantumConfigFlow:
    """Test the QvantumConfigFlow class."""

    @pytest.fixture
    def config_flow(self, hass: HomeAssistant):
        """Create a config flow instance."""
        flow = QvantumConfigFlow()
        flow.hass = hass
        return flow

    def test_config_flow_version(self, config_flow):
        """Test that config flow has correct version."""
        assert config_flow.VERSION == 7

    @pytest.mark.asyncio
    async def test_user_step_shows_mode_menu(self, hass, config_flow):
        result = await config_flow.async_step_user()
        assert result["type"] == "menu"
        assert result["step_id"] == "user"
        assert "cloud" in result["menu_options"]
        assert "modbus" in result["menu_options"]

    @pytest.mark.asyncio
    async def test_cloud_step_success(self, hass, config_flow):
        hass.config_entries = MagicMock()
        hass.config_entries.flow = MagicMock()
        hass.config_entries.flow.async_progress_by_handler = AsyncMock(return_value=[])

        with (
            patch(
                "custom_components.qvantum.config_flow.validate_input"
            ) as mock_validate,
            patch.object(config_flow, "async_set_unique_id") as mock_set_unique_id,
            patch.object(config_flow, "_abort_if_unique_id_configured"),
            patch.object(config_flow, "async_create_entry") as mock_create_entry,
        ):
            mock_validate.return_value = {
                "title": "Qvantum QE-6 (12345)",
                "serial": "12345",
            }
            mock_create_entry.return_value = {"type": "create_entry"}

            result = await config_flow.async_step_cloud(
                {"username": "test@example.com", "password": "testpass"}
            )

            assert result == {"type": "create_entry"}
            mock_set_unique_id.assert_called_once_with("12345")
            mock_create_entry.assert_called_once_with(
                title="Qvantum QE-6 (12345)",
                data={
                    "username": "test@example.com",
                    "password": "testpass",
                    "modbus_tcp": False,
                    "modbus_write": False,
                },
                options={"modbus_tcp": False, "modbus_write": False},
            )

    @pytest.mark.asyncio
    async def test_cloud_step_invalid_auth(self, hass, config_flow):
        with patch(
            "custom_components.qvantum.config_flow.validate_input"
        ) as mock_validate:
            mock_validate.side_effect = InvalidAuth()
            result = await config_flow.async_step_cloud(
                {"username": "test@example.com", "password": "bad"}
            )
        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_modbus_step_success(self, hass, config_flow):
        with (
            patch(
                "custom_components.qvantum.config_flow.validate_modbus",
                AsyncMock(
                    return_value={
                        "title": "Qvantum (12003)",
                        "serial": "12003",
                        "sw_version": "1.7.22",
                    }
                ),
            ),
            patch.object(config_flow, "async_set_unique_id") as mock_set_unique_id,
            patch.object(config_flow, "_abort_if_unique_id_configured"),
            patch.object(config_flow, "async_create_entry") as mock_create_entry,
        ):
            mock_create_entry.return_value = {"type": "create_entry"}
            result = await config_flow.async_step_modbus(
                {
                    "modbus_host": "Qvantum-HP",
                    "modbus_port": 502,
                    "modbus_unit_id": 1,
                    "modbus_scan_interval": 10,
                }
            )
        assert result == {"type": "create_entry"}
        mock_set_unique_id.assert_called_once_with("12003")
        created = mock_create_entry.call_args.kwargs
        assert created["data"]["modbus_tcp"] is True
        assert "username" not in created["data"]
        assert created["options"]["modbus_scan_interval"] == 10

    @pytest.mark.asyncio
    async def test_modbus_step_cannot_connect(self, hass, config_flow):
        with patch(
            "custom_components.qvantum.config_flow.validate_modbus",
            AsyncMock(side_effect=CannotConnect()),
        ):
            result = await config_flow.async_step_modbus(
                {"modbus_host": "bad-host"}
            )
        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_reconfigure_shows_mode_menu(self, hass, config_flow):
        result = await config_flow.async_step_reconfigure()
        assert result["type"] == "menu"
        assert "reconfigure_cloud" in result["menu_options"]
        assert "reconfigure_modbus" in result["menu_options"]

    @pytest.mark.asyncio
    async def test_reconfigure_cloud_success(self, hass, config_flow):
        config_entry = MagicMock()
        config_entry.data = {"username": "old@example.com", "password": "oldpass"}
        config_entry.options = {}
        config_entry.unique_id = "test_unique_id"
        hass.config_entries = MagicMock()
        hass.config_entries.async_get_entry.return_value = config_entry
        config_flow.context = {"entry_id": "test_entry_id"}

        with (
            patch(
                "custom_components.qvantum.config_flow.validate_input",
                AsyncMock(return_value={"title": "Updated", "serial": "12345"}),
            ),
            patch.object(
                config_flow, "async_update_reload_and_abort"
            ) as mock_update,
        ):
            mock_update.return_value = {"type": "abort"}
            result = await config_flow.async_step_reconfigure_cloud(
                {"username": "new@example.com", "password": "newpass"}
            )
        assert result == {"type": "abort"}
        assert mock_update.call_args.kwargs["data"]["modbus_tcp"] is False

    @pytest.mark.asyncio
    async def test_reconfigure_modbus_success(self, hass, config_flow):
        config_entry = MagicMock()
        config_entry.data = {}
        config_entry.options = {}
        config_entry.unique_id = "test_unique_id"
        hass.config_entries = MagicMock()
        hass.config_entries.async_get_entry.return_value = config_entry
        config_flow.context = {"entry_id": "test_entry_id"}

        with (
            patch(
                "custom_components.qvantum.config_flow.validate_modbus",
                AsyncMock(return_value={"title": "Qvantum (1)", "serial": "1"}),
            ),
            patch.object(
                config_flow, "async_update_reload_and_abort"
            ) as mock_update,
        ):
            mock_update.return_value = {"type": "abort"}
            result = await config_flow.async_step_reconfigure_modbus(
                {
                    "modbus_host": "hp.local",
                    "modbus_port": 502,
                    "modbus_unit_id": 1,
                    "modbus_scan_interval": 5,
                }
            )
        assert result == {"type": "abort"}
        assert mock_update.call_args.kwargs["data"]["modbus_tcp"] is True
        assert mock_update.call_args.kwargs["options"]["modbus_host"] == "hp.local"


class TestQvantumOptionsFlow:
    """Options show only fields for the current connection mode."""

    def _entry(self, **options):
        from homeassistant.config_entries import ConfigEntry

        return ConfigEntry(
            version=1,
            minor_version=1,
            domain="qvantum",
            title="Test",
            data={},
            options=options,
            source="user",
            unique_id="test_unique_id",
            discovery_keys={},
            subentries_data={},
        )

    @pytest.mark.asyncio
    async def test_cloud_options_update_scan_interval(self, hass):
        from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler

        flow = QvantumOptionsFlowHandler(self._entry(scan_interval=120))
        with patch.object(flow, "async_create_entry") as mock_create_entry:
            mock_create_entry.return_value = {"type": "create_entry"}
            result = await flow.async_step_init({"scan_interval": 300})
        assert result == {"type": "create_entry"}
        mock_create_entry.assert_called_once_with(
            title="",
            data={
                "scan_interval": 300,
                "modbus_tcp": False,
                "modbus_write": False,
            },
        )

    @pytest.mark.asyncio
    async def test_modbus_options_update_host_and_interval(self, hass):
        from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler

        flow = QvantumOptionsFlowHandler(self._entry(modbus_tcp=True))
        with patch.object(flow, "async_create_entry") as mock_create_entry:
            mock_create_entry.return_value = {"type": "create_entry"}
            result = await flow.async_step_init(
                {
                    "modbus_host": "hp.local",
                    "modbus_port": 502,
                    "modbus_unit_id": 1,
                    "modbus_scan_interval": MIN_MODBUS_SCAN_INTERVAL,
                }
            )
        assert result == {"type": "create_entry"}
        data = mock_create_entry.call_args.kwargs["data"]
        assert data["modbus_tcp"] is True
        assert data["modbus_host"] == "hp.local"
        assert data["modbus_scan_interval"] == MIN_MODBUS_SCAN_INTERVAL
        assert "scan_interval" not in data

