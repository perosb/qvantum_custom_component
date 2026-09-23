"""Tests for Qvantum config flow."""

import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.qvantum.client.modbus.device import IdentityProbeError
from custom_components.qvantum.config_flow import (
    QvantumConfigFlow,
    CannotConnect,
    InvalidAuth,
    _normalize_modbus_scan_interval,
    validate_input,
    validate_modbus,
)
from custom_components.qvantum.const import (
    DEFAULT_MODBUS_HOST,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_SCAN_INTERVAL,
    DEFAULT_MODBUS_UNIT_ID,
    MIN_MODBUS_SCAN_INTERVAL,
)


def _schema_defaults(result):
    """Return the resolved defaults of a form result's data schema."""
    defaults = {}
    for key in result["data_schema"].schema:
        name = key.schema if hasattr(key, "schema") else key
        if not hasattr(key, "default"):
            continue
        value = key.default
        defaults[name] = value() if callable(value) else value
    return defaults


class TestValidateInput:
    """Test the validate_input function."""

    @pytest.mark.asyncio
    async def test_validate_input_success(self, hass):
        """Test validate_input with successful authentication."""
        with patch(
            "custom_components.qvantum.config_flow.QvantumCloudClient"
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
            "custom_components.qvantum.config_flow.QvantumCloudClient"
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
            "custom_components.qvantum.config_flow.QvantumCloudClient"
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
        from custom_components.qvantum.client.exceptions import APIAuthError

        with patch(
            "custom_components.qvantum.config_flow.QvantumCloudClient"
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
        from custom_components.qvantum.client.exceptions import APIConnectionError

        with patch(
            "custom_components.qvantum.config_flow.QvantumCloudClient"
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

    @pytest.mark.asyncio
    async def test_validate_input_non_dict_device_falls_back(self, hass):
        """A non-dict device payload should fall back to a plain Qvantum title."""
        with patch(
            "custom_components.qvantum.config_flow.QvantumCloudClient"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api_class.return_value = mock_api
            mock_api.authenticate = AsyncMock()
            mock_api.get_primary_device = AsyncMock(return_value=None)
            mock_api.close = AsyncMock()

            result = await validate_input(
                hass, {"username": "test@example.com", "password": "testpass"}
            )

            assert result == {"title": "Qvantum", "serial": None}

    @pytest.mark.asyncio
    async def test_validate_input_close_failure_is_ignored(self, hass):
        """A failing close must not mask a successful validation."""
        with patch(
            "custom_components.qvantum.config_flow.QvantumCloudClient"
        ) as mock_api_class:
            mock_api = MagicMock()
            mock_api_class.return_value = mock_api
            mock_api.authenticate = AsyncMock()
            mock_api.get_primary_device = AsyncMock(
                return_value={"vendor": "Qvantum", "model": "QE-6", "serial": "12345"}
            )
            mock_api.close = AsyncMock(side_effect=RuntimeError("close failed"))

            result = await validate_input(
                hass, {"username": "test@example.com", "password": "testpass"}
            )

            assert result == {"title": "Qvantum QE-6 (12345)", "serial": "12345"}
            mock_api.close.assert_called_once()


class TestNormalizeModbusScanInterval:
    """Test the Modbus scan interval normalizer."""

    def test_non_numeric_falls_back_to_default(self):
        assert _normalize_modbus_scan_interval(None) == DEFAULT_MODBUS_SCAN_INTERVAL
        assert _normalize_modbus_scan_interval("nope") == DEFAULT_MODBUS_SCAN_INTERVAL

    def test_below_minimum_is_clamped(self):
        assert _normalize_modbus_scan_interval(1) == MIN_MODBUS_SCAN_INTERVAL

    def test_numeric_value_passes_through(self):
        assert _normalize_modbus_scan_interval("45") == 45


class _FakeModbusContext:
    """Async context manager standing in for HA's temporary Modbus unit."""

    def __init__(self, unit=None, error=None):
        self._unit = unit
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self._unit

    async def __aexit__(self, *exc_info):
        return False


def _patch_temporary_unit(mock_get_unit):
    """Patch HA's lazy Modbus component without importing serial deps."""
    fake_modbus = types.ModuleType("homeassistant.components.modbus")
    fake_modbus.async_get_temporary_unit = mock_get_unit
    return patch.dict(sys.modules, {"homeassistant.components.modbus": fake_modbus})


class TestValidateModbus:
    """Test the validate_modbus function."""

    @pytest.mark.asyncio
    async def test_validate_modbus_success(self, hass):
        mock_get_unit = MagicMock(return_value=_FakeModbusContext(unit=object()))
        with (
            _patch_temporary_unit(mock_get_unit),
            patch(
                "custom_components.qvantum.config_flow.async_probe_identity",
                AsyncMock(return_value=("12003", "1.7.22")),
            ),
        ):
            result = await validate_modbus(hass, "hp.local", 502, 1)

        assert result == {
            "title": "Qvantum (12003)",
            "serial": "12003",
            "sw_version": "1.7.22",
        }
        params = mock_get_unit.call_args.args[1]
        assert params.host == "hp.local"
        assert params.port == 502
        assert mock_get_unit.call_args.args[2] == 1

    @pytest.mark.asyncio
    async def test_validate_modbus_identity_probe_error(self, hass):
        with (
            _patch_temporary_unit(
                MagicMock(return_value=_FakeModbusContext(unit=object()))
            ),
            patch(
                "custom_components.qvantum.config_flow.async_probe_identity",
                AsyncMock(side_effect=IdentityProbeError("no serial")),
            ),
        ):
            with pytest.raises(CannotConnect):
                await validate_modbus(hass, "hp.local", 502, 1)

    @pytest.mark.asyncio
    async def test_validate_modbus_home_assistant_error(self, hass):
        with _patch_temporary_unit(
            MagicMock(side_effect=HomeAssistantError("modbus unavailable"))
        ):
            with pytest.raises(CannotConnect):
                await validate_modbus(hass, "hp.local", 502, 1)

    @pytest.mark.asyncio
    async def test_validate_modbus_unexpected_error(self, hass):
        with _patch_temporary_unit(MagicMock(side_effect=RuntimeError("boom"))):
            with pytest.raises(CannotConnect):
                await validate_modbus(hass, "hp.local", 502, 1)


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

    def test_async_get_options_flow_returns_handler(self):
        """The options flow entry point should build the handler."""
        from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler

        entry = MagicMock()
        entry.options = {}
        flow = QvantumConfigFlow.async_get_options_flow(entry)
        assert isinstance(flow, QvantumOptionsFlowHandler)

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
    async def test_cloud_step_cannot_connect(self, hass, config_flow):
        with patch(
            "custom_components.qvantum.config_flow.validate_input",
            AsyncMock(side_effect=CannotConnect()),
        ):
            result = await config_flow.async_step_cloud(
                {"username": "test@example.com", "password": "testpass"}
            )
        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_cloud_step_unexpected_error(self, hass, config_flow):
        with patch(
            "custom_components.qvantum.config_flow.validate_input",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await config_flow.async_step_cloud(
                {"username": "test@example.com", "password": "testpass"}
            )
        assert result["type"] == "form"
        assert result["errors"]["base"] == "unknown"

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
                    "modbus_write": True,
                }
            )
        assert result == {"type": "create_entry"}
        mock_set_unique_id.assert_called_once_with("12003")
        created = mock_create_entry.call_args.kwargs
        assert created["data"]["modbus_tcp"] is True
        assert created["data"]["modbus_write"] is True
        assert "username" not in created["data"]
        assert created["options"]["modbus_scan_interval"] == 10
        assert created["options"]["modbus_write"] is True

    @pytest.mark.asyncio
    async def test_modbus_step_strips_host_whitespace(self, hass, config_flow):
        """Padded or empty hosts should not be persisted as-is."""
        from custom_components.qvantum.const import DEFAULT_MODBUS_HOST

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
            ) as mock_validate,
            patch.object(config_flow, "async_set_unique_id"),
            patch.object(config_flow, "_abort_if_unique_id_configured"),
            patch.object(config_flow, "async_create_entry") as mock_create_entry,
        ):
            mock_create_entry.return_value = {"type": "create_entry"}
            result = await config_flow.async_step_modbus(
                {
                    "modbus_host": "  hp.local  ",
                    "modbus_port": 502,
                    "modbus_unit_id": 1,
                    "modbus_scan_interval": 10,
                }
            )
            assert result == {"type": "create_entry"}
            assert mock_validate.await_args.args[1] == "hp.local"
            created = mock_create_entry.call_args.kwargs
            assert created["data"]["modbus_host"] == "hp.local"
            assert created["options"]["modbus_host"] == "hp.local"

            mock_create_entry.return_value = {"type": "create_entry"}
            await config_flow.async_step_modbus(
                {
                    "modbus_host": "   ",
                    "modbus_port": 502,
                    "modbus_unit_id": 1,
                    "modbus_scan_interval": 10,
                }
            )
            assert mock_validate.await_args.args[1] == DEFAULT_MODBUS_HOST
            created = mock_create_entry.call_args.kwargs
            assert created["data"]["modbus_host"] == DEFAULT_MODBUS_HOST

    @pytest.mark.asyncio
    async def test_modbus_step_cannot_connect(self, hass, config_flow):
        with patch(
            "custom_components.qvantum.config_flow.validate_modbus",
            AsyncMock(side_effect=CannotConnect()),
        ):
            result = await config_flow.async_step_modbus(
                {
                    "modbus_host": "bad-host",
                    "modbus_port": 1502,
                    "modbus_unit_id": 7,
                    "modbus_scan_interval": 20,
                }
            )
        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"
        defaults = {
            (key.schema if hasattr(key, "schema") else key): key.default
            for key in result["data_schema"].schema
            if hasattr(key, "default")
        }

        def _default(name):
            value = defaults[name]
            return value() if callable(value) else value

        assert _default("modbus_host") == "bad-host"
        assert _default("modbus_port") == 1502
        assert _default("modbus_unit_id") == 7
        assert _default("modbus_scan_interval") == 20

    @pytest.mark.asyncio
    async def test_modbus_step_unexpected_error(self, hass, config_flow):
        with patch(
            "custom_components.qvantum.config_flow.validate_modbus",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await config_flow.async_step_modbus(
                {
                    "modbus_host": "hp.local",
                    "modbus_port": 502,
                    "modbus_unit_id": 1,
                    "modbus_scan_interval": 10,
                }
            )
        assert result["type"] == "form"
        assert result["errors"]["base"] == "unknown"

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
        assert mock_update.call_args.kwargs["unique_id"] == "12345"
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
        assert mock_update.call_args.kwargs["unique_id"] == "1"
        assert mock_update.call_args.kwargs["data"]["modbus_tcp"] is True
        assert mock_update.call_args.kwargs["options"]["modbus_host"] == "hp.local"

    def _prepare_reconfigure(self, hass, config_flow, data=None, options=None):
        config_entry = MagicMock()
        config_entry.data = data or {}
        config_entry.options = options or {}
        hass.config_entries = MagicMock()
        hass.config_entries.async_get_entry.return_value = config_entry
        config_flow.context = {"entry_id": "test_entry_id"}
        return config_entry

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (CannotConnect(), "cannot_connect"),
            (InvalidAuth(), "invalid_auth"),
            (RuntimeError("boom"), "unknown"),
        ],
    )
    async def test_reconfigure_cloud_error_branches(
        self, hass, config_flow, error, expected
    ):
        self._prepare_reconfigure(
            hass,
            config_flow,
            data={"username": "old@example.com", "password": "oldpass"},
        )
        with patch(
            "custom_components.qvantum.config_flow.validate_input",
            AsyncMock(side_effect=error),
        ):
            result = await config_flow.async_step_reconfigure_cloud(
                {"username": "new@example.com", "password": "newpass"}
            )
        assert result["type"] == "form"
        assert result["errors"]["base"] == expected

    @pytest.mark.asyncio
    async def test_reconfigure_cloud_form_defaults_username(self, hass, config_flow):
        self._prepare_reconfigure(
            hass,
            config_flow,
            data={"username": "old@example.com", "password": "oldpass"},
        )
        result = await config_flow.async_step_reconfigure_cloud()
        assert result["type"] == "form"
        assert _schema_defaults(result)["username"] == "old@example.com"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (CannotConnect(), "cannot_connect"),
            (RuntimeError("boom"), "unknown"),
        ],
    )
    async def test_reconfigure_modbus_error_branches(
        self, hass, config_flow, error, expected
    ):
        self._prepare_reconfigure(hass, config_flow)
        with patch(
            "custom_components.qvantum.config_flow.validate_modbus",
            AsyncMock(side_effect=error),
        ):
            result = await config_flow.async_step_reconfigure_modbus(
                {
                    "modbus_host": "hp.local",
                    "modbus_port": 502,
                    "modbus_unit_id": 1,
                    "modbus_scan_interval": 5,
                }
            )
        assert result["type"] == "form"
        assert result["errors"]["base"] == expected

    @pytest.mark.asyncio
    async def test_reconfigure_modbus_form_defaults_from_entry(
        self, hass, config_flow
    ):
        self._prepare_reconfigure(
            hass,
            config_flow,
            data={
                "modbus_host": "old.local",
                "modbus_port": 1502,
                "modbus_unit_id": 9,
                "modbus_write": True,
            },
        )
        result = await config_flow.async_step_reconfigure_modbus()
        assert result["type"] == "form"
        defaults = _schema_defaults(result)
        assert defaults["modbus_host"] == "old.local"
        assert defaults["modbus_port"] == 1502
        assert defaults["modbus_unit_id"] == 9
        assert defaults["modbus_write"] is True

    def _prepare_reauth(self, hass, config_flow):
        from homeassistant.config_entries import SOURCE_REAUTH

        config_entry = MagicMock()
        config_entry.entry_id = "test_entry_id"
        config_entry.data = {
            "username": "old@example.com",
            "password": "oldpass",
            "modbus_tcp": False,
            "modbus_write": False,
        }
        config_entry.update_listeners = []
        hass.config_entries = MagicMock()
        hass.config_entries.async_get_known_entry.return_value = config_entry
        config_flow.context = {
            "source": SOURCE_REAUTH,
            "entry_id": config_entry.entry_id,
        }
        return config_entry

    @pytest.mark.asyncio
    async def test_reauth_step_shows_confirm_form(self, hass, config_flow):
        self._prepare_reauth(hass, config_flow)

        result = await config_flow.async_step_reauth(
            {"username": "old@example.com", "password": "oldpass"}
        )

        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        assert _schema_defaults(result)["username"] == "old@example.com"

    @pytest.mark.asyncio
    async def test_reauth_confirm_updates_credentials(self, hass, config_flow):
        entry = self._prepare_reauth(hass, config_flow)

        with patch(
            "custom_components.qvantum.config_flow.validate_input",
            AsyncMock(return_value={"title": "Qvantum", "serial": "12345"}),
        ):
            result = await config_flow.async_step_reauth_confirm(
                {"username": "new@example.com", "password": "newpass"}
            )

        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        updated = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert updated["username"] == "new@example.com"
        assert updated["password"] == "newpass"
        assert updated["modbus_tcp"] is False
        hass.config_entries.async_schedule_reload.assert_called_once_with(
            entry.entry_id
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (CannotConnect(), "cannot_connect"),
            (InvalidAuth(), "invalid_auth"),
            (RuntimeError("boom"), "unknown"),
        ],
    )
    async def test_reauth_confirm_error_branches(
        self, hass, config_flow, error, expected
    ):
        self._prepare_reauth(hass, config_flow)

        with patch(
            "custom_components.qvantum.config_flow.validate_input",
            AsyncMock(side_effect=error),
        ):
            result = await config_flow.async_step_reauth_confirm(
                {"username": "new@example.com", "password": "newpass"}
            )

        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"]["base"] == expected


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
                    "modbus_host": "  hp.local  ",
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

    @pytest.mark.asyncio
    async def test_modbus_options_form_lists_modbus_fields(self, hass):
        from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler

        flow = QvantumOptionsFlowHandler(self._entry(modbus_tcp=True))
        result = await flow.async_step_init()
        assert result["type"] == "form"
        assert result["step_id"] == "init"
        defaults = _schema_defaults(result)
        assert defaults["modbus_host"] == DEFAULT_MODBUS_HOST
        assert defaults["modbus_port"] == DEFAULT_MODBUS_PORT
        assert defaults["modbus_unit_id"] == DEFAULT_MODBUS_UNIT_ID
        assert defaults["modbus_scan_interval"] == DEFAULT_MODBUS_SCAN_INTERVAL
        assert defaults["modbus_write"] is False

    @pytest.mark.asyncio
    async def test_cloud_options_form_lists_scan_interval(self, hass):
        from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler

        flow = QvantumOptionsFlowHandler(self._entry(scan_interval=60))
        result = await flow.async_step_init()
        assert result["type"] == "form"
        assert result["step_id"] == "init"
        assert _schema_defaults(result)["scan_interval"] == 60

