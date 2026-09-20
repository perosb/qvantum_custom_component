import json
import re
from pathlib import Path

# Home Assistant hassfest: [a-z0-9-_]+, not starting/ending with - or _,
# and no empty segments (so not `curve_-30`).
_TRANSLATION_KEY = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")


TRANSLATIONS_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "qvantum"
    / "translations"
)


def test_danish_and_czech_translations_are_available():
    expected_strings = {
        "da": {
            "config_title": "Qvantum varmepumpe",
            "powertotal": "Samlet effekt",
            "smart_dhw_control_status": "Smart DHW-styringsstatus",
            "hp_status_name": "Varmepumpestatus",
            "hp_status_defrosting": "Afrimning",
        },
        "cs": {
            "config_title": "Qvantum tepelné čerpadlo",
            "powertotal": "Celkový výkon",
            "smart_dhw_control_status": "Stav řízení Smart DHW",
            "hp_status_name": "Stav tepelného čerpadla",
            "hp_status_defrosting": "Odmrazování",
        },
        "fi": {
            "config_title": "Qvantum lämpöpumppu",
            "powertotal": "Kokonaisteho",
            "smart_dhw_control_status": "Älykkään käyttöveden ohjaustila",
            "hp_status_name": "Lämpöpumpun tila",
            "hp_status_defrosting": "Sulatus",
        },
    }

    for locale, expected in expected_strings.items():
        path = TRANSLATIONS_DIR / f"{locale}.json"
        assert path.exists(), f"Missing translation file for {locale}"

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["config"]["title"] == expected["config_title"]
        assert data["entity"]["sensor"]["powertotal"]["name"] == expected["powertotal"]
        assert (
            data["entity"]["sensor"]["smart_dhw_control_status"]["name"]
            == expected["smart_dhw_control_status"]
        )
        assert data["entity"]["sensor"]["hp_status"]["name"] == expected["hp_status_name"]
        assert data["entity"]["sensor"]["hp_status"]["state"]["1"] == expected["hp_status_defrosting"]
        assert "cloud" in data["config"]["step"]["user"]["menu_options"]
        assert "modbus" in data["config"]["step"]["user"]["menu_options"]


def test_runtime_sensor_translations_exist_in_all_locales():
    """Input registers 88-90 have a sensor name in every locale."""
    keys = (
        "compressor_run_time",
        "compressor_starts",
        "ventilation_fan_run_time",
    )
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        sensors = data["entity"]["sensor"]
        for key in keys:
            assert sensors[key]["name"], f"{path.name} missing {key}"


def test_alarm_translations_exist_in_all_locales():
    """Modbus alarm entities have names and device-automation strings."""
    sensor_keys = (
        "active_alarms",
        "alarm_1_code",
        "alarm_2_code",
        "alarm_3_code",
        "alarm_4_code",
        "alarm_5_code",
    )
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        sensors = data["entity"]["sensor"]
        for key in sensor_keys:
            assert sensors[key]["name"], f"{path.name} missing {key}"
        assert data["entity"]["binary_sensor"]["alarm_active"]["name"], path.name
        assert data["device_automation"]["trigger_type"]["alarm_active"], path.name
        assert data["device_automation"]["condition_type"]["is_alarm_active"], path.name


def _entity_translation_keys(node: object, path: str = "entity") -> list[str]:
    """Yield translation-key path segments under entity.<domain>."""
    keys: list[str] = []
    if not isinstance(node, dict):
        return keys
    for key, value in node.items():
        child = f"{path}.{key}"
        if path.count(".") >= 1:
            keys.append(key)
        keys.extend(_entity_translation_keys(value, child))
    return keys


def test_entity_translation_keys_match_hassfest():
    """Entity translation keys must be valid hassfest identifiers."""
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in _entity_translation_keys(data.get("entity", {})):
            assert _TRANSLATION_KEY.match(key), (
                f"{path.name}: invalid translation key {key!r}"
            )


def test_heating_curve_translations_exist_in_all_locales():
    """Heating curve select/number names exist in every locale."""
    keys_number = (
        "temp_compensation_curve",
        "curve_minus_30",
        "curve_minus_20",
        "curve_minus_10",
        "curve_0",
        "curve_10",
        "curve_20",
        "curve_30",
    )
    # Holding 23 is the Auto curve number (datasheet: Temperature
    # compensation curve heating). User-defined points keep a short
    # "Heating curve N: temp" label in Qvantum app order (+30 … -30).
    compensation_names = {
        "en": "Temperature compensation curve",
        "sv": "Temperaturkompensationskurva",
        "de": "Temperaturkompensationskurve",
        "da": "Temperaturkompensationskurve",
        "fi": "Lämpötilakompensaatiokäyrä",
        "fr": "Courbe de compensation de température",
        "es": "Curva de compensación de temperatura",
        "nl": "Temperatuurcompensatiecurve",
        "pl": "Krzywa kompensacji temperatury",
        "cs": "Teplotní kompenzační křivka",
        "hu": "Hőmérséklet-kompenzációs görbe",
    }
    point_bases = {
        "en": "Heating curve",
        "sv": "Värmekurva",
        "de": "Heizkurve",
        "da": "Varmekurve",
        "fi": "Lämmityskäyrä",
        "fr": "Courbe de chauffage",
        "es": "Curva de calefacción",
        "nl": "Stooklijn",
        "pl": "Krzywa grzewcza",
        "cs": "Topná křivka",
        "hu": "Fűtési görbe",
    }
    curve_point_suffixes = {
        "curve_30": " 1: 30°C",
        "curve_20": " 2: 20°C",
        "curve_10": " 3: 10°C",
        "curve_0": " 4: 0°C",
        "curve_minus_10": " 5: -10°C",
        "curve_minus_20": " 6: -20°C",
        "curve_minus_30": " 7: -30°C",
    }
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        locale = path.stem
        select = data["entity"]["select"]["curve_type_heating"]
        assert select["name"], f"{path.name} missing curve_type_heating"
        assert select["state"]["0"], f"{path.name} missing Auto"
        assert select["state"]["1"], f"{path.name} missing User defined"
        numbers = data["entity"]["number"]
        for key in keys_number:
            assert numbers[key]["name"], f"{path.name} missing {key}"
        assert numbers["temp_compensation_curve"]["name"] == compensation_names[locale], (
            f"{path.name} holding 23 name"
        )
        base = point_bases[locale]
        for key, suffix in curve_point_suffixes.items():
            assert numbers[key]["name"] == f"{base}{suffix}", (
                f"{path.name} {key} should be {base}{suffix!r}"
            )


def test_released_translations_mean_permitted():
    """Released flags mean the function is permitted, not that it is running."""
    en = json.loads((TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
    sv = json.loads((TRANSLATIONS_DIR / "sv.json").read_text(encoding="utf-8"))
    de = json.loads((TRANSLATIONS_DIR / "de.json").read_text(encoding="utf-8"))

    assert en["entity"]["binary_sensor"]["heatingreleased"]["name"] == "Heating permitted"
    assert en["entity"]["sensor"]["heatingreleased"]["name"] == "Heating permitted"
    assert sv["entity"]["binary_sensor"]["heatingreleased"]["name"] == "Värme tillåten"
    assert de["entity"]["binary_sensor"]["heatingreleased"]["name"] == "Heizung freigegeben"
    assert en["entity"]["binary_sensor"]["wifi_connected"]["name"] == "Wi-Fi connected"
    assert en["entity"]["binary_sensor"]["cloud_connected"]["name"] == "Cloud connected"


def test_config_flow_strings_are_localized():
    """Non-English locales must keep localized config abort/error/title strings."""
    en = json.loads((TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
    en_abort = en["config"]["abort"]["already_configured"]
    en_error = en["config"]["error"]["cannot_connect"]
    en_user_title = en["config"]["step"]["user"]["title"]

    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        if path.name == "en.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["config"]["abort"]["already_configured"] != en_abort, path.name
        assert data["config"]["error"]["cannot_connect"] != en_error, path.name
        assert data["config"]["step"]["user"]["title"] != en_user_title, path.name
        assert data["config"]["step"]["user"]["menu_options"]["cloud"]
        assert data["config"]["step"]["user"]["menu_options"]["modbus"]
