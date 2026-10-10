#!/usr/bin/env python3
"""Generate a detailed per-release changelog from merged pull requests.

The output lives in ``docs/releases/<tag>.md`` (one file per release) and is the
source of truth for the link added to the GitHub release body. It is generated:

* on publish — stable and pre-release, never drafts — for the released tag;
* manually, to backfill a historical release.

GitHub access is isolated in :func:`_gh` so every rendering helper stays pure and
unit-testable without network access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DARTER = REPO_ROOT / ".github" / "release-drafter.yml"
DEFAULT_INDEX = REPO_ROOT / "docs" / "releases" / "README.md"

ALL_RELEASES_URL = "https://github.com/{repo}/tree/main/docs/releases"
COMPARE_URL = "https://github.com/{repo}/compare/{base}...{head}"
BLOB_URL = "https://github.com/{repo}/blob/main/docs/releases/{tag}.md"

# Markers around the overview inserted into the GitHub release body, so re-runs
# can replace it instead of appending.
OVERVIEW_MARKER_START = "<!-- changelog-overview:start -->"
OVERVIEW_MARKER_END = "<!-- changelog-overview:end -->"

NEW_FEATURES = "New features"
BUG_FIXES = "Bug fixes"

INTERNAL_NOTE = (
    "_Internal changes (refactor / tests / CI / docs), not described in detail: {numbers}._"
)

INDEX_HEADER = """\
# Release notes

One file per release, named after the tag. Each file expands every merged PR
into a short paragraph grouped by type, and is the source of truth for the
link added to the GitHub release body.

| Version | Released | Highlights |
| --- | --- | --- |
"""

# Category fallback when a PR carries no release-drafter label.
_TITLE_PREFIX_CATEGORY = {
    "feat": NEW_FEATURES,
    "feature": NEW_FEATURES,
    "perf": NEW_FEATURES,
    "fix": BUG_FIXES,
}

_TITLE_PREFIX_RE = re.compile(
    r"^(feat|fix|perf|chore|docs|refactor|test|ci)(\([^)]*\))?:\s*", re.IGNORECASE
)
_SUMMARY_RE = re.compile(r"^##+\s*Summary\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^##+\s", re.MULTILINE)
_META_RE = re.compile(r"^_Released\b")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")

# End-user polish of the overview paragraph (one-shot OpenRouter call, gpt-luna).
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
POLISH_MODEL = "~openai/gpt-luna-latest"
POLISH_MAX_TOKENS = 4096
POLISH_TIMEOUT = 120
POLISH_PROMPT = (
    "Below is a generated release-notes document for a Home Assistant integration for "
    "Qvantum heat pumps. Rewrite ONLY the overview paragraph (the first paragraph after "
    'the metadata line, starting with "Highlights:") into a short, friendly, '
    "non-technical introduction for end users: 2-4 sentences, plain language. Summarize "
    "the main themes of the new features AND of the bug fixes in the document; keep "
    "every fact, do not invent anything and do not drop a major theme. State plainly "
    "what changed; avoid marketing words. Do not mention pull requests, registers, "
    "code, Modbus or cloud. Output only the new paragraph, as a single paragraph."
)


@dataclass(frozen=True)
class PullRequest:
    """The subset of a merged PR the changelog needs."""

    number: int
    title: str
    body: str
    url: str
    merged_at: str
    labels: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# GitHub access
# --------------------------------------------------------------------------- #
def _gh(args: list[str]) -> str:
    """Run a ``gh`` command and return stdout, raising on failure."""
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def list_releases(repo: str) -> list[dict]:
    """Return every release (including drafts) known to GitHub."""
    out = _gh(
        [
            "release",
            "list",
            "--repo",
            repo,
            "--limit",
            "100",
            "--json",
            "tagName,publishedAt,isPrerelease,isDraft",
        ]
    )
    return json.loads(out)


def release_info(repo: str, tag: str) -> dict | None:
    """Return release metadata for ``tag``, or ``None`` if it is not released."""
    try:
        out = _gh(
            [
                "release",
                "view",
                tag,
                "--repo",
                repo,
                "--json",
                "tagName,publishedAt,isPrerelease,isDraft",
            ]
        )
    except subprocess.CalledProcessError:
        return None
    return json.loads(out)


def previous_stable_release(repo: str, before_iso: str | None = None) -> dict | None:
    """Return the newest stable (non-draft, non-prerelease) release before a date.

    Mirrors release-drafter's ``include-pre-releases: false``: pre-releases do not
    reset the change window, so a stable release summarizes everything since the
    previous stable release.
    """
    candidates = []
    for release in list_releases(repo):
        if release.get("isDraft") or release.get("isPrerelease"):
            continue
        published = release.get("publishedAt") or ""
        if not published or published.startswith("0001-"):
            continue
        if before_iso and published >= before_iso:
            continue
        candidates.append(release)
    if not candidates:
        return None
    return max(candidates, key=lambda release: release["publishedAt"])


def release_body(repo: str, tag: str) -> str | None:
    """Return the raw body of a release (published or draft), or ``None``."""
    try:
        return _gh(["release", "view", tag, "--repo", repo, "--json", "body", "-q", ".body"])
    except subprocess.CalledProcessError:
        return None


_PR_LINE_RE = re.compile(r"^-\s+#(\d+)\b", re.MULTILINE)


def parse_release_pr_numbers(body: str | None) -> list[int]:
    """Pull PR numbers out of a release-drafter body (``- #N title @author``).

    Using the release body as the source of truth keeps the changelog in sync
    with what release-drafter actually shipped, including PRs that reached
    ``main`` through a stacked branch rather than ``--base main``.
    """
    if not body:
        return []
    numbers: list[int] = []
    for match in _PR_LINE_RE.finditer(body):
        number = int(match.group(1))
        if number not in numbers:
            numbers.append(number)
    return numbers


def fetch_pr(repo: str, number: int) -> PullRequest | None:
    """Fetch the PR details the changelog needs, or ``None`` if it is gone."""
    try:
        out = _gh(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                "number,title,body,labels,mergedAt,url",
            ]
        )
    except subprocess.CalledProcessError:
        return None
    item = json.loads(out)
    return PullRequest(
        number=item["number"],
        title=item["title"],
        body=item.get("body") or "",
        url=item.get("url") or f"https://github.com/{repo}/pull/{item['number']}",
        merged_at=item.get("mergedAt") or "",
        labels=tuple(label["name"] for label in item.get("labels", [])),
    )


def collect_release_prs(repo: str, tag: str) -> list[PullRequest]:
    """Resolve the PRs listed in a release/draft body into full records."""
    numbers = parse_release_pr_numbers(release_body(repo, tag))
    pull_requests = [pr for pr in (fetch_pr(repo, n) for n in numbers) if pr is not None]
    pull_requests.sort(key=lambda pr: pr.number)
    return pull_requests


def _release_notes_link(repo: str, tag: str) -> str:
    return (
        f"📄 **Full release notes:** "
        f"[docs/releases/{tag}.md]({BLOB_URL.format(repo=repo, tag=tag)})"
    )


def _overview_in_body(body: str) -> str:
    """Return the overview currently wrapped in markers, or an empty string."""
    match = re.search(
        re.escape(OVERVIEW_MARKER_START) + r"\n(.*?)\n" + re.escape(OVERVIEW_MARKER_END),
        body,
        re.DOTALL,
    )
    return match.group(1) if match else ""


def _strip_inserted_notes(body: str) -> str:
    """Remove the marker block and the changelog link line, collapsing blank runs."""
    lines: list[str] = []
    inside = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == OVERVIEW_MARKER_START:
            inside = True
            continue
        if stripped == OVERVIEW_MARKER_END:
            inside = False
            continue
        if inside or "Full release notes:" in line:
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _write_release_body(repo: str, tag: str, body: str) -> None:
    body = body if body.endswith("\n") else body + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(body)
        path = handle.name
    try:
        _gh(["release", "edit", tag, "--repo", repo, "--notes-file", path])
    finally:
        os.unlink(path)


def update_release_notes(repo: str, tag: str, overview: str | None = None) -> bool:
    """Insert the overview and changelog link into a published release body.

    Idempotent: a marker block replaces any previously inserted overview, and the
    link line is rewritten. Called at publish time (after the file is pushed) and by
    the manual backfill.
    """
    body = release_body(repo, tag)
    if body is None:
        return False
    link = _release_notes_link(repo, tag)
    if link in body and _overview_in_body(body) == (overview or ""):
        return True

    insert: list[str] = []
    if overview:
        insert.extend([OVERVIEW_MARKER_START, overview, OVERVIEW_MARKER_END, ""])
    insert.append(link)

    result: list[str] = []
    inserted = False
    for line in _strip_inserted_notes(body).splitlines():
        result.append(line)
        if not inserted and line.startswith("# "):
            result.extend(["", *insert])
            inserted = True
    if not inserted:
        result = [*insert, "", *result]
    _write_release_body(repo, tag, "\n".join(result).strip())
    return True


def remove_release_notes(repo: str, tag: str) -> bool:
    """Remove the inserted overview and link from a release body (when pruning)."""
    body = release_body(repo, tag)
    if body is None:
        return False
    cleaned = _strip_inserted_notes(body)
    if cleaned == body.strip():
        return False
    _write_release_body(repo, tag, cleaned)
    return True


def prune_superseded_prereleases(repo: str, releases_dir: Path, index_path: Path) -> list[str]:
    """Keep only the newest pre-release file; drop older, superseded ones.

    A pre-release and the stable that follows it share the same PR set (the
    release-drafter window only resets on stable releases), so keeping every
    pre-release would duplicate the notes. The next release drops them.
    """
    published = [
        release
        for release in list_releases(repo)
        if not release.get("isDraft")
        and (release.get("publishedAt") or "").startswith("2")
    ]
    if not published:
        return []
    latest_tag = max(published, key=lambda release: release["publishedAt"])["tagName"]
    by_tag = {release["tagName"]: release for release in published}

    removed = []
    for path in sorted(Path(releases_dir).glob("*.md")):
        if path.name == "README.md":
            continue
        release = by_tag.get(path.stem)
        if not release or not release.get("isPrerelease") or path.stem == latest_tag:
            continue
        path.unlink()
        prune_index_row(index_path, path.stem)
        remove_release_notes(repo, path.stem)
        removed.append(path.stem)
    return removed


# --------------------------------------------------------------------------- #
# Rendering helpers (pure)
# --------------------------------------------------------------------------- #
def parse_summary(body: str) -> str | None:
    """Extract the text of a PR's ``## Summary`` section, if present."""
    if not body:
        return None
    match = _SUMMARY_RE.search(body)
    if not match:
        return None
    rest = body[match.end() :]
    next_heading = _NEXT_HEADING_RE.search(rest)
    section = rest[: next_heading.start()] if next_heading else rest
    section = section.strip()
    return section or None


def summary_to_prose(summary: str) -> str:
    """Turn a bulleted PR summary into a prose paragraph (nested bullets kept).

    PR bodies are usually hard-wrapped, so a line without a bullet marker is a
    continuation of the previous item (the previous paragraph line or the last
    nested bullet) and must be joined with a space, not treated as new text.
    """
    paragraph: list[str] = []
    nested: list[str] = []
    last_target: list[str] | None = None

    def _continue(target: list[str], text: str) -> None:
        if target:
            target[-1] = f"{target[-1]} {text}"
        else:
            target.append(text)

    for raw in summary.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        bullet = _BULLET_RE.match(text)
        if bullet:
            text = bullet.group(1).strip()
            if indent > 0:
                nested.append(text)
                last_target = nested
            else:
                paragraph.append(text)
                last_target = paragraph
            continue
        _continue(last_target if last_target is not None else paragraph, text)

    if not paragraph and nested:
        paragraph, nested = nested, []
    sentences = []
    for item in paragraph:
        item = item.strip()
        if item and item[-1] not in ".!?:;":
            item += "."
        sentences.append(item)
    prose = re.sub(r"\s+", " ", " ".join(sentences)).strip()
    if nested:
        prose += "\n\n" + "\n".join(f"- {item}" for item in nested)
    return prose


def display_title(title: str) -> str:
    """Strip the conventional-commit prefix for a readable heading."""
    return _TITLE_PREFIX_RE.sub("", title, count=1).strip() or title


def category_for(pull_request: PullRequest) -> str | None:
    """Map a PR to a changelog category, or ``None`` for internal changes."""
    labels = {label.lower() for label in pull_request.labels}
    if "bug" in labels:
        return BUG_FIXES
    if "enhancement" in labels:
        return NEW_FEATURES
    match = _TITLE_PREFIX_RE.match(pull_request.title)
    prefix = match.group(1).lower() if match else ""
    return _TITLE_PREFIX_CATEGORY.get(prefix)


def extract_callouts(path: Path = RELEASE_DARTER) -> str:
    """Read the IMPORTANT/WARNING callouts from release-drafter.yml (single source)."""
    text = Path(path).read_text(encoding="utf-8")
    match = re.search(r"^template: \|\n(.*?)\ninclude-labels:", text, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    lines = [
        line[2:] if line.startswith("  ") else line
        for line in match.group(1).splitlines()
    ]
    result: list[str] = []
    started = False
    for line in lines:
        if line.startswith("> [!"):
            started = True
        if started:
            result.append(line)
    return "\n".join(result).strip()


def existing_overview(path: Path) -> str | None:
    """Reuse a hand-written overview paragraph so regeneration preserves it."""
    path = Path(path)
    if not path.exists():
        return None
    collected: list[str] = []
    started = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not started:
            if _META_RE.match(line):
                started = True
            continue
        if line.startswith("> [") or line.startswith("## ") or line.startswith("---"):
            break
        collected.append(line)
    return "\n".join(collected).strip() or None


def generated_overview(features: list[PullRequest]) -> str:
    """Compose a fallback overview from the top feature titles."""
    if not features:
        return "Maintenance release."
    titles = [display_title(pr.title) for pr in features[:3]]
    text = "Highlights: " + ", ".join(titles) + "."
    if len(features) > 3:
        text += f" Plus {len(features) - 3} more."
    return text


def polish_overview(
    text: str,
    *,
    context: str | None = None,
    model: str = POLISH_MODEL,
    timeout: int = POLISH_TIMEOUT,
) -> str | None:
    """Rewrite the overview paragraph for end users via the OpenRouter API.

    ``context`` (the rendered release with the deterministic overview) is sent so the
    model can keep the facts; only ``text`` is expected back. Returns the rewritten
    paragraph, or ``None`` when ``OPENROUTER_API_KEY`` is unset or the request fails,
    so the caller keeps the deterministic overview. The caller persists the result in
    the file, where :func:`existing_overview` preserves it on later regenerations.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": f"{POLISH_PROMPT}\n\n---\n\n{context or text}"}
        ],
        "max_tokens": POLISH_MAX_TOKENS,
        "temperature": 0.4,
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") == "length":
            raise ValueError("response truncated (finish_reason=length)")
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
        print(f"Overview polish skipped: {error}", file=sys.stderr)
        return None
    return (content or "").strip() or None


def format_date(iso: str | None) -> str | None:
    """Return ``YYYY-MM-DD`` for a GitHub timestamp, or ``None`` if unavailable."""
    if not iso or iso.startswith("0001-"):
        return None
    return iso[:10]


def render_release(
    *,
    repo: str,
    tag: str,
    previous_tag: str,
    published_at: str | None,
    pull_requests: list[PullRequest],
    callouts: str,
    overview: str | None,
) -> str:
    """Render the full markdown document for one release."""
    features = [pr for pr in pull_requests if category_for(pr) == NEW_FEATURES]
    fixes = [pr for pr in pull_requests if category_for(pr) == BUG_FIXES]
    internal = [pr for pr in pull_requests if category_for(pr) is None]

    date = format_date(published_at)
    comparison = COMPARE_URL.format(repo=repo, base=previous_tag, head=tag)
    metadata = (
        f"_Released {date} · [compare {previous_tag}…{tag}]({comparison})"
        f" · [all releases]({ALL_RELEASES_URL.format(repo=repo)})_"
    )

    parts = [f"# {tag}", "", metadata, ""]
    if overview:
        parts.extend([overview, ""])
    if callouts:
        parts.extend([callouts, ""])

    for heading, items in ((NEW_FEATURES, features), (BUG_FIXES, fixes)):
        if not items:
            continue
        parts.extend([f"## {heading}", ""])
        for pull_request in items:
            parts.extend(
                [
                    f"### {display_title(pull_request.title)}"
                    f" ([#{pull_request.number}]({pull_request.url}))",
                    "",
                    summary_to_prose(parse_summary(pull_request.body) or pull_request.title),
                    "",
                ]
            )

    if internal:
        numbers = ", ".join(f"#{pr.number}" for pr in internal)
        parts.extend(["---", "", INTERNAL_NOTE.format(numbers=numbers), ""])

    return "\n".join(parts).rstrip() + "\n"


def highlights_for(pull_requests: list[PullRequest]) -> str:
    """One-line highlight for the index table."""
    for category in (NEW_FEATURES, BUG_FIXES):
        for pull_request in pull_requests:
            if category_for(pull_request) == category:
                return display_title(pull_request.title)
    return "Maintenance"


_INDEX_ROW_RE = re.compile(
    r"^\|\s*\[(.*?)\]\(.*?\)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|"
)


def _read_index_rows(index_path: Path) -> list[tuple[str, str, str]]:
    index_path = Path(index_path)
    if not index_path.exists():
        return []
    rows = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        match = _INDEX_ROW_RE.match(line)
        if match:
            rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


def _write_index_rows(index_path: Path, rows: list[tuple[str, str, str]]) -> None:
    rows = sorted(rows, key=lambda row: row[1], reverse=True)
    lines = [INDEX_HEADER.rstrip("\n")]
    lines.extend(f"| [{row[0]}]({row[0]}.md) | {row[1]} | {row[2]} |" for row in rows)
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_index(
    index_path: Path, tag: str, published_at: str | None, highlights: str
) -> None:
    """Insert or replace the tag's row, newest first, idempotently."""
    rows = [row for row in _read_index_rows(index_path) if row[0] != tag]
    rows.append((tag, format_date(published_at) or "", highlights))
    _write_index_rows(index_path, rows)


def prune_index_row(index_path: Path, tag: str) -> None:
    """Drop a tag's row from the index (used when pruning superseded files)."""
    rows = [row for row in _read_index_rows(index_path) if row[0] != tag]
    _write_index_rows(index_path, rows)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "perosb/qvantum_custom_component"),
    )
    parser.add_argument("--tag", required=True, help="Release tag/version.")
    parser.add_argument("--previous-tag", help="Override the previous stable tag.")
    parser.add_argument("--out", help="Output file (default docs/releases/<tag>.md).")
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument(
        "--update-link",
        action="store_true",
        help="Insert the changelog link into the published release body.",
    )
    parser.add_argument(
        "--link-only",
        action="store_true",
        help="Only insert the changelog link; do not regenerate the file.",
    )
    parser.add_argument(
        "--polish",
        action="store_true",
        help="Rewrite the overview for end users via OpenRouter (falls back if unavailable).",
    )
    parser.add_argument(
        "--repolish",
        action="store_true",
        help="Like --polish but ignore and replace an existing overview.",
    )
    parser.add_argument("--polish-model", default=POLISH_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo
    tag = args.tag
    out = Path(args.out) if args.out else REPO_ROOT / "docs" / "releases" / f"{tag}.md"

    info = release_info(repo, tag)
    published_at = None if info is None else info.get("publishedAt")
    if published_at and published_at.startswith("0001-"):
        published_at = None

    if args.link_only:
        if info is None:
            print(f"Release {tag} not found.", file=sys.stderr)
            return 1
        update_release_notes(repo, tag, existing_overview(out))
        return 0

    if args.previous_tag:
        previous = {"tagName": args.previous_tag, "publishedAt": None}
    else:
        previous = previous_stable_release(repo, before_iso=published_at)
    if previous is None:
        print("No previous stable release found; nothing to do.", file=sys.stderr)
        return 1

    pull_requests = collect_release_prs(repo, tag)
    if not pull_requests:
        print(f"No PRs listed for {tag}; skipping.")
        return 0

    features = [pr for pr in pull_requests if category_for(pr) == NEW_FEATURES]
    callouts = extract_callouts()
    overview = None if args.repolish else existing_overview(out)
    if overview is None:
        overview = generated_overview(features)
        if args.polish or args.repolish:
            preview = render_release(
                repo=repo,
                tag=tag,
                previous_tag=previous["tagName"],
                published_at=published_at,
                pull_requests=pull_requests,
                callouts=callouts,
                overview=overview,
            )
            overview = (
                polish_overview(
                    overview, context=preview, model=args.polish_model
                )
                or overview
            )
    content = render_release(
        repo=repo,
        tag=tag,
        previous_tag=previous["tagName"],
        published_at=published_at,
        pull_requests=pull_requests,
        callouts=callouts,
        overview=overview,
    )

    if args.dry_run:
        print(content)
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    update_index(Path(args.index), tag, published_at, highlights_for(pull_requests))
    prune_superseded_prereleases(repo, out.parent, Path(args.index))

    if args.update_link and info is not None:
        update_release_notes(repo, tag, overview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
