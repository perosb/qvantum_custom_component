import json
from pathlib import Path


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
            "config_description": "Modbus giver dig næsten realtidsmålinger ved direkte læsning fra enheden.",
        },
        "cs": {
            "config_title": "Qvantum tepelné čerpadlo",
            "powertotal": "Celkový výkon",
            "smart_dhw_control_status": "Stav řízení Smart DHW",
            "hp_status_name": "Stav tepelného čerpadla",
            "hp_status_defrosting": "Odmrazování",
            "config_description": "Modbus vám poskytne téměř okamžitá měření přímo čtením ze zařízení.",
        },
        "fi": {
            "config_title": "Qvantum lämpöpumppu",
            "powertotal": "Kokonaisteho",
            "smart_dhw_control_status": "Älykkään käyttöveden ohjaustila",
            "hp_status_name": "Lämpöpumpun tila",
            "hp_status_defrosting": "Sulatus",
            "config_description": "Modbus antaa sinulle lähes reaaliaikaisia mittauksia lukemalla laitetta suoraan.",
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
        assert expected["config_description"] in data["config"]["step"]["user"]["description"]
