#!/usr/bin/env python3
"""Play a Qvantum heat pump on an MQTT broker (POC).

The integration's MQTT transport expects a pump that

* publishes a retained snapshot to ``<prefix>/<serial>/state`` as
  ``{"hpid": "<serial>", "p": {"metrics": {...}, "settings": {...}}}``,
* answers a write on ``<prefix>/<serial>/cmd`` by applying
  ``{"set": {name: value}}`` and republishing the snapshot, then acknowledging
  on ``<prefix>/<serial>/cmd-res`` with ``{"ok": true, "applied": {...}}``,
* answers a command — ``{"cmd": {"name": ..., "args": {...}}}`` — with
  ``{"ok": true, "result": {...}}``. The setpoint list and the compressor
  speed cap are the two the firmware exposes that way, so they are the two
  implemented here: they demonstrate the verb, not the pump's real behaviour.
* sets ``<prefix>/<serial>/availability`` to ``offline`` as its last will.

This script is that pump. It replays a snapshot captured from a real
installation, so the entities look real, and it applies writes in memory.

    python3 tools/mqtt_pump_sim.py --snapshot snapshot.json \
        --host mqtt.home-assistant --serial 3010100244154033

It can also follow a live installation instead of replaying a still image,
which is what lets one Home Assistant keep the real pump on Modbus while another
consumes the same readings over MQTT:

    python3 tools/mqtt_pump_sim.py --follow http://homeassistant.local:8123 \
        --token-file ~/.ha-token --entry <config_entry_id> --serial POC0001

Requires ``paho-mqtt`` (``uv pip install paho-mqtt``).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

ENVELOPE_SERIAL = "hpid"
ENVELOPE_PAYLOAD = "p"


def _topics(prefix: str, serial: str) -> dict[str, str]:
    base = f"{prefix.strip('/')}/{serial}"
    return {
        "state": f"{base}/state",
        "command": f"{base}/cmd",
        "response": f"{base}/cmd-res",
        "availability": f"{base}/availability",
    }


def _envelope(serial: str, payload: dict[str, Any]) -> str:
    return json.dumps({ENVELOPE_SERIAL: serial, ENVELOPE_PAYLOAD: payload})


def _payload(raw: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    body = document.get(ENVELOPE_PAYLOAD)
    return body if isinstance(body, dict) else None


class PumpSimulator:
    """In-memory pump: a snapshot plus the writes applied to it."""

    def __init__(self, snapshot: dict[str, Any], *, verbose: bool = False) -> None:
        self.metrics: dict[str, Any] = dict(snapshot.get("metrics") or {})
        self.settings: dict[str, Any] = dict(snapshot.get("settings") or {})
        self.verbose = verbose
        self.writes = 0
        #: Settings the dev instance wrote. A real pump reports them back, so a
        #: follow pull must not undo them with the source installation's value.
        self.written: set[str] = set()

    def snapshot(self) -> dict[str, Any]:
        return {"metrics": self.metrics, "settings": self.settings}

    #: Compressor speed cap: the firmware's six ``limitStep`` values.
    LIMIT_STEPS = (7200, 6000, 5400, 4800, 4200, 3600)

    def command(
        self, name: str, args: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], str | None]:
        """Answer a ``cmd`` verb. Unknown commands are rejected, not guessed."""
        if name == "read_all_setpoints":
            return True, {
                "points": [
                    {"name": key, "value": value}
                    for key, value in sorted(self.settings.items())
                ]
            }, None
        if name == "limit_max_compressor_speed":
            step = args.get("limitStep")
            if not isinstance(step, int) or isinstance(step, bool):
                return False, {}, "'limitStep' does not exist or is not an integer"
            if not 0 <= step < len(self.LIMIT_STEPS):
                return False, {}, "'limitStep' has invalid value, expecting 0 - 5"
            return True, {"limitStep": step, "maxSpeed": self.LIMIT_STEPS[step]}, None
        return False, {}, f"unknown command: {name}"

    def follow(self, snapshot: dict[str, Any]) -> bool:
        """Adopt a live snapshot, keeping settings this instance wrote.

        Returns True when anything moved, so the caller can publish only on a
        change instead of on every poll.
        """
        changed = False
        metrics = snapshot.get("metrics") or {}
        if metrics != self.metrics:
            self.metrics = dict(metrics)
            changed = True
        for key, value in (snapshot.get("settings") or {}).items():
            if key in self.written or self.settings.get(key) == value:
                continue
            self.settings[key] = value
            changed = True
        return changed

    def apply(self, requested: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None]:
        """Apply a ``set`` object; unknown keys are rejected, not invented."""
        unknown = [key for key in requested if key not in self.settings]
        if unknown:
            return False, {}, f"unknown setting(s): {', '.join(sorted(unknown))}"
        for key, value in requested.items():
            self.settings[key] = value
            self.written.add(key)
        self.writes += 1
        if self.verbose:
            print(f"  applied {requested}", flush=True)
        return True, dict(requested), None


def _fetch_diagnostics(base_url: str, token: str, entry_id: str) -> dict:
    """Pull one config-entry diagnostics download and split it into a snapshot."""
    from snapshot_from_diagnostics import split

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/diagnostics/config_entry/{entry_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return split(json.loads(response.read().decode("utf-8")))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument(
        "--follow", metavar="URL", help="follow a live HA instance instead of a file"
    )
    parser.add_argument("--entry", help="config entry id to follow")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--host", default="mqtt.home-assistant")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--prefix", default="qvantum")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if bool(args.snapshot) == bool(args.follow):
        parser.error("give exactly one of --snapshot and --follow")

    token = None
    if args.follow:
        if not args.entry:
            parser.error("--follow needs --entry")
        token_path = args.token_file or Path(
            os.environ.get("HA_TOKEN_FILE", "~/.ha-token")
        ).expanduser()
        if not token_path.is_file():
            parser.error(f"token file not found: {token_path}")
        token = token_path.read_text(encoding="utf-8").strip()
        try:
            snapshot = _fetch_diagnostics(args.follow, token, args.entry)
        except (urllib.error.URLError, ValueError) as err:
            parser.error(f"could not read diagnostics: {err}")
    else:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))

    pump = PumpSimulator(snapshot, verbose=args.verbose)
    topics = _topics(args.prefix, args.serial)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pump-sim-{args.serial}")
    if args.username:
        client.username_pw_set(args.username, args.password)
    client.will_set(topics["availability"], "offline", retain=True)

    def publish_state() -> None:
        client.publish(topics["state"], _envelope(args.serial, pump.snapshot()), retain=True)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            print(f"connect failed: {reason_code}", file=sys.stderr)
            return
        client.subscribe(topics["command"], qos=1)
        client.publish(topics["availability"], "online", retain=True)
        publish_state()
        print(
            f"pump online on {args.host}:{args.port} as {args.serial} "
            f"({len(pump.metrics)} metrics, {len(pump.settings)} settings)",
            flush=True,
        )

    def on_message(client, userdata, message):
        body = _payload(message.payload)
        if body is None:
            print("  ignoring malformed command", flush=True)
            return
        command = body.get("cmd")
        if isinstance(command, dict):
            name = command.get("name")
            command_args = command.get("args")
            if not isinstance(name, str):
                print("  ignoring cmd without a name", flush=True)
                return
            ok, result, error = pump.command(
                name, command_args if isinstance(command_args, dict) else {}
            )
            response = {"ok": ok}
            if ok:
                response["result"] = result
            else:
                response["error"] = error
            client.publish(
                topics["response"], _envelope(args.serial, response)
            )
            if args.verbose:
                print(f"  cmd {name} -> {'ok' if ok else error}", flush=True)
            return

        requested = body.get("set")
        if not isinstance(requested, dict):
            print("  ignoring command without a 'set' object", flush=True)
            return
        ok, applied, error = pump.apply(requested)
        if ok:
            publish_state()
        response: dict[str, Any] = {"ok": ok}
        if ok:
            response["applied"] = applied
        else:
            response["error"] = error
        client.publish(topics["response"], _envelope(args.serial, response))
        if not ok:
            print(f"  rejected {requested}: {error}", flush=True)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    stop_follow = threading.Event()
    if args.follow:

        def _follow() -> None:
            while not stop_follow.wait(args.interval):
                try:
                    live = _fetch_diagnostics(args.follow, token, args.entry)
                except (urllib.error.URLError, ValueError) as err:
                    print(f"  follow failed: {err}", flush=True)
                    continue
                if pump.follow(live):
                    publish_state()
                    if args.verbose:
                        print("  followed a change from the source", flush=True)

        threading.Thread(target=_follow, daemon=True).start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.is_set():
            time.sleep(0.2)
    finally:
        stop_follow.set()
        # A clean DISCONNECT discards the last will, so the offline message has
        # to be sent explicitly — and *be delivered*: stopping the network loop
        # first leaves it queued, and the retained topic keeps saying "online"
        # while the process is gone. That makes a dead pump look alive to every
        # subscriber, which is the one thing the availability topic exists to
        # prevent.
        info = client.publish(topics["availability"], "offline", retain=True)
        try:
            info.wait_for_publish(timeout=2.0)
        except (RuntimeError, ValueError):
            pass
        time.sleep(0.1)
        client.loop_stop()
        client.disconnect()
        print(f"pump offline after {pump.writes} write(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
