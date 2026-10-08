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
    """Return the text of the last completed assistant message."""
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
            if event.get("type") == "message_end" and message.get("stopReason") == "stop":
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
    """Extract the review JSON object, tolerating stray code fences."""
    candidate = raw.strip()
    if "```" in candidate:
        # Prefer a fenced json block if present.
        for fence in ("```json", "```"):
            if fence in candidate:
                start = candidate.index(fence) + len(fence)
                end = candidate.find("```", start)
                if end != -1:
                    candidate = candidate[start:end]
                break
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("review output contains no JSON object")
    review = json.loads(candidate[start : end + 1])
    if not isinstance(review, dict):
        raise RuntimeError("review output is not a JSON object")
    return review


def suggestion_body(comment: dict) -> str | None:
    """Return the comment body, with a suggestion block when applicable."""
    text = str(comment.get("comment") or "").strip()
    if "suggestion" not in comment:
        return text
    suggestion = str(comment.get("suggestion") or "")
    # Nested code fences need an outer 4-backtick fence.
    fence = "````" if SUGGESTION_FENCE in suggestion else SUGGESTION_FENCE
    return f"{text}\n\n{fence}suggestion\n{suggestion}\n{fence}"


def gh_api(endpoint: str, payload: dict | None = None, method: str | None = None) -> tuple[int, dict]:
    command = ["gh", "api", endpoint, "--input", "-"]
    args = ["--method", method] if method else []
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


def post_review(number: int, head_sha: str, review: dict) -> None:
    raw_comments = review.get("comments") or []
    if isinstance(raw_comments, dict):
        raw_comments = list(raw_comments.values())
    raw_comments = [item for item in raw_comments if isinstance(item, dict)]
    raw_comments.sort(key=lambda item: (SEVERITY_ORDER.get(str(item.get("severity", "low")).lower(), 9)))

    overview = str(review.get("overview") or "").strip() or "Clean diff."
    body = f"## 🤖 Pi review\n\n{overview}\n"

    inline = []
    dropped = []
    for comment in raw_comments[:MAX_COMMENTS]:
        path = str(comment.get("path") or "").strip()
        try:
            line = int(comment.get("line"))
        except (TypeError, ValueError):
            dropped.append((path, None, "missing/invalid line number"))
            continue
        severity = str(comment.get("severity") or "medium").lower()
        entry = {
            "path": path,
            "line": line,
            "side": "RIGHT",
            "body": f"{SEVERITY_LABEL.get(severity, '💬 Low')}\n\n{suggestion_body(comment) or 'No comment text.'}",
        }
        if comment.get("start_line") is not None:
            try:
                entry["start_line"] = int(comment["start_line"])
                entry["start_side"] = "RIGHT"
            except (TypeError, ValueError):
                pass
        inline.append(entry)

    payload = {"event": "COMMENT", "body": body, "comments": inline}
    if head_sha:
        payload["commit_id"] = head_sha

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    code, response = gh_api(f"repos/{repo}/pulls/{number}/reviews", payload)
    if code == 0:
        accepted = len(response.get("comments") or [])
        print(f"Posted review with {accepted} inline comment(s).")
        return

    # GitHub rejected the whole batch — retry comment by comment so valid ones
    # still land inline, and fold the rest into the review body.
    print(f"Batch review rejected (HTTP {code}); retrying inline comments individually.", file=sys.stderr)
    kept = []
    for entry in inline:
        single = {
            "event": "COMMENT",
            "body": entry["body"],
            "commit_id": head_sha,
            "path": entry["path"],
            "line": entry["line"],
            "side": entry["side"],
        }
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
        body = f"## 🤖 Pi review\n\n{overview}\n\n_(No inline comments could be posted.)_\n"

    code, _ = gh_api(f"repos/{repo}/pulls/{number}/reviews", {"event": "COMMENT", "body": body})
    if code != 0:
        print("Failed to post fallback review body.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", help="path to pi's --mode json output")
    parser.add_argument("number", type=int, help="pull request number")
    parser.add_argument("--head-sha", default="", help="PR head commit SHA for comment anchoring")
    args = parser.parse_args()

    try:
        raw = extract_assistant_text(args.events)
        review = parse_review_json(raw)
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"Review parsing failed: {error}", file=sys.stderr)
        sys.exit(1)

    post_review(args.number, args.head_sha, review)


if __name__ == "__main__":
    main()
