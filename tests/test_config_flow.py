"""Tests for Qvantum config flow."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.qvantum.config_flow import (
    QvantumConfigFlow,
    CannotConnect,
    InvalidAuth,
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
        """Test user step with connection error."""
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