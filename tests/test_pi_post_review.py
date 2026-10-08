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
        already.add((comment["path"], body))

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


def test_batch_rejection_retries_per_comment(review_module, monkeypatch):
    calls: list[tuple[str, Any]] = []

    def fake_gh_api(endpoint: str, payload: Any = None, method=None, paginate: bool = False):
        calls.append((endpoint, payload))
        if endpoint.endswith("/reviews"):
            # The batch attempt (with comments) is rejected; the body-only
            # fallback review succeeds.
            return (1, {}) if payload and payload.get("comments") else (0, {})
        return (0, {})

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(review_module, "existing_review_comments", lambda repo, number: set())
    monkeypatch.setattr(review_module, "gh_api", fake_gh_api)
    monkeypatch.setattr(review_module, "post_unanchorable", lambda repo, number, dropped: None)

    comments = [{"path": "a.py", "line": 5, "severity": "high", "comment": "boom"}]
    review_module.post_review(1, "sha", {"overview": "o", "comments": comments})

    per_comment = [endpoint for endpoint, _ in calls if endpoint.endswith("/comments")]
    assert per_comment == ["repos/owner/repo/pulls/1/comments"]

    fallback_bodies = [
        payload["body"]
        for endpoint, payload in calls
        if endpoint.endswith("/reviews") and payload and "comments" not in payload
    ]
    assert fallback_bodies, "expected a body-only fallback review"
    assert "a.py:5" in fallback_bodies[-1]


def test_extract_assistant_text_keeps_earlier_text_over_empty_block(review_module, tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"message_end","message":{"role":"assistant","stopReason":"stop",'
                '"content":[{"type":"text","text":"{\\"overview\\":\\"kept\\",\\"comments\\":[]}"}]}}',
                '{"type":"message_end","message":{"role":"assistant","stopReason":"stop",'
                '"content":[{"type":"text","text":""}]}}',
            ]
        )
        + "\n"
    )
    assert "kept" in review_module.extract_assistant_text(str(events))


def test_gh_api_tolerates_non_json_output(review_module, monkeypatch):
    class Result:
        returncode = 0
        stdout = "not json"

    monkeypatch.setattr(review_module.subprocess, "run", lambda *a, **k: Result())
    assert review_module.gh_api("repos/x/y") == (0, {})


def test_existing_review_comments_empty_on_error(review_module, monkeypatch):
    monkeypatch.setattr(review_module, "gh_api", lambda *a, **k: (1, {}))
    assert review_module.existing_review_comments("owner/repo", 1) == set()


def test_post_fallback_comment_uses_issue_endpoint(review_module, monkeypatch):
    captured: list[tuple[str, Any]] = []
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(
        review_module, "gh_api", lambda endpoint, payload=None, **k: captured.append((endpoint, payload)) or (0, {})
    )
    review_module.post_fallback_comment(7, "raw model text")
    assert captured[0][0] == "repos/owner/repo/issues/7/comments"
    assert "raw model text" in captured[0][1]["body"]


def test_severity_defaults_are_consistent(review_module):
    assert review_module.severity_of({}) == "medium"
    assert review_module.severity_of({"severity": "HIGH"}) == "high"
    assert review_module.severity_of({"severity": "bogus"}) == "medium"


def test_truncation_is_reported(review_module, monkeypatch):
    comments = [
        {"path": "a.py", "line": i + 1, "severity": "low", "comment": f"c{i}"}
        for i in range(review_module.MAX_COMMENTS + 5)
    ]
    posted, _ = _capture_post_review(review_module, monkeypatch, comments, set())
    assert "5 further comment(s) omitted" in posted["body"]


def test_single_line_suggestion_omits_start_line(review_module, monkeypatch):
    comments = [
        {"path": "a.py", "line": 7, "start_line": 7, "severity": "low", "comment": "x"},
        {"path": "a.py", "line": 12, "start_line": 10, "severity": "low", "comment": "y"},
    ]
    posted, _ = _capture_post_review(review_module, monkeypatch, comments, set())

    by_line = {c["line"]: c for c in posted["comments"]}
    assert "start_line" not in by_line[7]
    assert by_line[12]["start_line"] == 10
