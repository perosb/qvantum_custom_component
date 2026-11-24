"""MQTT transport client (POC).

A third ``QvantumClient`` implementation beside cloud HTTP and local Modbus.
It is deliberately Home-Assistant-free: the broker is reached through two
injected coroutines, so the HA layer owns the connection and this module stays
inside the vendored library.

    subscribe(topic, callback) -> unsubscribe callable
    publish(topic, payload, retain=False) -> None

The pump publishes a retained snapshot to ``state`` and answers writes on
``cmd-res``. Reads are served from the last snapshot, so a poll costs nothing
on the wire; writes are request/response with a timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..constants import (
    DHW_MODE_EXTRA,
    DHW_MODE_NORMAL,
    TAP_WATER_CAPACITY_START_STOP,
)
from ..exceptions import TransportError
from ..models import MetricsPayload, SettingsPayload
from . import topics

_LOGGER = logging.getLogger(__name__)

#: Seconds to wait for a ``cmd-res`` before failing the write.
DEFAULT_RESPONSE_TIMEOUT = 5.0

SubscribeCallback = Callable[[str], Awaitable[Callable[[], None]]]
PublishCallback = Callable[..., Awaitable[None]]
MessageCallback = Callable[[str], None]


class QvantumMqttClient:
    """Transport-agnostic client backed by an MQTT broker."""

    def __init__(
        self,
        *,
        topic_prefix: str,
        serial: str,
        device_id: str,
        subscribe: SubscribeCallback,
        publish: PublishCallback,
        writable: bool = False,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
    ) -> None:
        self._prefix = topic_prefix
        self._serial = serial
        self._device_id = device_id
        self._subscribe = subscribe
        self._publish = publish
        self._writable = writable
        self._timeout = response_timeout

        self._metrics: dict[str, Any] = {}
        self._settings: dict[str, Any] = {}
        #: ``monotonic()`` of the last state message, for the age the coordinator
        #: publishes as ``latency``. A push transport has no round-trip to time.
        self._last_state_at: float | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []
        self._pending: asyncio.Future[dict[str, Any]] | None = None
        self._write_lock = asyncio.Lock()
        self._started = False
        self._first_state = asyncio.Event()
        # Optimistic until the pump's last will says otherwise: the retained
        # availability message arrives with the subscription, and a pump that
        # has never published one is not offline, it is simply quiet.
        self._online = True

    # -- lifecycle ---------------------------------------------------------

    async def async_start(
        self, *, wait_for_state: bool = True, timeout: float = 10.0
    ) -> None:
        """Subscribe to the pump's topics and announce availability.

        A push transport has nothing to read until the first snapshot arrives,
        so the first refresh would race the broker. Wait for it here instead:
        the retained message lands in milliseconds, and a pump that never
        answers fails setup with a clear reason rather than an empty poll.
        """
        if self._started:
            return
        self._unsubscribers.append(
            await self._subscribe(
                topics.state_topic(self._prefix, self._serial), self._handle_state
            )
        )
        self._unsubscribers.append(
            await self._subscribe(
                topics.response_topic(self._prefix, self._serial),
                self._handle_response,
            )
        )
        self._unsubscribers.append(
            await self._subscribe(
                topics.availability_topic(self._prefix, self._serial),
                self._handle_availability,
            )
        )
        await self._publish(
            topics.availability_topic(self._prefix, self._serial),
            topics.AVAILABILITY_ONLINE,
            retain=True,
        )
        self._started = True
        if wait_for_state:
            try:
                await asyncio.wait_for(self._first_state.wait(), timeout)
            except TimeoutError as err:
                raise TransportError(
                    f"No MQTT state on {topics.state_topic(self._prefix, self._serial)} "
                    f"within {timeout}s"
                ) from err

    async def close(self) -> None:
        """Unsubscribe and mark the pump offline. Never closes the broker."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._started:
            await self._publish(
                topics.availability_topic(self._prefix, self._serial),
                topics.AVAILABILITY_OFFLINE,
                retain=True,
            )
        self._started = False
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(TransportError("MQTT client closed"))

    # -- push --------------------------------------------------------------

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever a new snapshot arrives; returns a remover."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def _handle_state(self, raw: str) -> None:
        payload = topics.decode_envelope(raw)
        if payload is None:
            _LOGGER.debug("Ignoring malformed MQTT state payload")
            return
        changed = False
        metrics = payload.get("metrics")
        settings = payload.get("settings")
        if isinstance(metrics, dict) and metrics != self._metrics:
            self._metrics = metrics
            changed = True
        if isinstance(settings, dict) and settings != self._settings:
            self._settings = settings
            changed = True
        self._last_state_at = time.monotonic()
        self._first_state.set()
        if not changed:
            # A publisher that republishes on a timer is not news. Waking the
            # coordinator for an identical payload would run the whole derived
            # pass for nothing.
            return
        for listener in list(self._listeners):
            listener()

    def _handle_response(self, raw: str) -> None:
        payload = topics.decode_envelope(raw)
        if payload is None:
            return
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(payload)

    def _handle_availability(self, raw: str) -> None:
        """Track the pump's last will.

        Without this a dead pump keeps serving its last snapshot: the cache
        still answers, so every entity stays available with frozen values and
        nothing says they are old. The availability topic is the only signal
        that separates "nothing changed" from "nothing is there".
        """
        online = str(raw).strip().lower() == topics.AVAILABILITY_ONLINE
        if online == self._online:
            return
        self._online = online
        for listener in list(self._listeners):
            listener()

    def _require_online(self) -> None:
        """Fail a read when the pump's last will says it is gone."""
        if not self._online:
            raise TransportError(
                f"The pump is offline on {topics.availability_topic(self._prefix, self._serial)}"
            )

    @property
    def online(self) -> bool:
        """Whether the pump last reported itself online."""
        return self._online

    # -- reads -------------------------------------------------------------

    async def get_metrics(
        self,
        device_id: str,
        enabled_metrics: list[str] | None = None,
    ) -> MetricsPayload:
        """Return the last snapshot; the pump pushes, we do not poll.

        ``latency`` is the **age of that snapshot** in milliseconds, not a
        round-trip: a push transport has nothing to time, and the snapshot's own
        ``latency`` belongs to whoever produced it — on a followed installation
        that is another instance's Modbus poll, which says nothing about this
        link.
        """
        self._require_online()
        if not self._metrics:
            raise TransportError("No MQTT state received yet")
        if enabled_metrics is None:
            metrics = dict(self._metrics)
        else:
            metrics = {
                key: value
                for key, value in self._metrics.items()
                if key in enabled_metrics
            }
        if self._last_state_at is not None:
            age_ms = (time.monotonic() - self._last_state_at) * 1000.0
            metrics["latency"] = int(age_ms)
        return {"metrics": metrics}

    async def get_settings(self, device_id: str) -> SettingsPayload:
        """Return the settings half of the last snapshot."""
        self._require_online()
        if not self._settings:
            raise TransportError("No MQTT state received yet")
        return {
            "settings": [
                {"name": name, "value": value}
                for name, value in self._settings.items()
            ]
        }

    # -- writes ------------------------------------------------------------

    @property
    def writable(self) -> bool:
        """Whether writes are allowed on this link."""
        return self._writable

    async def update_setting(
        self, device_id: str, name: str, value: Any
    ) -> dict[str, Any]:
        """Publish one setting and wait for the pump's acknowledgement."""
        return await self.update_settings(device_id, {name: value})

    async def update_settings(
        self, device_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        """Publish several settings in one command."""
        if not self._writable:
            raise TransportError("MQTT writes are disabled for this entry")
        if not settings:
            return {"status": "APPLIED"}
        await self._request(topics.VERB_SET, settings, label=sorted(settings))
        return {"status": "APPLIED"}

    async def send_command(
        self,
        device_id: str,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        writable_command: bool = True,
    ) -> dict[str, Any]:
        """Invoke a controller operation that is not a setting.

        The setpoint list, the compressor speed cap and anything else the
        pump exposes over a command rather than a register lands here: the
        state payload carries readings, ``set`` carries settings, and this is
        the third verb.

        ``writable_command`` is False for read-only operations, so a
        read-only link can still ask the pump questions.
        """
        if writable_command and not self._writable:
            raise TransportError("MQTT writes are disabled for this entry")
        payload = dict(args or {})
        response = await self._request(
            topics.VERB_COMMAND,
            {
                topics.COMMAND_NAME: name,
                topics.COMMAND_ARGS: payload,
            },
            label=name,
        )
        result = response.get(topics.RESPONSE_RESULT)
        return result if isinstance(result, dict) else {"result": result}

    async def _request(
        self, verb: str, payload: dict[str, Any], *, label: Any
    ) -> dict[str, Any]:
        """Publish one request and return the pump's acknowledgement."""
        # Reads already fail fast when the last will says offline; a write that
        # waits five seconds for a pump that is known to be gone turns one clear
        # error into a timeout cascade in whatever called it.
        self._require_online()
        async with self._write_lock:
            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            await self._publish(
                topics.command_topic(self._prefix, self._serial),
                topics.encode_envelope(self._serial, {verb: payload}),
            )
            try:
                response = await asyncio.wait_for(self._pending, self._timeout)
            except TimeoutError as err:
                raise TransportError(
                    f"No MQTT response for {label} within {self._timeout}s"
                ) from err
            finally:
                self._pending = None
        if not response.get(topics.RESPONSE_OK):
            raise TransportError(
                f"MQTT request rejected: {response.get(topics.RESPONSE_ERROR)}"
            )
        return response

    async def set_indoor_temperature_target(
        self, device_id: str, temperature: float
    ) -> dict[str, Any]:
        return await self.update_setting(
            device_id, "indoor_temperature_target", temperature
        )

    async def set_indoor_temperature_offset(
        self, device_id: str, value: int
    ) -> dict[str, Any]:
        return await self.update_setting(device_id, "indoor_temperature_offset", value)

    async def set_curve_type_heating(
        self, device_id: str, value: int
    ) -> dict[str, Any]:
        return await self.update_setting(device_id, "curve_type_heating", int(value))

    async def set_heating_curve_point(
        self, device_id: str, metric_key: str, value: int
    ) -> dict[str, Any]:
        return await self.update_setting(device_id, metric_key, int(value))

    async def set_tap_water(
        self, device_id: str, start: int = 0, stop: int = 0
    ) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        if start:
            settings["tap_water_start"] = start
        if stop:
            settings["tap_water_stop"] = stop
        return await self.update_settings(device_id, settings)

    async def set_tap_water_capacity_target(
        self, device_id: str, capacity: int
    ) -> dict[str, Any]:
        pair = TAP_WATER_CAPACITY_START_STOP.get(capacity)
        if pair is None:
            raise ValueError(f"Unknown tap-water capacity: {capacity}")
        start, stop = pair
        return await self.set_tap_water(device_id, start=start, stop=stop)

    async def set_fanspeedselector(
        self, device_id: str, preset_mode: str
    ) -> dict[str, Any]:
        return await self.update_setting(device_id, "fanspeedselector", preset_mode)

    async def set_extra_tap_water(
        self, device_id: str, minutes: int
    ) -> dict[str, Any]:
        """Write Extra/Normal only; the restore timer belongs to the caller."""
        mode = DHW_MODE_EXTRA if minutes else DHW_MODE_NORMAL
        return await self.update_setting(device_id, "extra_tap_water", mode)
