"""Unit tests for the pi PR review poster (.github/pi/post_review.py).

The script is CI tooling rather than integration code, so it is loaded by path
instead of imported as a package. All GitHub calls are monkeypatched.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "pi" / "post_review.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pi_post_review", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def review_module():
    return _load_module()


@pytest.mark.parametrize(
    "raw",
    [
        '{"overview":"a","comments":[]}',
        '```json\n{"overview":"a","comments":[]}\n```',
        'Here you go:\n{"overview":"a","comments":[]}\nHope that helps!',
        '{\n// stray object\n{"overview":"a","comments":[]}',
        '{"overview":"has } brace","comments":[]} {"overview":"b","comments":[]}',
    ],
)
def test_parse_review_json_tolerates_wrapping(review_module, raw):
    assert review_module.parse_review_json(raw)["overview"] in {"a", "has } brace"}


def test_parse_review_json_raises_without_object(review_module):
    with pytest.raises(RuntimeError):
        review_module.parse_review_json("no json at all")


def test_suggestion_body_variants(review_module):
    assert review_module.suggestion_body({"comment": "nit"}) == "nit"
    assert "```suggestion\nx = 1\n```" in review_module.suggestion_body(
        {"comment": "why", "suggestion": "x = 1"}
    )
    # Nested fences switch to a four-backtick outer fence.
    assert "````suggestion" in review_module.suggestion_body(
        {"comment": "why", "suggestion": "```\ncode\n```"}
    )
    # Empty suggestion deletes the lines but still renders an apply-able block.
    assert "```suggestion\n\n```" in review_module.suggestion_body(
        {"comment": "remove", "suggestion": ""}
    )


def test_extract_assistant_text_uses_length_capped_message(review_module, tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"agent_start"}',
                '{"type":"message_end","message":{"role":"assistant","stopReason":"length",'
                '"content":[{"type":"text","text":"{\\"overview\\":\\"partial\\",\\"comments\\":[]}"}]}}',
                '{"type":"message_end","message":{"role":"assistant","stopReason":"stop","content":[]}}',
            ]
        )
        + "\n"
    )
    assert "partial" in review_module.extract_assistant_text(str(events))


def test_extract_assistant_text_raises_when_empty(review_module, tmp_path):
    events = tmp_path / "empty.jsonl"
    events.write_text('{"type":"agent_start"}\n')
    with pytest.raises(RuntimeError):
        review_module.extract_assistant_text(str(events))


def _capture_post_review(review_module, monkeypatch, comments: list[dict[str, Any]], already: set):
    posted: dict[str, Any] = {}
    unanchored: list = []

    def fake_gh_api(endpoint: str, payload: Any = None, method=None, paginate: bool = False):
        if endpoint.endswith("/reviews"):
            posted["review"] = payload
        return (0, {})

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(review_module, "existing_review_comments", lambda repo, number: already)
    monkeypatch.setattr(review_module, "gh_api", fake_gh_api)
    monkeypatch.setattr(
        review_module, "post_unanchorable", lambda repo, number, dropped: unanchored.extend(dropped)
    )
    review_module.post_review(1, "sha", {"overview": "o", "comments": comments})
    return posted.get("review"), unanchored


def test_duplicates_do_not_consume_the_comment_cap(review_module, monkeypatch):
    # A duplicate-heavy head must not hide the real comment that follows it.
    duplicates = [
        {
            "path": "a.py",
            "line": 10 + i,
            "severity": "low",
            "comment": "duplicate",
            "suggestion": "x = 1",
        }
        for i in range(review_module.MAX_COMMENTS)
    ]
    real = {"path": "b.py", "line": 42, "severity": "high", "comment": "real issue"}
    review = {"overview": "o", "comments": duplicates + [real]}

    already = set()
    for comment in duplicates:
        body = f"{review_module.SEVERITY_LABEL['low']}\n\n{review_module.suggestion_body(comment)}"
        already.add((comment["path"], comment["line"], body[:120]))

    posted, _ = _capture_post_review(review_module, monkeypatch, review["comments"], already)

    assert posted is not None
    assert len(posted["comments"]) == 1
    assert posted["comments"][0]["path"] == "b.py"


def test_invalid_line_is_dropped_and_reported_not_counted(review_module, monkeypatch):
    comments = [
        {"path": "a.py", "severity": "low", "comment": "no line"},
        {"path": "b.py", "line": 5, "severity": "high", "comment": "anchorable"},
    ]
    posted, unanchored = _capture_post_review(review_module, monkeypatch, comments, set())

    assert [c["path"] for c in posted["comments"]] == ["b.py"]
    assert unanchored and unanchored[0][0] == "a.py"


def test_single_line_suggestion_omits_start_line(review_module, monkeypatch):
    comments = [
        {"path": "a.py", "line": 7, "start_line": 7, "severity": "low", "comment": "x"},
        {"path": "a.py", "line": 12, "start_line": 10, "severity": "low", "comment": "y"},
    ]
    posted, _ = _capture_post_review(review_module, monkeypatch, comments, set())

    by_line = {c["line"]: c for c in posted["comments"]}
    assert "start_line" not in by_line[7]
    assert by_line[12]["start_line"] == 10
