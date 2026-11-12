"""Unit tests for the per-pull-request coverage summary.

CI tooling, loaded by path like the other scripts under `.github/scripts`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "scripts"
    / "coverage_summary.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("coverage_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def summary():
    return _load_module()


def _payload(total=59, missing=2, percent=96.0, stats=None):
    return {
        "diff_name": "abc123...HEAD",
        "total_num_lines": total,
        "total_num_violations": missing,
        "total_percent_covered": percent,
        "src_stats": stats
        if stats is not None
        else {
            "custom_components/qvantum/a.py": {
                "percent_covered": 95.9,
                "covered_lines": list(range(100, 148)),
                "violation_lines": [101, 102],
            },
            "custom_components/qvantum/b.py": {
                "percent_covered": 100.0,
                "covered_lines": list(range(200, 210)),
                "violation_lines": [],
            },
        },
    }


def test_render_reports_totals_and_the_worst_files_first(summary):
    markdown, ok = summary.render(_payload(), 80.0)

    assert ok
    assert "Patch coverage | 96.0 % (floor 80 %)" in markdown
    assert "Changed lines | 59" in markdown
    assert "Uncovered | 2" in markdown
    # The file with a miss is listed before the clean one.
    assert markdown.index("`a.py`") < markdown.index("`b.py`")
    assert "| `a.py` | 50 | 2 | 95.9 % |" in markdown


def test_render_fails_below_the_floor(summary):
    markdown, ok = summary.render(_payload(percent=79.9), 80.0)

    assert not ok
    assert "79.9 % (floor 80 %)" in markdown


def test_a_diff_without_python_lines_has_nothing_to_measure(summary):
    markdown, ok = summary.render(
        {"total_num_lines": 0, "total_num_violations": 0, "src_stats": {}}, 80.0
    )

    assert ok
    assert "touches no Python lines" in markdown


def test_an_unreadable_report_is_not_a_failure(summary, tmp_path):
    """A broken upload must not fail the job — and must not invent numbers."""
    markdown, ok = summary.render(summary.load(tmp_path / "missing.json"), 80.0)

    assert ok
    assert "touches no Python lines" in markdown


def test_the_file_list_is_capped(summary):
    stats = {
        f"custom_components/qvantum/f{index}.py": {
            "percent_covered": 50.0,
            "covered_lines": [1],
            "violation_lines": [2],
        }
        for index in range(summary.MAX_FILES + 3)
    }

    markdown, _ok = summary.render(_payload(stats=stats), 80.0)

    assert f"… {3} more" in markdown
    assert markdown.count("custom_components") == 0  # basenames only


def test_main_writes_the_summary_and_exits_on_the_floor(summary, tmp_path, capsys):
    patch = tmp_path / "patch.json"
    patch.write_text(json.dumps(_payload(percent=70.0)))
    report = tmp_path / "project.txt"
    report.write_text("TOTAL  9400  430  95%\n")

    status = summary.main(
        ["--json", str(patch), "--floor", "80", "--project-report", str(report)]
    )

    out = capsys.readouterr()
    assert status == 1
    assert "patch coverage 80 % floor not met" in out.err
    assert "### Project" in out.out
    assert "TOTAL  9400  430  95%" in out.out
