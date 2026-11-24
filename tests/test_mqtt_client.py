"""Tests for the MQTT transport POC (client side, no broker)."""

from __future__ import annotations

import asyncio
import json

import pytest

from custom_components.qvantum.client.exceptions import TransportError
from custom_components.qvantum.client.mqtt import (
    QvantumMqttClient,
    availability_topic,
    command_topic,
    decode_envelope,
    encode_envelope,
    response_topic,
    state_topic,
)

PREFIX = "qvantum"
SERIAL = "3010100244154033"


class FakeBroker:
    """Minimal broker: records publishes and lets a test push messages."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []
        self.subscriptions: dict[str, object] = {}
        self.unsubscribed: list[str] = []

    async def subscribe(self, topic, callback):
        self.subscriptions[topic] = callback

        def _unsubscribe() -> None:
            self.subscriptions.pop(topic, None)
            self.unsubscribed.append(topic)

        return _unsubscribe

    async def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))

    def deliver(self, topic, payload) -> None:
        callback = self.subscriptions.get(topic)
        assert callback is not None, f"nothing subscribed to {topic}"
        callback(payload)


def _client(broker: FakeBroker, *, writable: bool = True, timeout: float = 0.2):
    return QvantumMqttClient(
        topic_prefix=PREFIX,
        serial=SERIAL,
        device_id="dev-1",
        subscribe=broker.subscribe,
        publish=broker.publish,
        writable=writable,
        response_timeout=timeout,
    )


def _without_age(payload: dict) -> dict:
    """The metrics without the injected ``latency`` (the snapshot's age).

    A push transport has no round-trip to time, so ``get_metrics`` reports how
    old the snapshot is instead. The age is asserted on its own below.
    """
    metrics = dict(payload["metrics"])
    metrics.pop("latency", None)
    return metrics


def _state(metrics: dict, settings: dict) -> str:
    return encode_envelope(SERIAL, {"metrics": metrics, "settings": settings})


def test_topics_are_built_under_prefix_and_serial():
    assert state_topic(PREFIX, SERIAL) == f"qvantum/{SERIAL}/state"
    assert command_topic(PREFIX, SERIAL) == f"qvantum/{SERIAL}/cmd"
    assert response_topic(PREFIX, SERIAL) == f"qvantum/{SERIAL}/cmd-res"
    assert availability_topic(PREFIX, SERIAL) == f"qvantum/{SERIAL}/availability"
    # A trailing slash on the prefix must not double up.
    assert state_topic("qvantum/", SERIAL) == f"qvantum/{SERIAL}/state"


def test_envelope_round_trip():
    raw = encode_envelope(SERIAL, {"set": {"curve_10": 30}})
    assert json.loads(raw) == {"hpid": SERIAL, "p": {"set": {"curve_10": 30}}}
    assert decode_envelope(raw) == {"set": {"curve_10": 30}}


@pytest.mark.parametrize(
    "raw",
    [None, "", "not json", "[]", '{"hpid":"x"}', '{"hpid":"x","p":[]}'],
)
def test_decode_envelope_rejects_malformed(raw):
    assert decode_envelope(raw) is None


async def test_start_subscribes_and_announces_availability():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    assert set(broker.subscriptions) == {
        state_topic(PREFIX, SERIAL),
        response_topic(PREFIX, SERIAL),
        # The last will is what separates "nothing changed" from "nothing is
        # there"; without it a dead pump keeps serving its last snapshot.
        availability_topic(PREFIX, SERIAL),
    }
    assert broker.published == [
        (availability_topic(PREFIX, SERIAL), "online", True)
    ]


async def test_latency_is_the_age_of_the_snapshot(monkeypatch):
    """A push transport has no round-trip, so `latency` is how old the data is.

    The snapshot's own `latency` belongs to whoever produced it — on a followed
    installation that is another instance's Modbus poll, which says nothing
    about this link.
    """
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    now = 1000.0
    monkeypatch.setattr("custom_components.qvantum.client.mqtt.client.time.monotonic", lambda: now)
    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 1.0, "latency": 42}, {}))
    first = (await client.get_metrics("dev-1"))["metrics"]["latency"]
    assert first == 0

    now += 12.5
    later = (await client.get_metrics("dev-1"))["metrics"]["latency"]
    assert later == 12_500


async def test_reads_are_served_from_the_pushed_snapshot():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    broker.deliver(
        state_topic(PREFIX, SERIAL),
        _state({"bt1": 3.2, "bt2": 20.4}, {"curve_10": 29, "curve_0": 20}),
    )

    metrics = await client.get_metrics("dev-1")
    assert _without_age(metrics) == {"bt1": 3.2, "bt2": 20.4}
    # The age of the snapshot, not a round-trip — and it travels even through
    # the filter, because freshness is a property of the link.
    assert isinstance(metrics["metrics"]["latency"], int)

    filtered = await client.get_metrics("dev-1", enabled_metrics=["bt1"])
    assert _without_age(filtered) == {"bt1": 3.2}
    assert "latency" in filtered["metrics"]

    settings = await client.get_settings("dev-1")
    assert settings == {
        "settings": [
            {"name": "curve_10", "value": 29},
            {"name": "curve_0", "value": 20},
        ]
    }


async def test_reads_fail_before_the_first_snapshot():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    with pytest.raises(TransportError, match="No MQTT state"):
        await client.get_metrics("dev-1")
    with pytest.raises(TransportError, match="No MQTT state"):
        await client.get_settings("dev-1")


async def test_listener_fires_on_every_snapshot():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    calls: list[int] = []
    remove = client.add_listener(lambda: calls.append(1))

    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 1.0}, {}))
    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 2.0}, {}))
    assert len(calls) == 2

    remove()
    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 3.0}, {}))
    assert len(calls) == 2


async def test_malformed_state_is_ignored():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    broker.deliver(state_topic(PREFIX, SERIAL), "garbage")
    with pytest.raises(TransportError):
        await client.get_metrics("dev-1")


async def test_write_publishes_a_command_and_waits_for_the_response():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    async def _answer() -> None:
        await asyncio.sleep(0)
        broker.deliver(
            response_topic(PREFIX, SERIAL),
            encode_envelope(SERIAL, {"ok": True, "applied": {"curve_10": 30}}),
        )

    task = asyncio.create_task(_answer())
    result = await client.update_setting("dev-1", "curve_10", 30)
    await task

    assert result == {"status": "APPLIED"}
    topic, payload, retain = broker.published[-1]
    assert topic == command_topic(PREFIX, SERIAL)
    assert retain is False
    assert json.loads(payload) == {
        "hpid": SERIAL,
        "p": {"set": {"curve_10": 30}},
    }


async def test_write_times_out_without_a_response():
    broker = FakeBroker()
    client = _client(broker, timeout=0.05)
    await client.async_start(wait_for_state=False)

    with pytest.raises(TransportError, match="No MQTT response"):
        await client.update_setting("dev-1", "curve_10", 30)


async def test_write_rejected_by_the_pump():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    async def _reject() -> None:
        await asyncio.sleep(0)
        broker.deliver(
            response_topic(PREFIX, SERIAL),
            encode_envelope(SERIAL, {"ok": False, "error": "out of range"}),
        )

    task = asyncio.create_task(_reject())
    with pytest.raises(TransportError, match="out of range"):
        await client.update_setting("dev-1", "curve_10", 99)
    await task


async def test_writes_are_refused_when_disabled():
    broker = FakeBroker()
    client = _client(broker, writable=False)
    await client.async_start(wait_for_state=False)

    assert client.writable is False
    with pytest.raises(TransportError, match="writes are disabled"):
        await client.update_setting("dev-1", "curve_10", 30)


async def test_empty_write_is_a_no_op():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    assert await client.update_settings("dev-1", {}) == {"status": "APPLIED"}
    assert broker.published[-1][0] == availability_topic(PREFIX, SERIAL)


async def test_tap_water_capacity_maps_to_the_start_stop_pair():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    async def _answer() -> None:
        await asyncio.sleep(0)
        broker.deliver(
            response_topic(PREFIX, SERIAL),
            encode_envelope(SERIAL, {"ok": True}),
        )

    task = asyncio.create_task(_answer())
    await client.set_tap_water_capacity_target("dev-1", 3)
    await task

    sent = json.loads(broker.published[-1][1])["p"]["set"]
    assert set(sent) == {"tap_water_start", "tap_water_stop"}


async def test_extra_tap_water_writes_the_mode_only():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    async def _answer() -> None:
        await asyncio.sleep(0)
        broker.deliver(
            response_topic(PREFIX, SERIAL),
            encode_envelope(SERIAL, {"ok": True}),
        )

    task = asyncio.create_task(_answer())
    await client.set_extra_tap_water("dev-1", 120)
    await task

    sent = json.loads(broker.published[-1][1])["p"]["set"]
    assert sent == {"extra_tap_water": 2}


async def test_close_unsubscribes_and_marks_offline():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)
    await client.close()

    assert broker.subscriptions == {}
    assert broker.published[-1] == (
        availability_topic(PREFIX, SERIAL),
        "offline",
        True,
    )


def test_client_satisfies_the_shared_protocol():
    """The coordinator types its client as ``QvantumClient``; MQTT must fit."""
    from custom_components.qvantum.client.protocol import QvantumClient

    broker = FakeBroker()
    client = _client(broker)
    assert isinstance(client, QvantumClient)


async def test_start_waits_for_the_first_snapshot():
    """A push transport has nothing to read until the retained state lands."""
    broker = FakeBroker()
    client = _client(broker)

    async def _publish_soon() -> None:
        await asyncio.sleep(0.01)
        broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 1.0}, {}))

    task = asyncio.create_task(_publish_soon())
    await client.async_start(timeout=1.0)
    await task

    assert _without_age(await client.get_metrics("dev-1")) == {"bt1": 1.0}


async def test_start_times_out_when_the_pump_never_answers():
    broker = FakeBroker()
    client = _client(broker)

    with pytest.raises(TransportError, match="No MQTT state"):
        await client.async_start(timeout=0.05)


async def test_command_publishes_and_returns_the_result():
    """The third verb: an operation that is not a setting."""
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    async def _answer() -> None:
        await asyncio.sleep(0)
        broker.deliver(
            response_topic(PREFIX, SERIAL),
            encode_envelope(
                SERIAL,
                {"ok": True, "result": {"points": [{"id": 721, "value": 3600}]}},
            ),
        )

    task = asyncio.create_task(_answer())
    result = await client.send_command("dev-1", "read_all_setpoints")
    await task

    assert result == {"points": [{"id": 721, "value": 3600}]}
    assert json.loads(broker.published[-1][1]) == {
        "hpid": SERIAL,
        "p": {"cmd": {"name": "read_all_setpoints", "args": {}}},
    }


async def test_read_only_command_works_on_a_read_only_link():
    """Reading the setpoint list must not need a write-enabled entry."""
    broker = FakeBroker()
    client = _client(broker, writable=False)
    await client.async_start(wait_for_state=False)

    async def _answer() -> None:
        await asyncio.sleep(0)
        broker.deliver(
            response_topic(PREFIX, SERIAL),
            encode_envelope(SERIAL, {"ok": True, "result": {"points": []}}),
        )

    task = asyncio.create_task(_answer())
    result = await client.send_command(
        "dev-1", "read_all_setpoints", writable_command=False
    )
    await task
    assert result == {"points": []}


async def test_writing_command_is_refused_when_disabled():
    broker = FakeBroker()
    client = _client(broker, writable=False)
    await client.async_start(wait_for_state=False)

    with pytest.raises(TransportError, match="writes are disabled"):
        await client.send_command("dev-1", "limit_max_compressor_speed")


async def test_command_rejection_surfaces_the_pump_error():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    async def _reject() -> None:
        await asyncio.sleep(0)
        broker.deliver(
            response_topic(PREFIX, SERIAL),
            encode_envelope(
                SERIAL,
                {"ok": False, "error": "'limitStep' has invalid value, expecting 0 - 5"},
            ),
        )

    task = asyncio.create_task(_reject())
    with pytest.raises(TransportError, match="expecting 0 - 5"):
        await client.send_command(
            "dev-1", "limit_max_compressor_speed", {"limitStep": 9}
        )
    await task


async def test_offline_pump_makes_reads_fail():
    """A dead pump must not keep serving frozen values."""
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)
    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 1.0}, {"curve_0": 20}))

    assert client.online is True
    assert _without_age(await client.get_metrics("dev-1")) == {"bt1": 1.0}

    broker.deliver(availability_topic(PREFIX, SERIAL), "offline")

    assert client.online is False
    with pytest.raises(TransportError, match="offline"):
        await client.get_metrics("dev-1")
    with pytest.raises(TransportError, match="offline"):
        await client.get_settings("dev-1")


async def test_the_pump_comes_back_online():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)
    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 1.0}, {}))

    broker.deliver(availability_topic(PREFIX, SERIAL), "offline")
    broker.deliver(availability_topic(PREFIX, SERIAL), "online")

    assert client.online is True
    assert _without_age(await client.get_metrics("dev-1")) == {"bt1": 1.0}


async def test_availability_changes_notify_listeners():
    """The coordinator learns about the flip the same way it learns about data."""
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    calls: list[int] = []
    client.add_listener(lambda: calls.append(1))

    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 1.0}, {}))
    broker.deliver(availability_topic(PREFIX, SERIAL), "offline")
    # A repeat of the same value is not a change.
    broker.deliver(availability_topic(PREFIX, SERIAL), "offline")
    broker.deliver(availability_topic(PREFIX, SERIAL), "online")

    assert len(calls) == 3


async def test_unknown_availability_payload_stays_online():
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    broker.deliver(availability_topic(PREFIX, SERIAL), "garbage")

    assert client.online is False  # anything that is not "online" is not online
    broker.deliver(availability_topic(PREFIX, SERIAL), "online")
    assert client.online is True


async def test_an_identical_snapshot_does_not_wake_anyone():
    """Republishing the same values is not news and must not refresh."""
    broker = FakeBroker()
    client = _client(broker)
    await client.async_start(wait_for_state=False)

    calls: list[int] = []
    client.add_listener(lambda: calls.append(1))
    snapshot = _state({"bt1": 1.0}, {"curve_0": 20})

    broker.deliver(state_topic(PREFIX, SERIAL), snapshot)
    broker.deliver(state_topic(PREFIX, SERIAL), snapshot)
    assert len(calls) == 1

    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 2.0}, {"curve_0": 20}))
    assert len(calls) == 2


async def test_a_write_while_offline_fails_fast():
    """A known-dead pump must not cost a five-second timeout on every write."""
    broker = FakeBroker()
    client = _client(broker, timeout=5.0)
    await client.async_start(wait_for_state=False)
    broker.deliver(state_topic(PREFIX, SERIAL), _state({"bt1": 1.0}, {}))
    broker.deliver(availability_topic(PREFIX, SERIAL), "offline")

    started = asyncio.get_running_loop().time()
    with pytest.raises(TransportError, match="offline"):
        await client.update_setting("dev-1", "curve_0", 20)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1.0  # not the 5 s response timeout
    # Nothing was published: the command never left.
    assert broker.published[-1][0] == availability_topic(PREFIX, SERIAL)


def test_a_flat_telemetry_document_is_read_as_metrics() -> None:
    """The 1.13.0 firmware publishes named items, not this POC's snapshot shape."""
    client = _client(FakeBroker())
    client._handle_state(
        '{"hpid":"S1","p":{"bt11":39.0,"compressor_power":1328,'
        '"compressor_energy_kwh":6394.6,"qn8_position":0}}'
    )

    metrics = client._metrics
    # Register names pass through; the firmware's own names are mapped.
    assert metrics["bt11"] == 39.0
    assert metrics["compressor_power"] == 1328
    assert metrics["compressorenergy"] == 6394.6
    assert metrics["qn8position"] == 0


def test_an_unknown_telemetry_item_passes_through() -> None:
    """A firmware that adds an item must not need a client release."""
    client = _client(FakeBroker())
    client._handle_state('{"hpid":"S1","p":{"brand_new_reading":12.5}}')

    assert client._metrics["brand_new_reading"] == 12.5


def test_a_document_that_is_neither_shape_is_ignored() -> None:
    client = _client(FakeBroker())
    client._handle_state('{"hpid":"S1","p":{"nested":{"deep":1}}}')

    assert client._metrics == {}


def test_the_inventory_maps_the_firmware_item_names() -> None:
    """Item names read out of the 1.13.0 image, not guessed."""
    from custom_components.qvantum.client.mqtt.inventory import (
        TELEMETRY_ITEM_KEYS,
        canonical_key,
    )

    # Not the canonical names: the feed says *_energy_kwh and qn8_position.
    assert canonical_key("compressor_energy_kwh") == "compressorenergy"
    assert canonical_key("additional_energy_kwh") == "additionalenergy"
    assert canonical_key("qn8_position") == "qn8position"
    assert canonical_key("calculated_heating_supply_temperature") == "cal_heat_temp"
    # Register names are already canonical and absent from the map by design.
    assert "bt11" not in TELEMETRY_ITEM_KEYS
    assert canonical_key("bt11") == "bt11"
    # The per-unit QG set is generated for both modules.
    assert len(TELEMETRY_ITEM_KEYS) == 30
    assert canonical_key("qg2_compressor_speed_rps") == "qg2_compressor_rpm"
