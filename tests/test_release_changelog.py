"""Unit tests for the per-release changelog generator.

The script is CI tooling rather than integration code, so it is loaded by path
instead of imported as a package. All GitHub calls are monkeypatched.
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
    / "generate_release_changelog.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_release_changelog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec so dataclass annotation resolution can find the module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def changelog():
    return _load_module()


def _pr(module, number=1, title="feat: Add thing", labels=("enhancement",), body="## Summary\n- Adds a thing.\n"):
    return module.PullRequest(
        number=number,
        title=title,
        body=body,
        url=f"https://github.com/perosb/qvantum_custom_component/pull/{number}",
        merged_at="2026-09-25T10:00:00Z",
        labels=labels,
    )


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_parse_summary_extracts_section(changelog):
    body = "## Summary\n- one\n- two\n\n## Test plan\n- [x] done\n"
    assert changelog.parse_summary(body) == "- one\n- two"


def test_parse_summary_handles_missing_and_eof(changelog):
    assert changelog.parse_summary("") is None
    assert changelog.parse_summary("no summary here") is None
    assert changelog.parse_summary("## Summary\nonly line") == "only line"


def test_parse_summary_normalizes_crlf(changelog):
    assert changelog.parse_summary("## Summary\r\n- a\r\n\r\n## Test plan\r\n") == "- a"


def test_summary_to_prose_joins_top_level_bullets(changelog):
    prose = changelog.summary_to_prose("- first thing\n- second thing")
    assert prose == "first thing. second thing."


def test_summary_to_prose_keeps_nested_bullets(changelog):
    prose = changelog.summary_to_prose("- lead\n  - nested one\n  - nested two")
    assert prose == "lead.\n\n- nested one\n- nested two"


def test_summary_to_prose_does_not_double_period(changelog):
    assert changelog.summary_to_prose("- already done.").endswith("done.")


def test_summary_to_prose_promotes_nested_when_no_lead(changelog):
    assert changelog.summary_to_prose("  - only nested") == "only nested."


def test_summary_to_prose_joins_hard_wrapped_continuations(changelog):
    summary = (
        "Reduces the hardcoding by deriving the\n"
        "remaining knobs from weather data.\n\n"
        "- **Baseline** While\n"
        "  the pump is on Auto, it corrects the table.\n"
        "- **Night term** scales with the\n"
        "  forecast's diurnal swing."
    )
    assert changelog.summary_to_prose(summary) == (
        "Reduces the hardcoding by deriving the remaining knobs from weather data. "
        "**Baseline** While the pump is on Auto, it corrects the table. "
        "**Night term** scales with the forecast's diurnal swing."
    )


def test_summary_to_prose_joins_nested_continuations(changelog):
    summary = "- lead\n  - nested line\n    continues here\n  - second nested"
    assert changelog.summary_to_prose(summary) == (
        "lead.\n\n- nested line continues here\n- second nested"
    )


def test_summary_to_prose_top_level_continuation_after_nested(changelog):
    summary = "- a\n  - n1\n- b\n  continued b"
    # The continuation belongs to the top-level bullet, not the last nested one.
    assert changelog.summary_to_prose(summary) == "a. b continued b.\n\n- n1"


def test_display_title_strips_conventional_prefix(changelog):
    assert changelog.display_title("feat(scope): Add thing") == "Add thing"
    assert changelog.display_title("fix: Fix thing") == "Fix thing"
    assert changelog.display_title("Something else") == "Something else"


def test_category_for_prefers_labels(changelog):
    assert changelog.category_for(_pr(changelog, labels=("bug",))) == changelog.BUG_FIXES
    assert (
        changelog.category_for(_pr(changelog, labels=("enhancement",)))
        == changelog.NEW_FEATURES
    )
    assert changelog.category_for(_pr(changelog, labels=(), title="Something else")) is None


def test_category_for_falls_back_to_title_prefix(changelog):
    assert (
        changelog.category_for(_pr(changelog, title="fix: x", labels=()))
        == changelog.BUG_FIXES
    )
    assert (
        changelog.category_for(_pr(changelog, title="perf: x", labels=()))
        == changelog.NEW_FEATURES
    )
    # Scoped conventional titles must not fall through to the internal line.
    assert (
        changelog.category_for(_pr(changelog, title="feat(modbus): x", labels=(("chore"),)))
        == changelog.NEW_FEATURES
    )
    assert (
        changelog.category_for(_pr(changelog, title="fix(curve): x", labels=("docs",)))
        == changelog.BUG_FIXES
    )
    assert changelog.category_for(_pr(changelog, title="chore: x", labels=())) is None


def test_parse_release_pr_numbers(changelog):
    body = "- #233 feat: Add x @a\n- #234 feat: Add y @a\n- #233 feat: Add x @a\n* #9 no\n"
    assert changelog.parse_release_pr_numbers(body) == [233, 234]
    assert changelog.parse_release_pr_numbers(None) == []


def test_extract_callouts_reads_release_drafter(changelog):
    callouts = changelog.extract_callouts()
    assert "> [!IMPORTANT]" in callouts
    assert "> [!WARNING]" in callouts
    assert "SmartControl stay cloud-only" in callouts
    # The template heading and change placeholder are not part of the callouts.
    assert "# What's Changed" not in callouts
    assert "$CHANGES" not in callouts


def test_format_date(changelog):
    assert changelog.format_date("2026-10-01T08:54:24Z") == "2026-10-01"
    assert changelog.format_date(None) is None
    assert changelog.format_date("0001-01-01T00:00:00Z") is None


def test_generated_overview(changelog):
    assert changelog.generated_overview([]) == "Maintenance release."
    one = [ _pr(changelog) ]
    assert changelog.generated_overview(one) == "Highlights: Add thing."
    many = [_pr(changelog, number=i, title=f"feat: Feature {i}") for i in range(1, 6)]
    assert changelog.generated_overview(many).endswith("Plus 2 more.")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_render_release_released(changelog, tmp_path):
    prs = [
        _pr(changelog, number=2, title="fix: Fix thing", labels=("bug",), body="## Summary\n- Fixes a thing.\n"),
        _pr(changelog, number=3, title="chore: Internal", labels=("chore",), body="## Summary\n- Internal.\n"),
    ]
    content = changelog.render_release(
        repo="perosb/qvantum_custom_component",
        tag="2026.9.16",
        previous_tag="2026.9.14",
        published_at="2026-10-01T08:54:24Z",
        pull_requests=prs,
        callouts=changelog.extract_callouts(),
        overview="A short overview.",
    )
    assert content.startswith("# 2026.9.16\n")
    assert "_Released 2026-10-01" in content
    assert "compare 2026.9.14…2026.9.16" in content
    assert "## Bug fixes" in content
    assert "### Fix thing ([#2]" in content
    assert "Fixes a thing." in content
    assert "Internal changes" in content
    assert "#3" in content
    # Internal-only PRs are not given their own section.
    assert "## Chores" not in content


def test_render_release_internal_only_has_no_sections(changelog):
    content = changelog.render_release(
        repo="perosb/qvantum_custom_component",
        tag="2026.10.2",
        previous_tag="2026.9.16",
        published_at="2026-10-05T19:03:08Z",
        pull_requests=[_pr(changelog, title="chore: Internal", labels=("chore",))],
        callouts="",
        overview=None,
    )
    assert "## New features" not in content
    assert "## Bug fixes" not in content
    assert "Internal changes" in content


# --------------------------------------------------------------------------- #
# Index + overview
# --------------------------------------------------------------------------- #
def test_update_index_is_idempotent_and_sorted(changelog, tmp_path):
    index = tmp_path / "README.md"
    changelog.update_index(index, "2026.9.11", "2026-09-10T10:00:00Z", "Older")
    changelog.update_index(index, "2026.9.16", "2026-10-01T08:00:00Z", "Newer")

    text = index.read_text()
    assert text.count("[2026.9.16]") == 1
    rows = [line for line in text.splitlines() if line.startswith("| [")]
    assert rows[0].startswith("| [2026.9.16]")
    assert rows[1].startswith("| [2026.9.11]")

    # Re-running replaces rather than duplicates.
    changelog.update_index(index, "2026.9.16", "2026-10-01T08:00:00Z", "Changed")
    assert index.read_text().count("[2026.9.16]") == 1
    assert "Changed" in index.read_text()


def test_existing_overview_preserved(changelog, tmp_path):
    path = tmp_path / "2026.9.16.md"
    path.write_text(
        "# 2026.9.16\n\n"
        "_Released 2026-10-01 · [compare x](y)_\n\n"
        "A hand-written overview.\n\n"
        "> [!IMPORTANT]\n> hi\n\n## New features\n"
    )
    assert changelog.existing_overview(path) == "A hand-written overview."
    assert changelog.existing_overview(tmp_path / "missing.md") is None


# --------------------------------------------------------------------------- #
# GitHub interaction (monkeypatched)
# --------------------------------------------------------------------------- #
class FakeGh:
    """Minimal stand-in for the ``gh`` CLI used by the generator."""

    def __init__(self, *, releases, info, body, prs):
        self.releases = releases
        self.info = info
        self.body = body
        self.prs = prs
        self.edited: str | None = None

    def __call__(self, args):
        if args[:2] == ["release", "list"]:
            return json.dumps(self.releases)
        if args[:2] == ["release", "view"]:
            if "tagName,publishedAt,isPrerelease,isDraft" in args:
                if self.info is None:
                    raise __import__("subprocess").CalledProcessError(1, "gh")
                return json.dumps(self.info)
            return self.body
        if args[:2] == ["release", "edit"]:
            path = args[args.index("--notes-file") + 1]
            self.edited = Path(path).read_text()
            return ""
        if args[:2] == ["pr", "view"]:
            number = int(args[2])
            if number not in self.prs:
                raise __import__("subprocess").CalledProcessError(1, "gh")
            return json.dumps(self.prs[number])
        raise AssertionError(f"unexpected gh call: {args}")


RELEASES = [
    {"tagName": "2026.9.14", "publishedAt": "2026-09-20T05:05:32Z", "isPrerelease": False, "isDraft": False},
    {"tagName": "2026.9.16", "publishedAt": "2026-10-01T08:54:24Z", "isPrerelease": False, "isDraft": False},
]
INFO = {
    "tagName": "2026.9.16",
    "publishedAt": "2026-10-01T08:54:24Z",
    "isPrerelease": False,
    "isDraft": False,
}
BODY = (
    "# What's Changed\n\n## New features\n\n- #1 feat: Add thing @a\n\n"
    "## Bug fixes\n\n- #2 fix: Fix thing @b\n\n## Chores\n\n- #3 chore: Internal @a\n"
)
PRS = {
    1: {
        "number": 1,
        "title": "feat: Add thing",
        "body": "## Summary\n- Adds a thing.\n",
        "labels": [{"name": "enhancement"}],
        "url": "https://github.com/perosb/qvantum_custom_component/pull/1",
        "mergedAt": "2026-09-25T10:00:00Z",
    },
    2: {
        "number": 2,
        "title": "fix: Fix thing",
        "body": "## Summary\n- Fixes a thing.\n",
        "labels": [{"name": "bug"}],
        "url": "https://github.com/perosb/qvantum_custom_component/pull/2",
        "mergedAt": "2026-09-25T11:00:00Z",
    },
    3: {
        "number": 3,
        "title": "chore: Internal",
        "body": "## Summary\n- Internal.\n",
        "labels": [{"name": "chore"}],
        "url": "https://github.com/perosb/qvantum_custom_component/pull/3",
        "mergedAt": "2026-09-25T12:00:00Z",
    },
}


def test_collect_release_prs_uses_body_order(changelog, monkeypatch):
    fake = FakeGh(releases=RELEASES, info=INFO, body=BODY, prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    prs = changelog.collect_release_prs("o/r", "2026.9.16")
    assert [pr.number for pr in prs] == [1, 2, 3]
    assert prs[0].labels == ("enhancement",)


def test_previous_stable_release_skips_drafts_and_prereleases(changelog, monkeypatch):
    releases = RELEASES + [
        {"tagName": "2026.9.15", "publishedAt": "2026-09-26T09:52:56Z", "isPrerelease": True, "isDraft": False},
        {"tagName": "2026.10.2", "publishedAt": "0001-01-01T00:00:00Z", "isPrerelease": False, "isDraft": True},
    ]
    monkeypatch.setattr(
        changelog, "_gh", lambda args: json.dumps(releases)
    )
    previous = changelog.previous_stable_release("o/r")
    assert previous["tagName"] == "2026.9.16"
    # Before 2026-10-01, the previous stable is 2026.9.14.
    previous = changelog.previous_stable_release("o/r", before_iso="2026-10-01T08:54:24Z")
    assert previous["tagName"] == "2026.9.14"


def test_update_release_notes_is_idempotent(changelog, monkeypatch):
    fake = FakeGh(releases=RELEASES, info=INFO, body="# What's Changed\n\n## New features\n", prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    assert changelog.update_release_notes("o/r", "2026.9.16", "A friendly overview.") is True
    assert fake.edited is not None
    assert fake.edited.count("Full release notes") == 1
    assert fake.edited.count(changelog.OVERVIEW_MARKER_START) == 1
    assert "A friendly overview." in fake.edited
    assert "docs/releases/2026.9.16.md" in fake.edited

    # A second run with the same overview is a no-op (no duplicate block).
    fake.body = fake.edited
    assert changelog.update_release_notes("o/r", "2026.9.16", "A friendly overview.") is True
    assert fake.edited.count(changelog.OVERVIEW_MARKER_START) == 1

    # A changed overview replaces the block instead of appending.
    assert changelog.update_release_notes("o/r", "2026.9.16", "New overview.") is True
    assert fake.edited.count(changelog.OVERVIEW_MARKER_START) == 1
    assert "New overview." in fake.edited
    assert "A friendly overview." not in fake.edited


def test_remove_release_notes_strips_block_and_link(changelog, monkeypatch):
    body = (
        "# What's Changed\n\n"
        "<!-- changelog-overview:start -->\n"
        "Friendly.\n"
        "<!-- changelog-overview:end -->\n\n"
        "📄 **Full release notes:** [x](y)\n\n"
        "## New features\n"
    )
    fake = FakeGh(releases=RELEASES, info=INFO, body=body, prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    assert changelog.remove_release_notes("o/r", "2026.9.15") is True
    assert "changelog-overview" not in fake.edited
    assert "Full release notes" not in fake.edited
    assert "## New features" in fake.edited

    # Without inserted notes there is nothing to do.
    fake.body = "# What's Changed\n\n## New features\n"
    fake.edited = None
    assert changelog.remove_release_notes("o/r", "2026.9.15") is False
    assert fake.edited is None


class _FakeResponse:
    """Minimal context-manager response for ``urllib.request.urlopen``."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_polish_overview_skips_without_key(changelog, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    called = False

    def fake_urlopen(*args, **kwargs):  # pragma: no cover - must not be called
        nonlocal called
        called = True

    monkeypatch.setattr(changelog.urllib.request, "urlopen", fake_urlopen)
    assert changelog.polish_overview("Highlights: a.") is None
    assert called is False


def test_polish_overview_calls_openrouter(changelog, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode())
        body = json.dumps({"choices": [{"message": {"content": " Friendly.\n"}}]})
        return _FakeResponse(body.encode())

    monkeypatch.setattr(changelog.urllib.request, "urlopen", fake_urlopen)
    assert changelog.polish_overview("Highlights: a.", context="full doc") == "Friendly."
    assert captured["url"] == changelog.OPENROUTER_URL
    assert captured["payload"]["model"] == changelog.POLISH_MODEL
    assert "full doc" in captured["payload"]["messages"][0]["content"]


def test_polish_overview_returns_none_on_error(changelog, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")

    def boom(request, timeout=None):
        raise changelog.urllib.error.URLError("down")

    monkeypatch.setattr(changelog.urllib.request, "urlopen", boom)
    assert changelog.polish_overview("Highlights: a.") is None
    assert "skipped" in capsys.readouterr().err


def test_polish_overview_falls_back_on_truncation(changelog, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")

    def fake_urlopen(request, timeout=None):
        body = json.dumps(
            {"choices": [{"finish_reason": "length", "message": {"content": "Cut off"}}]}
        )
        return _FakeResponse(body.encode())

    monkeypatch.setattr(changelog.urllib.request, "urlopen", fake_urlopen)
    assert changelog.polish_overview("Highlights: a.") is None
    assert "truncated" in capsys.readouterr().err


def test_main_polish_writes_and_preserves(changelog, monkeypatch, tmp_path):
    fake = FakeGh(releases=RELEASES, info=INFO, body=BODY, prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    calls = {"n": 0}

    def fake_polish(text, **kwargs):
        calls["n"] += 1
        return "A friendly overview."

    monkeypatch.setattr(changelog, "polish_overview", fake_polish)
    out = tmp_path / "2026.9.16.md"
    index = tmp_path / "README.md"
    args = ["--tag", "2026.9.16", "--out", str(out), "--index", str(index), "--polish"]

    assert changelog.main(args) == 0
    assert "A friendly overview." in out.read_text()
    assert calls["n"] == 1

    # The polished overview is preserved on regeneration; the model is not called again.
    monkeypatch.setattr(changelog, "polish_overview", lambda *a, **k: "Should not be used.")
    assert changelog.main(args) == 0
    assert "A friendly overview." in out.read_text()
    assert "Should not be used." not in out.read_text()


def test_main_writes_file_and_index(changelog, monkeypatch, tmp_path):
    fake = FakeGh(releases=RELEASES, info=INFO, body=BODY, prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    out = tmp_path / "2026.9.16.md"
    index = tmp_path / "README.md"

    result = changelog.main(
        ["--tag", "2026.9.16", "--out", str(out), "--index", str(index)]
    )
    assert result == 0
    content = out.read_text()
    assert "### Add thing ([#1]" in content
    assert "### Fix thing ([#2]" in content
    assert "Internal changes" in content
    assert "[2026.9.16]" in index.read_text()


def test_main_dry_run_writes_nothing(changelog, monkeypatch, tmp_path):
    fake = FakeGh(releases=RELEASES, info=INFO, body=BODY, prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    out = tmp_path / "2026.9.16.md"
    result = changelog.main(
        ["--tag", "2026.9.16", "--out", str(out), "--index", str(tmp_path / "i.md"), "--dry-run"]
    )
    assert result == 0
    assert not out.exists()


def test_main_link_only_updates_release_body(changelog, monkeypatch, tmp_path):
    fake = FakeGh(releases=RELEASES, info=INFO, body=BODY, prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    result = changelog.main(["--tag", "2026.9.16", "--link-only"])
    assert result == 0
    assert fake.edited is not None
    assert "docs/releases/2026.9.16.md" in fake.edited


def test_main_skips_without_prs(changelog, monkeypatch, tmp_path, capsys):
    fake = FakeGh(releases=RELEASES, info=INFO, body="# What's Changed\n", prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    result = changelog.main(
        ["--tag", "2026.9.16", "--out", str(tmp_path / "x.md"), "--index", str(tmp_path / "i.md")]
    )
    assert result == 0
    assert "skipping" in capsys.readouterr().out
    assert not (tmp_path / "x.md").exists()


def test_prune_superseded_prereleases_keeps_latest(changelog, monkeypatch, tmp_path):
    releases = [
        {"tagName": "2026.9.11", "publishedAt": "2026-09-10T00:00:00Z", "isPrerelease": False, "isDraft": False},
        {"tagName": "2026.9.15", "publishedAt": "2026-09-26T00:00:00Z", "isPrerelease": True, "isDraft": False},
        {"tagName": "2026.9.16", "publishedAt": "2026-10-01T00:00:00Z", "isPrerelease": False, "isDraft": False},
        {"tagName": "2026.10.1", "publishedAt": "2026-10-05T00:00:00Z", "isPrerelease": True, "isDraft": False},
    ]
    linked_body = "# What's Changed\n\n📄 **Full release notes:** [x](y)\n"
    fake = FakeGh(releases=releases, info=INFO, body=linked_body, prs=PRS)
    monkeypatch.setattr(changelog, "_gh", fake)
    releases_dir = tmp_path / "releases"
    releases_dir.mkdir()
    index = releases_dir / "README.md"
    for tag in ("2026.9.15", "2026.9.16", "2026.10.1"):
        (releases_dir / f"{tag}.md").write_text(f"# {tag}\n")
        changelog.update_index(index, tag, "2026-09-26T00:00:00Z", "x")

    removed = changelog.prune_superseded_prereleases("o/r", releases_dir, index)

    assert removed == ["2026.9.15"]
    assert not (releases_dir / "2026.9.15.md").exists()
    assert (releases_dir / "2026.10.1.md").exists()  # latest pre-release kept
    assert (releases_dir / "2026.9.16.md").exists()  # stable kept
    assert "[2026.9.15]" not in index.read_text()
    assert "[2026.10.1]" in index.read_text()
    assert fake.edited is not None and "Full release notes" not in fake.edited


def test_repo_docs_index_lists_every_release(changelog):
    releases_dir = SCRIPT.parents[2] / "docs" / "releases"
    index = (releases_dir / "README.md").read_text(encoding="utf-8")
    files = [path for path in releases_dir.glob("*.md") if path.name != "README.md"]
    assert files, "expected at least one release notes file"
    for path in files:
        assert f"[{path.stem}]" in index, f"{path.name} missing from the index"
        assert path.read_text(encoding="utf-8").startswith(f"# {path.stem}\n")
