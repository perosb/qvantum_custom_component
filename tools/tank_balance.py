#!/usr/bin/env python3
"""Measure the QE's real heating output from the tank balance after a draw.

The pump's own ``heatingenergy`` counter reads below the compressor's electrical
input (see ``docs/qe-architecture.md`` §11), so the derived COP series is a trend
and not a level. The 175 L buffer tank is a calorimeter we can read instead:
energy in equals ``ΔBT30 · V · c``, and the electricity that produced it is on
two counters the pump reports. What is left over came from the exhaust air.

The window matters. During the draw itself the compressor feeds the plate heat
exchanger directly and nothing is metered, so the window must start when the
draw **ends**; it ends when the heating circuit takes heat again (the first
``heatingenergy`` step), because that is where DHW priority releases. A defrost
inside the window takes heat out of the tank and invalidates the balance.

Usage::

    HA_TOKEN=... python tools/tank_balance.py --hours 6
    HA_TOKEN=... python tools/tank_balance.py --start 19:05 --end 20:05

``HA_URL`` defaults to ``http://homeassistant.home-assistant``. The tank volume
is read from ``--liters``; leaving it out uses the datasheet 175 L and prints
the volume implied by the draw as a cross-check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_URL = "http://homeassistant.home-assistant"
DEFAULT_TOKEN_FILE = "~/.ha-token"

#: Water: 4.186 kJ/(kg·K) at 1 kg/L, expressed in Wh to match the counters.
WATER_WH_PER_LITER_K = 4.186 / 3.6
#: Dry air near 20 °C at 1.2 kg/m³, in kJ/(kg·K).
AIR_KJ_PER_KG_K = 1.005
AIR_KG_PER_M3 = 1.2
DATASHEET_TANK_LITERS = 175.0
#: A draw's counter steps arrive within a couple of polls of each other.
DRAW_GAP_SECONDS = 300.0

ENTITIES = {
    "tank": "sensor.qvantum_hot_water_tank_temperature",
    "draw_energy": "sensor.qvantum_varmvattenenergi",
    "compressor_energy": "sensor.qvantum_compressor_energy",
    "element_energy": "sensor.qvantum_additional_energy",
    "heating_energy": "sensor.qvantum_varmeenergi",
    "air_in": "sensor.qvantum_franluftstemp_bt14",
    "flow_line": "sensor.qvantum_heating_medium_flow_line_temperature",
    "air_out": "sensor.qvantum_avluftstemp_bt15",
    "defrosts": "sensor.tvattstugan_qvantum_antal_avfrostningar",
    "shunt": "sensor.qvantum_shuntventil_tillsats_qn8",
}


def _token() -> str:
    token = os.environ.get("HA_TOKEN")
    if token:
        return token.strip()
    path = Path(os.environ.get("HA_TOKEN_FILE", DEFAULT_TOKEN_FILE)).expanduser()
    try:
        return path.read_text().strip()
    except OSError as err:
        raise SystemExit(
            f"No token: set HA_TOKEN or put one in {path} ({err})"
        ) from err


class Recorder:
    """Minimal REST client: one history call, then pure analysis."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    def _get(self, path: str) -> object:
        request = urllib.request.Request(
            self._base + path, headers={"Authorization": f"Bearer {self._token}"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())

    def history(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[tuple[datetime, float]]]:
        query = (
            f"/api/history/period/{start:%Y-%m-%dT%H:%M:%SZ}"
            f"?end_time={end:%Y-%m-%dT%H:%M:%SZ}"
            f"&filter_entity_id={','.join(entity_ids)}&minimal_response"
        )
        series: dict[str, list[tuple[datetime, float]]] = {}
        for block in self._get(query):
            if not block:
                continue
            entity_id = block[0].get("entity_id") or ""
            points = []
            for state in block:
                value = state.get("state")
                if value in (None, "unknown", "unavailable"):
                    continue
                try:
                    points.append(
                        (
                            datetime.fromisoformat(
                                state["last_changed"].replace("Z", "+00:00")
                            ),
                            float(value),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            series[entity_id] = sorted(points)
        return series


def _steps(
    points: list[tuple[datetime, float]], start: datetime, end: datetime
) -> list[datetime]:
    """Times at which a cumulative counter increased inside the window."""
    return [
        points[index][0]
        for index in range(1, len(points))
        if start <= points[index][0] <= end and points[index][1] > points[index - 1][1]
    ]


def _value_at(points: list[tuple[datetime, float]], when: datetime) -> float | None:
    found = None
    for stamp, value in points:
        if stamp <= when:
            found = value
        else:
            break
    return found


def _delta(
    points: list[tuple[datetime, float]], start: datetime, end: datetime
) -> float | None:
    first, last = _value_at(points, start), _value_at(points, end)
    if first is None or last is None or last < first:
        return None
    return last - first


def _time_weighted(
    points: list[tuple[datetime, float]], start: datetime, end: datetime
) -> float | None:
    """Mean of a sampled signal, weighted by how long each value held."""
    total = 0.0
    weight = 0.0
    for index, (stamp, value) in enumerate(points):
        # A sample is only in force until the next one arrives. The last one has
        # no successor, so it covers nothing: holding it to the window's end
        # would turn a gap into an observation.
        until = min(points[index + 1][0], end) if index + 1 < len(points) else stamp
        held_from = max(stamp, start)
        held_until = until
        if held_until > held_from:
            total += value * (held_until - held_from).total_seconds()
            weight += (held_until - held_from).total_seconds()
    return total / weight if weight else None


def _parse_clock(text: str, reference: datetime) -> datetime:
    """Today's HH:MM in the recorder's own timezone, or an ISO timestamp."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        hour, _, minute = text.partition(":")
        if not hour.isdigit() or not minute.isdigit():
            raise SystemExit(f"Not a time: {text!r} (use HH:MM or ISO 8601)") from None
        return reference.replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0
        )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=6.0,
                        help="how far back to search for the last draw (default 6)")
    parser.add_argument("--start", help="charge window start (HH:MM or ISO)")
    parser.add_argument("--end", help="charge window end (HH:MM or ISO)")
    parser.add_argument("--liters", type=float, default=DATASHEET_TANK_LITERS,
                        help=f"tank volume (default {DATASHEET_TANK_LITERS:.0f}; the "
                             "identified dhw_tank_liters is better)")
    args = parser.parse_args(argv)

    recorder = Recorder(os.environ.get("HA_URL", DEFAULT_URL), _token())
    now = datetime.now(UTC)
    start = now - timedelta(hours=max(args.hours, 0.5))
    series = recorder.history(list(ENTITIES.values()), start, now)

    def points(key: str) -> list[tuple[datetime, float]]:
        return series.get(ENTITIES[key], [])

    missing = [f"{key} ({entity})" for key, entity in ENTITIES.items() if not points(key)]
    for entry in missing:
        print(f"warning: no data for {entry}", file=sys.stderr)

    draws = points("draw_energy")
    if len(draws) < 2:
        print("No draw counter steps in the window: nothing to measure.", file=sys.stderr)
        return 2

    # The last draw: consecutive counter steps, allowing for poll spacing.
    draw_steps = [
        draws[index][0]
        for index in range(1, len(draws))
        if draws[index][1] > draws[index - 1][1]
    ]
    if not draw_steps:
        print("The draw counter never stepped: nothing to measure.", file=sys.stderr)
        return 2
    last = draw_steps[-1]
    run = [last]
    for stamp in reversed(draw_steps[:-1]):
        if (run[0] - stamp).total_seconds() > DRAW_GAP_SECONDS:
            break
        run.insert(0, stamp)
    draw_start, draw_end = run[0], run[-1]

    if args.start:
        window_start = _parse_clock(args.start, now)
    else:
        # The tank keeps falling for a few minutes after the counter's last step
        # (mixing), so the window opens when it bottoms out — not when the draw
        # meter stops. Starting earlier books the draw's own tail as a deficit.
        settling = [
            (stamp, value)
            for stamp, value in points("tank")
            if draw_end <= stamp <= draw_end + timedelta(minutes=20)
        ]
        window_start = draw_end
        if settling:
            window_start, _ = min(settling, key=lambda sample: sample[1])
    if args.end:
        window_end = _parse_clock(args.end, now)
    else:
        resumed = [
            stamp
            for stamp in _steps(points("heating_energy"), window_start, now)
            if stamp > window_start
        ]
        window_end = resumed[0] if resumed else now

    hours = (window_end - window_start).total_seconds() / 3600
    draw_kwh = _delta(draws, draw_start, draw_end) or 0.0
    tank_drop = (_value_at(points("tank"), draw_start) or 0.0) - (
        _value_at(points("tank"), draw_end) or 0.0
    )
    implied_liters = (
        draw_kwh * 1000.0 / (tank_drop * WATER_WH_PER_LITER_K) if tank_drop > 0 else None
    )

    tank_rise = _delta(points("tank"), window_start, window_end)
    compressor = _delta(points("compressor_energy"), window_start, window_end)
    element = _delta(points("element_energy"), window_start, window_end) or 0.0
    air_in = _time_weighted(points("air_in"), window_start, window_end)
    air_out = _time_weighted(points("air_out"), window_start, window_end)

    print(f"\nDraw      {draw_start:%H:%M:%S} → {draw_end:%H:%M:%S}  "
          f"{draw_kwh:.2f} kWh drawn, tank −{tank_drop:.1f} K")
    if implied_liters:
        print(f"          the draw implies {implied_liters:.0f} L, biased high: the "
              "compressor charges while the draw runs")
    print(f"Window    {window_start:%H:%M:%S} → {window_end:%H:%M:%S}  ({hours:.2f} h)")

    if tank_rise is None or compressor is None:
        print("\nNot enough data inside the window.", file=sys.stderr)
        return 2

    tank_kwh = tank_rise * args.liters * WATER_WH_PER_LITER_K / 1000.0
    electrical = compressor + element
    air_kwh = tank_kwh - electrical
    air_delta = (air_in - air_out) if air_in is not None and air_out is not None else None
    flow = None
    if air_delta and air_delta > 0 and air_kwh > 0:
        airflow_kg_s = air_kwh * 3600.0 / (AIR_KJ_PER_KG_K * air_delta)
        flow = airflow_kg_s * 3600.0 / AIR_KG_PER_M3

    print()
    print(f"  tank rise        {tank_rise:+6.1f} K  → {tank_kwh:6.2f} kWh   "
          f"({args.liters:.0f} L)")
    print(f"  compressor       {compressor:6.2f} kWh el")
    print(f"  element          {element:6.2f} kWh el")
    print(f"  from the air     {air_kwh:6.2f} kWh   ← tank − electricity")
    if air_delta is not None:
        print(f"  air in/out       {air_in:5.1f} / {air_out:5.1f} °C  (ΔT {air_delta:4.1f} K)")
    if flow:
        print(f"  implied airflow  {flow:6.0f} m³/h")
    if electrical > 0:
        print(f"  COP (tank/el)    {tank_kwh / electrical:6.2f}")

    problems = []
    if _steps(points("draw_energy"), window_start + timedelta(seconds=1), window_end):
        problems.append("the draw counter stepped inside the window (a second draw)")
    if _steps(points("defrosts"), window_start, window_end):
        problems.append("a defrost ran inside the window (it takes heat out of the tank)")
    if _steps(points("heating_energy"), window_start + timedelta(seconds=1), window_end):
        problems.append("the heating circuit took heat inside the window")
    if (_value_at(points("shunt"), window_end) or 0.0) > 0:
        problems.append("the addition shunt was open")
    # After a big draw the whole water inventory is cold, so the compressor
    # refills the circuit before it lifts the tank — a real sink that
    # `heatingenergy` does not meter, because the circuit is warming, not
    # delivering. The flow line falling across the window is that state.
    flow_start = _value_at(points("flow_line"), window_start)
    flow_end = _value_at(points("flow_line"), window_end)
    if flow_start is not None and flow_end is not None and flow_end < flow_start - 0.5:
        problems.append(
            f"the heating circuit was still cold and refilling (BT11 "
            f"{flow_start:.1f} → {flow_end:.1f} °C), an unmetered sink"
        )
    if air_kwh <= 0:
        problems.append("the air term is not positive: the balance does not close")
    if air_kwh > 0 and electrical > 0 and air_kwh < 0.25 * electrical:
        problems.append(
            f"the air term ({air_kwh:.2f} kWh) is only "
            f"{100 * air_kwh / electrical:.0f} % of the electricity — too small to "
            "be real, so the tank misses energy"
        )
    if flow is not None and not 40.0 <= flow <= 400.0:
        problems.append(
            f"the implied airflow ({flow:.0f} m³/h) is implausible: the tank and the "
            "electricity are too close to resolve their difference"
        )

    if problems:
        print("\nThis window is not a clean measurement:")
        print("  (a partial charge is the usual cause: BT30 is one point in a "
              "stratified\n   tank, so the top warms while the sensor still lags)")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nClean window: the tank is the only sink.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
