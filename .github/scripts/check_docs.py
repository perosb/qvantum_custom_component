#!/usr/bin/env python3
"""Fail a PR that changes behaviour without touching the documentation.

The integration is documented in two places and both are loaded by whoever (or
whatever) works on the repository next: ``AGENTS.md`` is injected as the
authoritative note, and ``docs/`` explains the behaviour to users and to future
maintainers. A change that alters behaviour and leaves both untouched is how the
note drifts out of date, which is worse than no note at all.

The rule is deliberately coarse — it only asks whether *any* documentation
moved — because the honest judgement ("does this behaviour change need prose?")
belongs to the author. A pull request that genuinely needs no prose opts out with
the ``docs-not-needed`` label, which keeps the check deterministic and the
decision explicit.

Usage (CI):

    python3 .github/scripts/check_docs.py --base <sha> --head <sha> \
        --label docs-not-needed [--label another]
    python3 .github/scripts/check_docs.py --base <sha> --head <sha> \
        --labels-json '["bug","docs-not-needed"]'

or, locally, from a branch:

    python3 .github/scripts/check_docs.py --base origin/main --head HEAD
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

#: Directories whose changes are behaviour: the integration itself. Tests,
#: translations, docs, releases, the CI helpers and the workflows are excluded —
#: a test-only, tooling or docs-only PR needs no further prose.
BEHAVIOUR_PREFIXES = (
    "custom_components/qvantum/",
)
#: Behaviour files inside those prefixes that are prose already.
NON_BEHAVIOUR_SUFFIXES = ("/translations/",)

#: A change to any of these satisfies the check.
DOC_PATHS = ("AGENTS.md", "README.md")
DOC_PREFIXES = ("docs/",)

#: The label that turns the check off for a pull request.
OPT_OUT_LABEL = "docs-not-needed"


def changed_files(base: str, head: str) -> list[str]:
    """Paths changed between ``base`` and ``head`` (three-dot diff)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def is_behaviour(path: str) -> bool:
    if not path.startswith(BEHAVIOUR_PREFIXES):
        return False
    return not any(marker in path for marker in NON_BEHAVIOUR_SUFFIXES)


def is_documentation(path: str) -> bool:
    return path in DOC_PATHS or path.startswith(DOC_PREFIXES)


def missing_documentation(files: list[str]) -> list[str]:
    """Behaviour files in the diff when no documentation moved with them."""
    behaviour = [path for path in files if is_behaviour(path)]
    if not behaviour:
        return []
    if any(is_documentation(path) for path in files):
        return []
    return behaviour


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base ref or sha")
    parser.add_argument("--head", required=True, help="head ref or sha")
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="pull-request label (may repeat); the opt-out label skips the check",
    )
    parser.add_argument(
        "--labels-json",
        default="",
        help=(
            "the pull request's labels as a JSON array, as the workflow hands "
            "them over; merged with --label"
        ),
    )
    args = parser.parse_args(argv)

    labels = list(args.label)
    if args.labels_json.strip():
        try:
            parsed = json.loads(args.labels_json)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            labels.extend(str(item) for item in parsed)

    if OPT_OUT_LABEL in labels:
        print(f"Documentation check skipped: the {OPT_OUT_LABEL!r} label is set.")
        return 0

    files = changed_files(args.base, args.head)
    missing = missing_documentation(files)
    if not missing:
        print("Documentation check passed.")
        return 0

    print(
        "Documentation check failed: this pull request changes behaviour but "
        "neither AGENTS.md nor docs/ moved with it.",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print("Changed behaviour files:", file=sys.stderr)
    for path in missing:
        print(f"  - {path}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "Update AGENTS.md when architecture, derived-metric semantics, "
        "persistence or this workflow changes, and docs/ when user-visible "
        "behaviour changes. If the change genuinely needs no prose, add the "
        f"{OPT_OUT_LABEL!r} label to the pull request.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
