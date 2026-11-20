"""Topic layout and message envelope for the MQTT transport (POC).

The envelope mirrors the display's own MQTT API: every message is

    {"hpid": "<serial>", "p": <payload>}

Qvantum signs their responses (``kid`` / ``signed_payload``) with a certificate
built into the display. We cannot, so this transport answers with a plain ``p``
and the difference is documented rather than faked.

Topics, all under ``<prefix>/<serial>``:

================  =========  ==============================================
topic             retained   payload
================  =========  ==============================================
``state``         yes        ``{"metrics": {...}, "settings": {...}}``
``cmd``           no         ``{"set": {name: value, ...}}`` or
                             ``{"cmd": {"name": ..., "args": {...}}}``
``cmd-res``       no         ``{"ok": true, "applied": {...}}`` or
                             ``{"ok": true, "result": {...}}``
``availability``  yes        ``online`` / ``offline`` (last will)
================  =========  ==============================================

The three verbs are the three things a transport has to carry: **metrics**
(read), **settings** (read and write) and **commands** (the controller
operations that are neither, such as the setpoint list or the compressor speed
cap). ``set`` writes a canonical setting; ``cmd`` invokes an operation.
"""

from __future__ import annotations

import json
from typing import Any

#: Envelope keys, matching the display's own MQTT API.
ENVELOPE_SERIAL = "hpid"
ENVELOPE_PAYLOAD = "p"

#: Suffixes appended to ``<prefix>/<serial>``.
SUFFIX_STATE = "state"
SUFFIX_COMMAND = "cmd"
SUFFIX_RESPONSE = "cmd-res"
SUFFIX_AVAILABILITY = "availability"

AVAILABILITY_ONLINE = "online"
AVAILABILITY_OFFLINE = "offline"

#: Payload verbs. ``set`` writes a canonical setting; ``cmd`` invokes a
#: controller operation that is not a setting (the setpoint list, the
#: compressor speed cap).
VERB_SET = "set"
VERB_COMMAND = "cmd"
COMMAND_NAME = "name"
COMMAND_ARGS = "args"
RESPONSE_OK = "ok"
RESPONSE_APPLIED = "applied"
RESPONSE_RESULT = "result"
RESPONSE_ERROR = "error"


def _base(prefix: str, serial: str) -> str:
    return f"{prefix.strip('/')}/{serial}"


def state_topic(prefix: str, serial: str) -> str:
    """Retained topic carrying the full metric and setting snapshot."""
    return f"{_base(prefix, serial)}/{SUFFIX_STATE}"


def command_topic(prefix: str, serial: str) -> str:
    """Topic the integration publishes writes to."""
    return f"{_base(prefix, serial)}/{SUFFIX_COMMAND}"


def response_topic(prefix: str, serial: str) -> str:
    """Topic the pump answers a write on."""
    return f"{_base(prefix, serial)}/{SUFFIX_RESPONSE}"


def availability_topic(prefix: str, serial: str) -> str:
    """Topic carrying ``online`` / ``offline``; the pump sets it as last will."""
    return f"{_base(prefix, serial)}/{SUFFIX_AVAILABILITY}"


def encode_envelope(serial: str, payload: dict[str, Any]) -> str:
    """Serialise ``payload`` inside the ``{"hpid", "p"}`` envelope."""
    return json.dumps(
        {ENVELOPE_SERIAL: serial, ENVELOPE_PAYLOAD: payload},
        separators=(",", ":"),
    )


def decode_envelope(raw: str | bytes | None) -> dict[str, Any] | None:
    """Return the ``p`` object of an envelope, or ``None`` if it is malformed."""
    if raw is None:
        return None
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    payload = document.get(ENVELOPE_PAYLOAD)
    return payload if isinstance(payload, dict) else None
