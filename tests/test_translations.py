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
        },
        "cs": {
            "config_title": "Qvantum tepelné čerpadlo",
            "powertotal": "Celkový výkon",
        },
    }

    for locale, expected in expected_strings.items():
        path = TRANSLATIONS_DIR / f"{locale}.json"
        assert path.exists(), f"Missing translation file for {locale}"

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["config"]["title"] == expected["config_title"]
        assert data["entity"]["sensor"]["powertotal"]["name"] == expected["powertotal"]
