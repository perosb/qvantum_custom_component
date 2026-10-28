"""Tests for the Qvantum alarm-code names."""

import pytest

from custom_components.qvantum.alarm_codes import ALARM_CODES, alarm_description


class TestAlarmCodes:
    def test_table_is_a_code_to_name_map(self):
        assert len(ALARM_CODES) >= 100
        for code, name in ALARM_CODES.items():
            assert isinstance(code, int)
            assert 0 < code <= 999
            assert isinstance(name, str)
            assert name and name == name.strip()

    @pytest.mark.parametrize(
        ("code", "name"),
        [
            (1, "Outdoor Sensor Error (BT1)"),
            (33, "High Pressure Alarm"),
            (279, "Fault of water flow switch on the ground source side."),
        ],
    )
    def test_documented_codes_keep_their_names(self, code, name):
        assert ALARM_CODES[code] == name

    def test_app_only_codes_survive_a_page_refresh(self):
        """252 and 391 are not on qvantum.com/alarms/ but the app shows them."""
        assert alarm_description(252) == "Source water low temperature protection"
        assert alarm_description(391) == (
            "QGM2 fault of water flow switch on the ground source side"
        )

    @pytest.mark.parametrize("code", [0, 999, -1, None, "", "abc", True, False])
    def test_unknown_or_invalid_codes_have_no_name(self, code):
        assert alarm_description(code) is None

    def test_numeric_strings_resolve(self):
        assert alarm_description("33") == "High Pressure Alarm"
        assert alarm_description(33.0) == "High Pressure Alarm"
