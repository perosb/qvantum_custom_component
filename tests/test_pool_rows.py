"""The pool's row shapes, pinned so a schema change cannot pass unnoticed.

The Cloudflare pool holds rows from many integration versions at once — measured
2026-11-25: 107 rows over schemas 5, 7, 8, 9, 10, 11, 13, 14 and 16 while the code
was at 19. A consumer therefore has to read several generations at the same time,
and a field added without bumping `SUMMARY_SCHEMA_VERSION` makes two different
shapes wear one version number with nothing to tell them apart.

`tests/data/pool_rows.json` holds one row per interesting generation. The older
ones are real rows with the per-installation pseudonyms removed (`install_token`,
`key`, `generated_on`); the current one is generated from this suite's own
builder, so it carries no user data at all.

**When you bump the version, add a row.** Two tests below fail until you do, which
is the point: the fixtures are a contract, not a convenience.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_summary import build, curve_coordinator

from custom_components.qvantum.summary import SUMMARY_SCHEMA_VERSION

FIXTURE = Path(__file__).parent / "data" / "pool_rows.json"
BLOCKS = ("plant", "fitted", "curve", "counters", "climate")


def _rows() -> dict[str, dict]:
    return json.loads(FIXTURE.read_text())


def test_every_row_declares_its_own_version():
    """A row filed under the wrong version tells the reader nothing useful."""
    for version, row in _rows().items():
        assert row["schema_version"] == int(version)


def test_the_current_version_has_a_row():
    """A bump without a fixture leaves the pool's newest shape unreadable.

    This is the check that makes the fixtures a contract: raising
    `SUMMARY_SCHEMA_VERSION` fails here until a row for the new version exists.
    """
    assert str(SUMMARY_SCHEMA_VERSION) in _rows()


def test_the_current_row_matches_what_the_code_exports():
    """The current fixture and the code must describe the same blocks and keys.

    Comparing them both ways is deliberate: a field added without a bump fails
    here (the fixture is missing it), and so does a field removed from the code
    while the fixture still carries it.
    """
    stored = _rows()[str(SUMMARY_SCHEMA_VERSION)]
    # The curve coordinator supplies the counters block; without one the export
    # carries `counters: null` and its keys would go unchecked.
    curve = curve_coordinator()
    curve.counters = {
        "since": "2026-11-18",
        "reverts": 0,
        "trim_updates": 12,
        "days_clamped": 0,
        "days_extrapolated": 7,
        "days_at_curve_end": 7,
        "revert_reasons": {"switched_off": 0},
    }
    live = build(curve=curve)

    for block in BLOCKS:
        assert isinstance(stored.get(block), dict), f"fixture has no {block}"
        assert set(stored[block]) == set(live[block]), (
            f"{block} differs between the v{SUMMARY_SCHEMA_VERSION} fixture and "
            "the export; regenerate tests/data/pool_rows.json"
        )


@pytest.mark.parametrize("version", sorted(_rows()))
def test_an_older_row_still_parses(version):
    """Every generation must survive a walk over the known blocks.

    A consumer that assumed the newest shape would raise a `KeyError` on the
    oldest row in the pool, which is exactly what a parser has to avoid: missing
    is not the same as malformed.
    """
    row = _rows()[version]

    # `plant`, `fitted` and `climate` existed from the first export. `curve` and
    # `counters` came later, so an old row carries `null` there — missing is not
    # the same as malformed, and a consumer has to read it that way.
    for block in ("plant", "fitted", "climate"):
        assert isinstance(row.get(block), dict), f"v{version} has no {block} block"
    for block in ("curve", "counters"):
        assert row.get(block) is None or isinstance(row[block], dict)


def test_a_row_carries_no_installation_identifier():
    """The export is identifier-free, and the fixtures must stay that way."""
    for version, row in _rows().items():
        for field in ("install_token", "key", "generated_on"):
            assert field not in row, f"v{version} still carries {field}"


def test_a_string_setting_survives_the_plant_coercion():
    """`fanspeedselector` is an enum, and `_number` would export it as null.

    The same defect shipped once already: `sensor_mode` arrives from the cloud as
    a name, and a numeric-only coercion dropped it, so every cloud row carried
    null. A plant field whose value is a short string needs its own branch.
    """
    from custom_components.qvantum.summary import plant_summary

    plant = plant_summary({"fan_speed_2": 65, "fanspeedselector": "normal"})

    assert plant["fan_speed_2"] == 65
    assert plant["fanspeedselector"] == "normal"
