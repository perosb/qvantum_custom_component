"""Typed Modbus components for the Qvantum heat pump.

Field layouts are generated from the register maps in ``maps`` so the maps
stay the datasheet and the components stay in lock-step with them.
Relay bits replace the ``relays_bitmask`` word rather than duplicating it.
"""

from __future__ import annotations

from modbus_connection.model import Component, bit, gauge, integer

from .maps import (
    MODBUS_HOLDING_REGISTER_MAP,
    MODBUS_IDENTITY_REGISTER_MAP,
    MODBUS_INPUT_REGISTER_MAP,
    RELAY_BIT_MAP,
)

_RELAYS_BITMASK_ADDRESS = MODBUS_INPUT_REGISTER_MAP["relays_bitmask"][0]


def _write_validator(signed: bool, scale: float):
    """Return a write validator rejecting values the register cannot hold.

    ``encode_int`` only checks the 16-bit width, so the scaled raw value can
    still wrap: a negative value on an unsigned register becomes a large
    positive one, and a value above the signed range becomes negative. Bound
    the raw value the same way ``NumberField.encode`` computes it.
    """
    low, high = (-32768, 32767) if signed else (0, 65535)
    kind = "signed" if signed else "unsigned"
    # Mirror the field's rounding precision so the bound matches encode().
    decimals = max(0, len(f"{scale:.10f}".rstrip("0").split(".")[1]))

    def _validate(value):
        if isinstance(value, bool):
            return value
        if not isinstance(value, (int, float)):
            raise ValueError(f"value {value!r} is not numeric")
        raw = round(round(float(value), decimals) / scale)
        if not low <= raw <= high:
            raise ValueError(
                f"value {value} does not fit the {kind} 16-bit register"
            )
        return value

    return _validate


def _register_field(
    data_type: str, address: int, scale: float, *, writable: bool = False
):
    """Return a gauge or integer field matching a register-map tuple."""
    signed = data_type == "int16"
    write_arg = _write_validator(signed, scale) if writable else False
    if scale == 1.0:
        return integer(address, signed=signed, writable=write_arg)
    return gauge(address, scale, signed=signed, writable=write_arg)


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
    # register_ranges keep reads off the refused QGM1/QGM2 span 119-146.
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
    # Live probe: 0-104 answers; QGM1/QGM2 inputs 119-146 raise 0x04;
    # 150-170 answers (alarms 150-155, smart/wifi/cloud/vacation 161-170).
    # Price registers 165-166 are unread.
    register_ranges=((0, 104), (150, 170)),
)

QvantumSettings = _component_from_map(
    "QvantumSettings",
    "holding",
    MODBUS_HOLDING_REGISTER_MAP,
    writable=True,
    # Live probe: the mapped holding span 0-88 answers as one block.
    register_ranges=((0, 88),),
)

QvantumIdentity = _component_from_map(
    "QvantumIdentity",
    "input",
    MODBUS_IDENTITY_REGISTER_MAP,
    # Datasheet island after the 105-160 hole: serial 180-184, IP 186-189,
    # version 191-193. Unused 185/190 sit inside the range.
    register_ranges=((180, 193),),
)
