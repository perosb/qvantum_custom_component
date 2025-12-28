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

            result = await validate_input(
                hass, {"username": "test@example.com", "password": "testpass"}
            )

            assert result == {"title": "Qvantum QE-6 (12345)"}
            mock_api.authenticate.assert_called_once()
            mock_api.get_primary_device.assert_called_once()

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

            with pytest.raises(InvalidAuth):
                await validate_input(
                    hass, {"username": "test@example.com", "password": "testpass"}
                )

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

            with pytest.raises(CannotConnect):
                await validate_input(
                    hass, {"username": "test@example.com", "password": "testpass"}
                )


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
        assert config_flow.VERSION == 4  # From const.py CONFIG_VERSION

    @pytest.mark.asyncio
    async def test_user_step_cannot_connect(self, hass, config_flow):
        # Mock the hass.config_entries.flow property
        hass.config_entries = MagicMock()
        hass.config_entries.flow = MagicMock()
        hass.config_entries.flow.async_progress_by_handler = AsyncMock(return_value=[])

        with patch("custom_components.qvantum.config_flow.validate_input") as mock_validate:
            mock_validate.side_effect = CannotConnect()

            result = await config_flow.async_step_user({
                "username": "test@example.com",
                "password": "testpass"
            })

            assert result["type"] == "form"
            assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_user_step_invalid_auth(self, hass, config_flow):
        """Test user step with authentication error."""
        # Mock the hass.config_entries.flow property
        hass.config_entries = MagicMock()
        hass.config_entries.flow = MagicMock()
        hass.config_entries.flow.async_progress_by_handler = AsyncMock(return_value=[])

        with patch("custom_components.qvantum.config_flow.validate_input") as mock_validate:
            mock_validate.side_effect = InvalidAuth()

            result = await config_flow.async_step_user({
                "username": "test@example.com",
                "password": "testpass"
            })

            assert result["type"] == "form"
            assert result["errors"]["base"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_user_step_success(self, hass, config_flow):
        """Test user step with successful validation."""
        # Mock the hass.config_entries.flow property
        hass.config_entries = MagicMock()
        hass.config_entries.flow = MagicMock()
        hass.config_entries.flow.async_progress_by_handler = AsyncMock(return_value=[])

        with (
            patch(
                "custom_components.qvantum.config_flow.validate_input"
            ) as mock_validate,
            patch.object(config_flow, "async_set_unique_id") as mock_set_unique_id,
            patch.object(config_flow, "_abort_if_unique_id_configured") as mock_abort,
            patch.object(config_flow, "async_create_entry") as mock_create_entry,
        ):
            mock_validate.return_value = {"title": "Test Device"}
            mock_create_entry.return_value = {"type": "create_entry"}

            result = await config_flow.async_step_user(
                {"username": "test@example.com", "password": "testpass"}
            )

            mock_validate.assert_called_once_with(
                hass, {"username": "test@example.com", "password": "testpass"}
            )
            mock_set_unique_id.assert_called_once_with("Test Device")
            mock_abort.assert_called_once()
            mock_create_entry.assert_called_once_with(
                title="Test Device",
                data={"username": "test@example.com", "password": "testpass"},
            )

    @pytest.mark.asyncio
    async def test_user_step_unknown_error(self, hass, config_flow):
        """Test user step with unknown error."""
        # Mock the hass.config_entries.flow property
        hass.config_entries = MagicMock()
        hass.config_entries.flow = MagicMock()
        hass.config_entries.flow.async_progress_by_handler = AsyncMock(return_value=[])

        with patch(
            "custom_components.qvantum.config_flow.validate_input"
        ) as mock_validate:
            mock_validate.side_effect = Exception("Unknown error")

            result = await config_flow.async_step_user(
                {"username": "test@example.com", "password": "testpass"}
            )

            assert result["type"] == "form"
            assert result["errors"]["base"] == "unknown"

    @pytest.mark.asyncio
    async def test_user_step_initial_form(self, hass, config_flow):
        """Test user step showing initial form."""
        # Mock the hass.config_entries.flow property
        hass.config_entries = MagicMock()
        hass.config_entries.flow = MagicMock()
        hass.config_entries.flow.async_progress_by_handler = AsyncMock(return_value=[])

        result = await config_flow.async_step_user()

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert "errors" in result

    @pytest.mark.asyncio
    async def test_step_reconfigure_success(self, hass, config_flow):
        """Test reconfigure step with successful validation."""
        # Mock config entry
        config_entry = MagicMock()
        config_entry.data = {"username": "old@example.com", "password": "oldpass"}
        config_entry.unique_id = "test_unique_id"

        hass.config_entries = MagicMock()
        hass.config_entries.async_get_entry.return_value = config_entry

        config_flow.context = {"entry_id": "test_entry_id"}

        with (
            patch(
                "custom_components.qvantum.config_flow.validate_input"
            ) as mock_validate,
            patch.object(
                config_flow, "async_update_reload_and_abort"
            ) as mock_update_reload,
        ):
            mock_validate.return_value = {"title": "Updated Device"}
            mock_update_reload.return_value = {"type": "abort"}

            result = await config_flow.async_step_reconfigure(
                {"username": "new@example.com", "password": "newpass"}
            )

            mock_validate.assert_called_once_with(
                hass, {"username": "new@example.com", "password": "newpass"}
            )
            mock_update_reload.assert_called_once_with(
                config_entry,
                unique_id="test_unique_id",
                data={
                    "username": "old@example.com",
                    "password": "oldpass",
                    "username": "new@example.com",
                    "password": "newpass",
                },
                reason="reconfigure_successful",
            )

    @pytest.mark.asyncio
    async def test_step_reconfigure_cannot_connect(self, hass, config_flow):
        """Test reconfigure step with connection error."""
        # Mock config entry
        config_entry = MagicMock()
        config_entry.data = {"username": "old@example.com", "password": "oldpass"}

        hass.config_entries = MagicMock()
        hass.config_entries.async_get_entry.return_value = config_entry

        config_flow.context = {"entry_id": "test_entry_id"}

        with patch(
            "custom_components.qvantum.config_flow.validate_input"
        ) as mock_validate:
            mock_validate.side_effect = CannotConnect()

            result = await config_flow.async_step_reconfigure(
                {"username": "new@example.com", "password": "newpass"}
            )

            assert result["type"] == "form"
            assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_step_reconfigure_initial_form(self, hass, config_flow):
        """Test reconfigure step showing initial form."""
        # Mock config entry
        config_entry = MagicMock()
        config_entry.data = {"username": "old@example.com", "password": "oldpass"}

        hass.config_entries = MagicMock()
        hass.config_entries.async_get_entry.return_value = config_entry

        config_flow.context = {"entry_id": "test_entry_id"}

        result = await config_flow.async_step_reconfigure()

        assert result["type"] == "form"
        assert result["step_id"] == "reconfigure"
        assert "data_schema" in result

    @pytest.mark.asyncio
    async def test_options_flow_init_success(self, hass):
        """Test options flow init step with successful update."""
        from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler
        from homeassistant.config_entries import ConfigEntry

        config_entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="qvantum",
            title="Test",
            data={},
            options={"scan_interval": 120},
            source="user",
            unique_id="test_unique_id",
            discovery_keys={},
            subentries_data={},
        )

        flow = QvantumOptionsFlowHandler(config_entry)

        with patch.object(flow, "async_create_entry") as mock_create_entry:
            mock_create_entry.return_value = {"type": "create_entry"}

            result = await flow.async_step_init({"scan_interval": 300})

            mock_create_entry.assert_called_once_with(
                title="", data={"scan_interval": 300}
            )

    @pytest.mark.asyncio
    async def test_options_flow_init_initial_form(self, hass):
        """Test options flow init step showing initial form."""
        from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler
        from homeassistant.config_entries import ConfigEntry

        config_entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="qvantum",
            title="Test",
            data={},
            options={"scan_interval": 120},
            source="user",
            unique_id="test_unique_id",
            discovery_keys={},
            subentries_data={},
        )

        flow = QvantumOptionsFlowHandler(config_entry)

        result = await flow.async_step_init()

        assert result["type"] == "form"
        assert result["step_id"] == "init"
        assert "data_schema" in result