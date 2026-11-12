#!/usr/bin/env python3
"""Render the per-pull-request coverage figure as Markdown for the run summary.

Replaces the external Codecov app. `diff-cover` reads the same ``coverage.xml``
the suite already writes and reports only the lines this diff touches — the
figure Codecov called *patch* coverage. The project floor (92 %) is enforced by
``pytest`` itself (``--cov-fail-under``); this adds the per-PR figure, a floor of
its own, and nothing that can hang: no upload, no app, no token.

Usage (the workflow appends the output to ``$GITHUB_STEP_SUMMARY``):

    coverage_summary.py --json patch.json --floor 80 [--project-report report.txt]

Exits non-zero when the patch coverage is below the floor, so the job fails on a
real number rather than on a stalled external service.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Files listed by name; a large refactor would otherwise push the totals off
#: the summary and say nothing about where the misses are.
MAX_FILES = 8


def load(path: Path) -> dict:
    """The ``diff-cover --format json`` payload, or an empty one."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def render(patch: dict, floor: float) -> tuple[str, bool]:
    """Markdown for the patch figure, and whether it clears the floor."""
    total = int(patch.get("total_num_lines") or 0)
    missing = int(patch.get("total_num_violations") or 0)
    percent = float(patch.get("total_percent_covered") or 0.0)
    diff_name = patch.get("diff_name")

    lines: list[str] = ["### Changed lines (patch)", ""]
    if diff_name:
        lines.append(f"Against `{diff_name}`.")
        lines.append("")
    if total == 0:
        lines.append(
            "This diff touches no Python lines, so there is nothing to measure."
        )
        return "\n".join(lines) + "\n", True

    lines.append("| | |")
    lines.append("|---|---:|")
    lines.append(f"| Patch coverage | {percent:.1f} % (floor {floor:.0f} %) |")
    lines.append(f"| Changed lines | {total} |")
    lines.append(f"| Uncovered | {missing} |")
    lines.append("")

    stats = patch.get("src_stats") or {}
    rows = []
    for name, entry in stats.items():
        changed = len(entry.get("covered_lines") or ()) + len(
            entry.get("violation_lines") or ()
        )
        uncovered = len(entry.get("violation_lines") or ())
        rows.append((float(entry.get("percent_covered") or 0.0), name, changed, uncovered))
    rows.sort(key=lambda row: (row[0], -row[3], row[1]))
    if rows:
        lines.append("| file | changed | uncovered | coverage |")
        lines.append("|---|---:|---:|---:|")
        for coverage, name, changed, uncovered in rows[:MAX_FILES]:
            short = name.rsplit("/", 1)[-1]
            lines.append(
                f"| `{short}` | {changed} | {uncovered} | {coverage:.1f} % |"
            )
        if len(rows) > MAX_FILES:
            lines.append(f"| … {len(rows) - MAX_FILES} more | | | |")
        lines.append("")
    return "\n".join(lines), percent + 1e-9 >= floor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, type=Path, help="diff-cover JSON")
    parser.add_argument("--floor", type=float, default=80.0)
    parser.add_argument(
        "--project-report",
        type=Path,
        help="Text of `coverage report`, appended in a fenced block",
    )
    args = parser.parse_args(argv)

    markdown, ok = render(load(args.json), args.floor)
    parts = [markdown]
    if args.project_report is not None:
        try:
            report = args.project_report.read_text().rstrip()
        except OSError:
            report = ""
        if report:
            parts.append("### Project\n\n```\n" + report + "\n```\n")
    sys.stdout.write("\n".join(parts))
    if not ok:
        sys.stderr.write(
            f"patch coverage {args.floor:.0f} % floor not met\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
