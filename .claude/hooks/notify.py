#!/usr/bin/env python3
"""Claude Code hook: tell the user Claude is waiting on them.

    notify.py bell   — Stop / PermissionRequest: ring the terminal bell
    notify.py push   — Notification: push to ntfy when the user seems away

Hooks run without a controlling terminal, so the bell is handed back to Claude
Code as a `terminalSequence` for it to emit on its own TTY. Any failure here is
swallowed: a missed notification must never break the session."""

import contextlib
import json
import os
import sys
import urllib.request
from pathlib import Path

# permission_prompt only fires once the user has stopped typing for a while,
# and idle_prompt 60s after a finished turn — both mean "probably away", which
# is when a phone push is worth sending.
_PUSH_TYPES = {"permission_prompt", "idle_prompt"}
_OFF = {"0", "false", "off", "no"}


def bell() -> None:
    if os.environ.get("CLAUDE_NOTIFY_BELL", "1").strip().lower() in _OFF:
        return
    print(json.dumps({"terminalSequence": "\a"}))


def push_request(payload: dict) -> urllib.request.Request | None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic or payload.get("notification_type") not in _PUSH_TYPES:
        return None
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    project = Path(payload.get("cwd") or Path.cwd()).name
    message = payload.get("message") or "Claude Code is waiting for you"
    return urllib.request.Request(  # noqa: S310 — https by default; NTFY_SERVER is the user's own choice
        f"{server}/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": f"Claude Code - {project}"},
        method="POST",
    )


def push(payload: dict) -> None:
    request = push_request(payload)
    if request is None:
        return
    with contextlib.suppress(OSError, ValueError):
        urllib.request.urlopen(request, timeout=5).close()  # noqa: S310 — see push_request


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "bell":
        bell()
    elif mode == "push":
        push(payload)


if __name__ == "__main__":
    main()
