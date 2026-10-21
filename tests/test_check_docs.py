"""The documentation gate that runs on every pull request."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "check_docs.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_docs = _load()


def test_behaviour_files_count_and_translations_do_not() -> None:
    assert check_docs.is_behaviour("custom_components/qvantum/curve_coordinator.py")
    assert not check_docs.is_behaviour("custom_components/qvantum/translations/sv.json")
    assert not check_docs.is_behaviour("tests/test_curve_coordinator.py")
    assert not check_docs.is_behaviour("docs/heating-curve.md")
    assert not check_docs.is_behaviour(".github/workflows/test.yml")


def test_documentation_paths() -> None:
    assert check_docs.is_documentation("AGENTS.md")
    assert check_docs.is_documentation("README.md")
    assert check_docs.is_documentation("docs/heating-curve.md")
    assert not check_docs.is_documentation("custom_components/qvantum/sensor.py")


def test_behaviour_without_documentation_is_reported() -> None:
    files = [
        "custom_components/qvantum/heating_curve.py",
        "tests/test_heating_curve.py",
    ]

    assert check_docs.missing_documentation(files) == [
        "custom_components/qvantum/heating_curve.py"
    ]


def test_behaviour_with_documentation_passes() -> None:
    files = [
        "custom_components/qvantum/heating_curve.py",
        "docs/heating-curve.md",
    ]

    assert check_docs.missing_documentation(files) == []


def test_translations_and_tests_alone_need_no_documentation() -> None:
    files = [
        "custom_components/qvantum/translations/fi.json",
        "tests/test_sensor.py",
        ".github/workflows/test.yml",
    ]

    assert check_docs.missing_documentation(files) == []


def test_opt_out_label_skips_the_check(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        check_docs,
        "changed_files",
        lambda base, head: ["custom_components/qvantum/sensor.py"],
    )

    args = ["--base", "a", "--head", "b", "--label", "docs-not-needed"]
    assert check_docs.main(args) == 0
    assert "skipped" in capsys.readouterr().out


def test_failure_names_the_files_and_the_label(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        check_docs,
        "changed_files",
        lambda base, head: ["custom_components/qvantum/sensor.py"],
    )

    assert check_docs.main(["--base", "a", "--head", "b"]) == 1
    err = capsys.readouterr().err
    assert "custom_components/qvantum/sensor.py" in err
    assert "docs-not-needed" in err
