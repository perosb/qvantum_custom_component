"""Typed Modbus components for the Qvantum heat pump.

Field layouts are generated from the register maps in ``modbus.py`` so the
maps stay the datasheet and the components stay in lock-step with them.
Relay bits replace the ``relays_bitmask`` word rather than duplicating it.
"""

from __future__ import annotations

from modbus_connection.model import Component, bit, gauge, integer

from .modbus import (
    MODBUS_HOLDING_REGISTER_MAP,
    MODBUS_INPUT_REGISTER_MAP,
    RELAY_BIT_MAP,
)

_RELAYS_BITMASK_ADDRESS = MODBUS_INPUT_REGISTER_MAP["relays_bitmask"][0]


def _register_field(
    data_type: str, address: int, scale: float, *, writable: bool = False
):
    """Return a gauge or integer field matching a register-map tuple."""
    signed = data_type == "int16"
    if scale == 1.0:
        return integer(address, signed=signed, writable=writable)
    return gauge(address, scale, signed=signed, writable=writable)


def _component_from_map(
    name: str,
    register_space: str,
    register_map: dict,
    *,
    writable: bool = False,
    extra: dict | None = None,
    skip: frozenset[str] = frozenset(),
    max_gap: int = 16,
    register_ranges: tuple[tuple[int, int], ...] | None = None,
) -> type[Component]:
    # max_gap=16 matches a live Qvantum-HP probe: documented holes answer.
    # register_ranges keep reads off the refused input 105-160 span.
    attrs: dict = {"register_space": register_space, "max_gap": max_gap}
    if register_ranges is not None:
        attrs["register_ranges"] = register_ranges
    for field_name, (address, data_type, scale) in register_map.items():
        if field_name in skip:
            continue
        attrs[field_name] = _register_field(
            data_type, address, scale, writable=writable
        )
    if extra:
        attrs.update(extra)
    return type(name, (Component,), attrs)


QvantumInputs = _component_from_map(
    "QvantumInputs",
    "input",
    MODBUS_INPUT_REGISTER_MAP,
    skip=frozenset({"relays_bitmask"}),
    extra={
        name: bit(_RELAYS_BITMASK_ADDRESS, index)
        for name, index in RELAY_BIT_MAP.items()
    },
    # Live probe: 0-104 answers, 105-160 raises 0x04, 161-164 answers.
    register_ranges=((0, 104), (161, 164)),
)

QvantumSettings = _component_from_map(
    "QvantumSettings",
    "holding",
    MODBUS_HOLDING_REGISTER_MAP,
    writable=True,
    # Live probe: the mapped holding span 0-88 answers as one block.
    register_ranges=((0, 88),),
)
