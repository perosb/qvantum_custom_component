#!/usr/bin/env python3
"""Parse pi's JSON event stream from a PR review run and post a GitHub review.

Reads pi's ``--mode json`` output, extracts the assistant's review JSON, and
posts it as a PR review with inline comments. ``suggestion`` fields become
GitHub ```suggestion``` blocks with an "Apply suggestion" button.

Inline comments that GitHub rejects (e.g. line outside the diff) are appended
to the review body instead so no feedback is lost.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SUGGESTION_FENCE = "```"
MAX_COMMENTS = 20
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_LABEL = {
    "critical": "🚨 **Critical**",
    "high": "❗ **High**",
    "medium": "⚠️ Medium",
    "low": "💬 Low",
}


def extract_assistant_text(events_path: str) -> str:
    """Return the text of the last assistant message that contains text.

    Requiring a specific stop reason would lose the review when the model hits
    its output-token limit (stopReason "length"); the message may still hold
    the complete JSON. Error/aborted messages carry no text and are skipped.
    """
    text = ""
    with open(events_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            if event.get("type") == "message_end":
                content = message.get("content") or []
                chunks = []
                if isinstance(content, str):
                    chunks.append(content)
                else:
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            chunks.append(block.get("text") or "")
                if chunks:
                    text = "\n".join(chunks)
    if not text:
        raise RuntimeError("pi produced no completed assistant message")
    return text


def parse_review_json(raw: str) -> dict:
    """Extract the review JSON object, tolerating prose or stray code fences.

    Models sometimes wrap the object in fences, add trailing commentary or
    emit multiple objects. Scan every "{" and accept the first position from
    which a complete JSON object can be decoded.
    """
    decoder = json.JSONDecoder()
    start = raw.find("{")
    while start != -1:
        try:
            review, _ = decoder.raw_decode(raw[start:])
            if isinstance(review, dict):
                return review
        except json.JSONDecodeError:
            pass
        start = raw.find("{", start + 1)
    raise RuntimeError(
        f"review output contains no decodable JSON object (head: {raw[:200]!r})"
    )


def suggestion_body(comment: dict) -> str | None:
    """Return the comment body, with a suggestion block when applicable."""
    text = str(comment.get("comment") or "").strip()
    if "suggestion" not in comment:
        return text
    suggestion = str(comment.get("suggestion") or "")
    # Nested code fences need an outer 4-backtick fence.
    fence = "````" if SUGGESTION_FENCE in suggestion else SUGGESTION_FENCE
    return f"{text}\n\n{fence}suggestion\n{suggestion}\n{fence}"


def gh_api(endpoint: str, payload: dict | None = None, method: str | None = None, paginate: bool = False) -> tuple[int, dict]:
    command = ["gh", "api"]
    if paginate:
        command.append("--paginate")
    command.append(endpoint)
    args = []
    if method:
        args += ["--method", method]
    if payload is not None:
        # gh defaults to POST whenever a request body is present, so only pass
        # --input for actual writes; GET lookups would otherwise be sent as POST.
        args += ["--input", "-"]
    result = subprocess.run(
        command + args,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    body = {}
    if result.stdout.strip():
        try:
            body = json.loads(result.stdout)
        except json.JSONDecodeError:
            body = {}
    return result.returncode, body


def existing_review_comments(repo: str, number: int) -> set[tuple[str, int, str]]:
    """Return (path, line, body-prefix) of comments already posted on the PR.

    The review agent sees the full cumulative diff on every push and tends to
    re-emit comments that were already fixed in earlier pushes; skip those.
    """
    code, comments = gh_api(f"repos/{repo}/pulls/{number}/comments?per_page=100", paginate=True)
    if code != 0 or not isinstance(comments, list):
        return set()
    seen = set()
    for item in comments:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        try:
            line = int(item.get("line") or 0)
        except (TypeError, ValueError):
            continue
        body = str(item.get("body") or "").strip()
        seen.add((path, line, body))
    return seen


def post_unanchorable(repo: str, number: int, dropped: list[tuple[str, int | None, str]]) -> None:
    """Surface comments that could not be anchored as an issue comment."""
    if not dropped:
        return
    lines = "\n".join(
        f"- `{path}:{line or '?'}` ({reason})" for path, line, reason in dropped
    )
    gh_api(
        f"repos/{repo}/issues/{number}/comments",
        {"body": f"⚠️ Pi review could not anchor these to the diff:\n\n{lines}"},
    )


def post_review(number: int, head_sha: str, review: dict) -> None:
    raw_comments = review.get("comments") or []
    if isinstance(raw_comments, dict):
        raw_comments = list(raw_comments.values())
    raw_comments = [item for item in raw_comments if isinstance(item, dict)]
    raw_comments.sort(key=lambda item: (SEVERITY_ORDER.get(str(item.get("severity", "low")).lower(), 9)))

    overview = str(review.get("overview") or "").strip() or "Clean diff."
    body = f"{overview}\n"

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    already_posted = existing_review_comments(repo, number)

    inline = []
    dropped = []
    skipped = 0
    # Drop duplicates and unanchorable entries before applying MAX_COMMENTS so a
    # duplicate-heavy head cannot hide real comments further down the list.
    unique = []
    for comment in raw_comments:
        path = str(comment.get("path") or "").strip()
        try:
            line = int(comment.get("line"))
        except (TypeError, ValueError):
            dropped.append((path, None, "missing/invalid line number"))
            continue
        severity = str(comment.get("severity") or "medium").lower()
        body_text = f"{SEVERITY_LABEL.get(severity, '💬 Low')}\n\n{suggestion_body(comment) or 'No comment text.'}"
        if (path, line, body_text) in already_posted:
            skipped += 1
            continue
        unique.append(comment)

    for comment in unique[:MAX_COMMENTS]:
        path = str(comment.get("path") or "").strip()
        try:
            line = int(comment.get("line"))
        except (TypeError, ValueError):
            dropped.append((path, None, "missing/invalid line number"))
            continue
        severity = str(comment.get("severity") or "medium").lower()
        body_text = f"{SEVERITY_LABEL.get(severity, '💬 Low')}\n\n{suggestion_body(comment) or 'No comment text.'}"
        entry = {
            "path": path,
            "line": line,
            "side": "RIGHT",
            "body": body_text,
        }
        if comment.get("start_line") is not None:
            try:
                start = int(comment["start_line"])
                # GitHub rejects multi-line ranges where start == line; single-
                # line comments must omit start_line entirely.
                if 0 < start < line:
                    entry["start_line"] = start
                    entry["start_side"] = "RIGHT"
            except (TypeError, ValueError):
                pass
        inline.append(entry)

    payload = {"event": "COMMENT", "body": body, "comments": inline}
    if head_sha:
        payload["commit_id"] = head_sha
    code, _ = gh_api(f"repos/{repo}/pulls/{number}/reviews", payload)
    if code == 0:
        print(f"Posted review with {len(inline)} inline comment(s) ({skipped} duplicate(s) skipped).")
        post_unanchorable(repo, number, dropped)
        return

    # GitHub rejected the whole batch — retry comment by comment so valid ones
    # still land inline, and fold the rest into the review body.
    print(f"Batch review rejected (HTTP {code}); retrying inline comments individually.", file=sys.stderr)
    kept = []
    for entry in inline:
        single = {
            "event": "COMMENT",
            "body": entry["body"],
            "path": entry["path"],
            "line": entry["line"],
            "side": entry["side"],
        }
        if head_sha:
            single["commit_id"] = head_sha
        if "start_line" in entry:
            single["start_line"] = entry["start_line"]
            single["start_side"] = entry["start_side"]
        single_code, _ = gh_api(f"repos/{repo}/pulls/{number}/comments", single)
        if single_code == 0:
            kept.append(entry)
        else:
            dropped.append((entry["path"], entry.get("line"), "outside the diff"))

    body += "\n### Could not anchor these to the diff\n\n"
    for path, line, reason in dropped:
        body += f"- `{path}:{line or '?'}` ({reason})\n"
    for entry in kept:
        body += f"- `{entry['path']}:{entry['line']}` — posted inline\n"
    if not dropped and not kept:
        body = f"{overview}\n\n_(No inline comments could be posted.)_\n"

    code, _ = gh_api(f"repos/{repo}/pulls/{number}/reviews", {"event": "COMMENT", "body": body})
    if code != 0:
        print("Failed to post fallback review body.", file=sys.stderr)
        sys.exit(1)


def post_fallback_comment(number: int, raw: str) -> None:
    """Keep the review visible even when the JSON could not be parsed."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    body = (
        "⚠️ The model's output could not be parsed as review JSON. Raw text:\n\n"
        f"<details><summary>Raw model output</summary>\n\n{raw[:8000]}\n\n</details>"
    )
    code, _ = gh_api(f"repos/{repo}/issues/{number}/comments", {"body": body})
    if code == 0:
        print("Posted unparsed review as fallback comment.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", help="path to pi's --mode json output")
    parser.add_argument("number", type=int, help="pull request number")
    parser.add_argument("--head-sha", default="", help="PR head commit SHA for comment anchoring")
    args = parser.parse_args()

    raw = ""
    try:
        raw = extract_assistant_text(args.events)
        review = parse_review_json(raw)
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"Review parsing failed: {error}", file=sys.stderr)
        if raw:
            post_fallback_comment(args.number, raw)
        sys.exit(1)

    post_review(args.number, args.head_sha, review)


if __name__ == "__main__":
    main()
