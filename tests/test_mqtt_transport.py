"""Tests for the MQTT transport wiring (POC): transport selection and flow."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.qvantum import _async_mqtt_client
from custom_components.qvantum.client.mqtt import QvantumMqttClient
from custom_components.qvantum.config_flow import QvantumConfigFlow
from custom_components.qvantum.coordinator import resolve_transport
from custom_components.qvantum.const import (
    CONF_MODBUS_TCP,
    CONF_TRANSPORT,
    CONF_MQTT_SERIAL,
    CONF_MQTT_TOPIC_PREFIX,
    CONF_MQTT_WRITE,
    CONF_TRANSPORT,
    TRANSPORT_CLOUD,
    TRANSPORT_MODBUS,
    TRANSPORT_MQTT,
)


def _entry(data=None, options=None):
    entry = MagicMock()
    entry.data = data or {}
    entry.options = options or {}
    return entry


class TestTransportSelection:
    def test_explicit_transport_wins(self):
        entry = _entry({CONF_TRANSPORT: TRANSPORT_MQTT, CONF_MODBUS_TCP: True})
        assert resolve_transport(entry) == TRANSPORT_MQTT

    def test_options_beat_data(self):
        entry = _entry(
            {CONF_TRANSPORT: TRANSPORT_CLOUD}, {CONF_TRANSPORT: TRANSPORT_MODBUS}
        )
        assert resolve_transport(entry) == TRANSPORT_MODBUS

    def test_legacy_entry_without_transport_falls_back_to_modbus(self):
        entry = _entry({CONF_MODBUS_TCP: True})
        assert resolve_transport(entry) == TRANSPORT_MODBUS

    def test_legacy_entry_without_transport_falls_back_to_cloud(self):
        entry = _entry({CONF_MODBUS_TCP: False})
        assert resolve_transport(entry) == TRANSPORT_CLOUD

    def test_unknown_transport_value_is_ignored(self):
        entry = _entry({CONF_TRANSPORT: "carrier-pigeon", CONF_MODBUS_TCP: True})
        assert resolve_transport(entry) == TRANSPORT_MODBUS


class TestMqttClientWiring:
    async def test_builds_a_client_on_top_of_the_ha_mqtt_integration(self, hass):
        # The harness gives ``hass`` a loop that is not the running one; the
        # transport marshals broker callbacks onto ``hass.loop``.
        hass.loop = asyncio.get_running_loop()
        entry = _entry(
            {
                CONF_TRANSPORT: TRANSPORT_MQTT,
                CONF_MQTT_TOPIC_PREFIX: "qvantum",
                CONF_MQTT_SERIAL: "3010100244154033",
                CONF_MQTT_WRITE: True,
            }
        )
        unsubscribe = MagicMock()
        seen: list[str] = []
        callbacks: dict[str, object] = {}

        async def _subscribe(hass, topic, callback, qos=0):
            seen.append(topic)
            callbacks[topic] = callback
            return unsubscribe

        with (
            patch(
                "homeassistant.components.mqtt.async_wait_for_mqtt_client",
                AsyncMock(return_value=True),
            ),
            patch("homeassistant.components.mqtt.async_subscribe", _subscribe),
            patch(
                "homeassistant.components.mqtt.async_publish", AsyncMock()
            ) as publish,
        ):
            task = asyncio.create_task(_async_mqtt_client(hass, entry))
            # Let the client subscribe, then deliver the retained snapshot the
            # way the broker would.
            for _ in range(20):
                await asyncio.sleep(0)
                if "qvantum/3010100244154033/state" in callbacks:
                    break
            callbacks["qvantum/3010100244154033/state"](
                SimpleNamespace(
                    payload=json.dumps(
                        {
                            "hpid": "3010100244154033",
                            "p": {"metrics": {"bt1": 1.0}, "settings": {}},
                        }
                    )
                )
            )
            client = await asyncio.wait_for(task, 5)

        assert isinstance(client, QvantumMqttClient)
        assert client.writable is True
        # State, the write acknowledgement, and the pump's last will.
        assert len(seen) == 3
        assert set(seen) == {
            "qvantum/3010100244154033/state",
            "qvantum/3010100244154033/cmd-res",
            "qvantum/3010100244154033/availability",
        }
        # Availability is announced retained.
        publish.assert_awaited_once()
        assert publish.await_args.args[1] == "qvantum/3010100244154033/availability"
        assert publish.await_args.kwargs["retain"] is True
        assert (await client.get_metrics("x"))["metrics"] == {"bt1": 1.0}

    async def test_missing_mqtt_integration_raises_not_ready(self, hass):
        from homeassistant.exceptions import ConfigEntryNotReady

        entry = _entry(
            {CONF_TRANSPORT: TRANSPORT_MQTT, CONF_MQTT_SERIAL: "123"}
        )
        with patch(
            "homeassistant.components.mqtt.async_wait_for_mqtt_client",
            AsyncMock(return_value=False),
        ):
            with pytest.raises(ConfigEntryNotReady, match="MQTT integration"):
                await _async_mqtt_client(hass, entry)


class TestMqttConfigFlow:
    @pytest.fixture
    def flow(self, hass):
        flow = QvantumConfigFlow()
        flow.hass = hass
        return flow

    async def test_menu_offers_mqtt(self, flow):
        result = await flow.async_step_user()
        assert result["type"] == "menu"
        assert "mqtt" in result["menu_options"]

    async def test_form_is_shown_first(self, flow):
        result = await flow.async_step_mqtt()
        assert result["type"] == "form"
        assert result["step_id"] == "mqtt"
        assert CONF_MQTT_SERIAL in result["data_schema"].schema

    async def test_entry_is_created_with_the_transport_marker(self, flow):
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        result = await flow.async_step_mqtt(
            {
                CONF_MQTT_TOPIC_PREFIX: "qvantum",
                CONF_MQTT_SERIAL: "3010100244154033",
                CONF_MQTT_WRITE: True,
            }
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_TRANSPORT] == TRANSPORT_MQTT
        assert result["data"][CONF_MQTT_SERIAL] == "3010100244154033"
        assert result["data"][CONF_MQTT_WRITE] is True
        assert result["options"][CONF_TRANSPORT] == TRANSPORT_MQTT
        flow.async_set_unique_id.assert_awaited_once_with("3010100244154033")

    async def test_blank_serial_keeps_the_form(self, flow):
        result = await flow.async_step_mqtt(
            {CONF_MQTT_TOPIC_PREFIX: "qvantum", CONF_MQTT_SERIAL: "  "}
        )
        assert result["type"] == "form"


class TestMqttWriteAccess:
    """The write-access mixin assumed cloud-or-Modbus; MQTT is a third case."""

    def _entity(self, *, transport, writable):
        from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator
        from custom_components.qvantum.entity import QvantumAccessMixin

        # spec makes isinstance() pass, which the mixin checks first.
        coordinator = MagicMock(spec=QvantumDataUpdateCoordinator)
        coordinator.transport = transport
        coordinator.modbus_enabled = transport == TRANSPORT_MODBUS
        coordinator.client = SimpleNamespace(writable=writable)

        class _Entity(QvantumAccessMixin):
            pass

        entity = _Entity()
        entity.coordinator = coordinator
        return entity

    def test_mqtt_write_access_follows_the_link_flag(self):
        assert self._entity(transport=TRANSPORT_MQTT, writable=True)._has_write_access
        assert not self._entity(
            transport=TRANSPORT_MQTT, writable=False
        )._has_write_access


class TestMqttReconfigure:
    """An existing entry must be able to move onto the broker."""

    @pytest.fixture
    def flow(self, hass):
        flow = QvantumConfigFlow()
        flow.hass = hass
        return flow

    async def test_reconfigure_menu_offers_mqtt(self, flow):
        result = await flow.async_step_reconfigure()
        assert result["type"] == "menu"
        assert "reconfigure_mqtt" in result["menu_options"]

    async def test_reconfigure_mqtt_shows_the_form_prefilled(self, flow):
        entry = MagicMock()
        entry.data = {
            CONF_TRANSPORT: TRANSPORT_MODBUS,
            CONF_MQTT_TOPIC_PREFIX: "hus",
            CONF_MQTT_SERIAL: "POC0042",
            CONF_MQTT_WRITE: True,
        }
        entry.options = {}
        flow._reconfigure_entry = MagicMock(return_value=entry)

        result = await flow.async_step_reconfigure_mqtt()

        assert result["type"] == "form"
        assert result["step_id"] == "reconfigure_mqtt"

    async def test_reconfigure_mqtt_switches_the_transport(self, flow):
        entry = MagicMock()
        entry.data = {CONF_TRANSPORT: TRANSPORT_MODBUS}
        entry.options = {}
        flow._reconfigure_entry = MagicMock(return_value=entry)
        flow.async_update_reload_and_abort = MagicMock(
            return_value={"type": "abort", "reason": "reconfigure_successful"}
        )

        await flow.async_step_reconfigure_mqtt(
            {
                CONF_MQTT_TOPIC_PREFIX: "qvantum",
                CONF_MQTT_SERIAL: "POC0042",
                CONF_MQTT_WRITE: False,
            }
        )

        kwargs = flow.async_update_reload_and_abort.call_args.kwargs
        assert kwargs["data"][CONF_TRANSPORT] == TRANSPORT_MQTT
        assert kwargs["options"][CONF_TRANSPORT] == TRANSPORT_MQTT
        assert kwargs["unique_id"] == "POC0042"
        # Modbus flags must not linger on an MQTT entry.
        assert kwargs["data"][CONF_MODBUS_TCP] is False

    async def test_reconfigure_mqtt_without_a_serial_keeps_the_form(self, flow):
        entry = MagicMock()
        entry.data = {}
        entry.options = {}
        flow._reconfigure_entry = MagicMock(return_value=entry)

        result = await flow.async_step_reconfigure_mqtt(
            {CONF_MQTT_TOPIC_PREFIX: "qvantum", CONF_MQTT_SERIAL: "   "}
        )

        assert result["type"] == "form"


async def test_reconfigure_to_mqtt_hands_the_curve_back(hass):
    """Leaving Modbus for MQTT must not leave a frozen table on the pump."""
    from custom_components.qvantum import _async_hand_back_curve

    flow = QvantumConfigFlow()
    flow.hass = hass
    entry = MagicMock()
    entry.data = {CONF_TRANSPORT: TRANSPORT_MODBUS}
    entry.options = {}
    flow._reconfigure_entry = MagicMock(return_value=entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})

    with patch(
        "custom_components.qvantum._async_hand_back_curve", AsyncMock(return_value=True)
    ) as hand_back:
        await flow.async_step_reconfigure_mqtt(
            {
                CONF_MQTT_TOPIC_PREFIX: "qvantum",
                CONF_MQTT_SERIAL: "POC0042",
            }
        )

    hand_back.assert_awaited_once()
    assert hand_back.await_args.kwargs["reason"] == "reconfiguring"
    assert _async_hand_back_curve is not None


class TestMqttOptions:
    """The options dialog must speak the transport it is configuring."""

    @staticmethod
    def _flow(hass, entry):
        from custom_components.qvantum.config_flow import QvantumOptionsFlowHandler

        flow = QvantumOptionsFlowHandler(entry)
        flow.hass = hass
        return flow

    async def test_mqtt_offers_the_curve_terms_and_no_interval(self, hass):
        entry = _entry(
            {
                CONF_TRANSPORT: TRANSPORT_MQTT,
                CONF_MQTT_TOPIC_PREFIX: "qvantum",
                CONF_MQTT_SERIAL: "POC0001",
            }
        )
        entry.entry_id = "entry-1"
        # The curve coordinator exists on MQTT too, so its terms have an owner.
        entry.runtime_data = MagicMock()
        entry.runtime_data.curve_coordinator = SimpleNamespace(
            terms={"solar": True, "cop_feedback": False}
        )
        flow = self._flow(hass, entry)

        with patch(
            "custom_components.qvantum.config_flow.async_load_state",
            AsyncMock(return_value={}),
        ):
            result = await flow.async_step_init()

        schema = result["data_schema"].schema
        keys = {str(key) for key in schema}
        # No poll interval: the broker pushes.
        assert not any("scan_interval" in key for key in keys)
        # The curve's optional terms apply, because the curve writes settings.
        assert any("curve_solar_term" in key for key in keys)
        assert any("telemetry" in key for key in keys)

