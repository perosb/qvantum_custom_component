#!/usr/bin/env python3
"""Turn a Qvantum diagnostics download into the simulator's snapshot file.

The pump simulator replays a snapshot of the integration's coordinator values:

    {"metrics": {name: value, ...}, "settings": {name: value, ...}}

A config-entry diagnostics download already carries exactly that dict under
``data.coordinator.values``, so this splits it by the holding-register map and
writes the file the simulator wants.

    python3 tools/snapshot_from_diagnostics.py diagnostics.json -o snapshot.json

The split needs ``MODBUS_HOLDING_TO_SETTINGS_MAP``. It is read with a regex
rather than imported: importing it pulls
``custom_components/qvantum/__init__.py``, which imports Home Assistant.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAPS = REPO_ROOT / "custom_components" / "qvantum" / "client" / "modbus" / "maps.py"

#: Settings that are written by name but do not come from a holding register.
EXTRA_SETTING_KEYS = frozenset(
    {
        "extra_tap_water",
        "fanspeedselector",
        "indoor_temperature_target",
        "indoor_temperature_offset",
        "tap_water_start",
        "tap_water_stop",
        "curve_type_heating",
        "temp_compensation_curve",
        "tap_water_capacity_target",
    }
)


def setting_keys() -> set[str]:
    """Canonical settings names, read out of the holding map source."""
    source = MAPS.read_text(encoding="utf-8")
    block = re.search(
        r"MODBUS_HOLDING_TO_SETTINGS_MAP[^=]*=\s*\{(.*?)\n\}", source, re.S
    )
    if block is None:
        raise SystemExit(f"could not find the holding map in {MAPS}")
    names = set(re.findall(r':\s*"([a-z0-9_]+)"', block.group(1)))
    return names | set(EXTRA_SETTING_KEYS)


def split(diagnostics: dict) -> dict:
    """Split a diagnostics document into the simulator's two halves."""
    values = (
        diagnostics.get("data", {})
        .get("coordinator", {})
        .get("values")
    )
    if not isinstance(values, dict):
        raise SystemExit(
            "no data.coordinator.values — this is not a config-entry diagnostics "
            "download, or the entry has not polled yet"
        )
    settings = setting_keys()
    return {
        "metrics": {k: v for k, v in values.items() if k not in settings},
        "settings": {k: v for k, v in values.items() if k in settings},
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)

    snapshot = split(json.loads(args.diagnostics.read_text(encoding="utf-8")))
    args.output.write_text(
        json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"{len(snapshot['metrics'])} metrics and {len(snapshot['settings'])} "
        f"settings -> {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
