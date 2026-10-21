#!/usr/bin/env python3
"""Tee pi's JSON event stream to a file while printing a readable log.

``pi --mode json`` writes one JSON object per line to stdout. The review
workflow must keep that stream intact for ``post_review.py``, but a plain
``tee`` dumps the whole prompt and diff into the Actions log. This filter
writes every line verbatim to ``--output`` and, when logging is enabled, prints
the review-relevant highlights so a stuck or failing run is diagnosable from
the console log alone.

Logging is opt-in: pass ``--verbose`` or set ``PI_REVIEW_LOG`` to a truthy value
(``1``, ``true``, ``yes``, ``on``). Without it the filter is a pure pass-through
to the file and the step stays as quiet as before.

Only stdout is rewritten; pi's diagnostics on stderr are left untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TextIO

MAX_TEXT = 300
TRUTHY = {"1", "true", "yes", "on"}
LOG_ENV = "PI_REVIEW_LOG"

state = {"logs": False, "turns": 0, "tools": 0, "cost": 0.0, "errors": 0}


def clip(value: str, limit: int = MAX_TEXT) -> str:
    """Collapse whitespace and cap a value so one event cannot flood the log."""
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def emit(line: str = "") -> None:
    """Print one highlight line, unless logging is disabled."""
    if state["logs"]:
        print(line, flush=True)


def handle_update(update: dict) -> None:
    """Render one streaming assistant-message delta."""
    kind = update.get("type")
    if kind == "thinking_start":
        emit("\n[thinking]")
    elif kind == "thinking_end":
        emit(f"[thinking] {clip(update.get('content') or '')}")
    elif kind == "text_start":
        emit("\n[answer]")
    elif kind == "text_delta":
        if state["logs"]:
            print(update.get("delta") or "", end="", flush=True)
    elif kind == "toolcall_end":
        # The matching tool_execution_start carries the same call; this line
        # only marks that the model finished emitting the arguments.
        call = update.get("toolCall") or {}
        emit(f"\n[call] {call.get('name')}")
    elif kind == "error":
        state["errors"] += 1
        emit(f"\n[error] {clip(update.get('reason') or '')} {clip(json.dumps(update.get('error') or {}))}")


def handle(event: dict) -> None:
    """Print the highlights of one session event."""
    kind = event.get("type")
    if kind == "message_update":
        handle_update(event.get("assistantMessageEvent") or {})
    elif kind == "tool_execution_start":
        state["tools"] += 1
        emit(f"[tool] {event.get('toolName')} {clip(json.dumps(event.get('args') or {}))}")
    elif kind == "tool_execution_end":
        duration = event.get("durationMs")
        suffix = f" {duration}ms" if duration is not None else ""
        emit(f"[tool] {event.get('toolName')}{suffix} error={bool(event.get('isError'))}")
    elif kind == "message_end":
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            return
        state["turns"] += 1
        usage = message.get("usage") or {}
        cost = (usage.get("cost") or {}).get("total")
        if isinstance(cost, (int, float)):
            state["cost"] += cost
        emit(
            "\n[usage]"
            f" in={usage.get('input')} out={usage.get('output')}"
            f" cache_read={usage.get('cacheRead')} cost={cost}"
            f" stop={message.get('stopReason')}"
        )
    elif kind in ("auto_retry_start", "auto_retry_end", "compaction_start", "compaction_end"):
        emit(f"[{kind}] {clip(json.dumps(event), 400)}")
    elif kind == "agent_settled":
        emit(f"[agent_settled] aborted={event.get('aborted')}")


def env_flag(name: str) -> bool:
    """Read a boolean-ish environment variable; anything else means off."""
    return os.environ.get(name, "").strip().lower() in TRUTHY


def summarize() -> None:
    """Print one line an operator can grep for after the run."""
    emit(
        f"\n[summary] assistant_messages={state['turns']}"
        f" tool_calls={state['tools']} stream_errors={state['errors']}"
        f" cost={state['cost']:.4f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="/tmp/pi-events.jsonl",
        help="File that receives the untouched JSONL stream (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=f"Print highlights to the log; also enabled by {LOG_ENV}=1",
    )
    args = parser.parse_args(argv)
    state["logs"] = args.verbose or env_flag(LOG_ENV)

    raw: TextIO = open(args.output, "w", encoding="utf-8")
    try:
        for line in sys.stdin:
            raw.write(line)
            raw.flush()
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                handle(event)
    except BrokenPipeError:
        return 0
    finally:
        raw.close()
    summarize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
