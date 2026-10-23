#!/usr/bin/env python3
"""Claude Code hook: tell the user Claude is waiting on them.

    notify.py bell   — Stop / PermissionRequest: ring the terminal bell
    notify.py push   — Stop / Notification: push to ntfy when the user seems away
    notify.py install <settings.json> — merge these hooks into a settings file

Hooks run without a controlling terminal, so the bell is handed back to Claude
Code as a `terminalSequence` for it to emit on its own TTY. Any failure here is
swallowed: a missed notification must never break the session."""

import contextlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

_OFF = {"0", "false", "off", "no"}

_SCRIPT = '"$CLAUDE_PROJECT_DIR/.claude/hooks/notify.py"'


def _hook(mode: str) -> dict:
    # notify.py is gitignored in installed projects, so a fresh clone that hasn't
    # run install.sh lacks it. `python3 <missing file>` exits 2, which a Stop hook
    # treats as "keep going" — guard so a missing script is a silent no-op.
    command = f"[ ! -f {_SCRIPT} ] || python3 {_SCRIPT} {mode}"
    return {"type": "command", "command": command, "timeout": 10}


HOOKS = {
    "Stop": [{"hooks": [_hook("bell"), _hook("push")]}],
    "PermissionRequest": [{"hooks": [_hook("bell")]}],
    "Notification": [{"hooks": [_hook("push")]}],
}


def _enabled(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() not in _OFF


def bell() -> None:
    if _enabled("CLAUDE_NOTIFY_BELL"):
        print(json.dumps({"terminalSequence": "\a"}))


def _push_message(payload: dict) -> str | None:
    push_on_stop = _enabled("CLAUDE_NOTIFY_PUSH_ON_STOP")
    if payload.get("hook_event_name") == "Stop":
        return "Claude finished and is waiting for you" if push_on_stop else None
    # permission_prompt only fires once the user has stopped typing for a while.
    # idle_prompt (60s after a finished turn) would just repeat the Stop push,
    # so it only stands in for it when push-on-stop is turned off.
    away_types = {"permission_prompt"} if push_on_stop else {"permission_prompt", "idle_prompt"}
    if payload.get("notification_type") not in away_types:
        return None
    return payload.get("message") or "Claude Code is waiting for you"


def project_name(cwd: str) -> str:
    # Dev containers mount every project at /workspace, so the folder name can't
    # tell sessions apart; the origin remote's repo name can.
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        url = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout.strip()
        name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
        if name:
            return name
    return Path(cwd).name


def push_request(payload: dict) -> urllib.request.Request | None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    message = _push_message(payload)
    if not topic or message is None:
        return None
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    project = project_name(payload.get("cwd") or str(Path.cwd()))
    return urllib.request.Request(  # noqa: S310 — https by default; NTFY_SERVER is the user's own choice
        f"{server}/{topic}",
        data=f"{project}: {message}".encode(),
        headers={"Title": f"Claude Code - {project}"},
        method="POST",
    )


def push(payload: dict) -> None:
    request = push_request(payload)
    if request is None:
        return
    with contextlib.suppress(OSError, ValueError):
        urllib.request.urlopen(request, timeout=5).close()  # noqa: S310 — see push_request


def _is_ours(handler: dict) -> bool:
    return ".claude/hooks/notify.py" in handler.get("command", "")


def merge_hooks(settings: dict) -> dict:
    """Replace any earlier copy of HOOKS in settings, keeping every other key and
    every hook that isn't ours."""
    hooks = settings.setdefault("hooks", {})
    for event, groups in HOOKS.items():
        kept = []
        for group in hooks.get(event, []):
            handlers = [h for h in group.get("hooks", []) if not _is_ours(h)]
            if handlers:
                kept.append({**group, "hooks": handlers})
        hooks[event] = kept + json.loads(json.dumps(groups))
    return settings


def install(path: Path) -> str:
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as e:
        return f"SKIP {path.name}: not valid JSON ({e}); add the notification hooks by hand"
    if not isinstance(settings, dict):
        return f"SKIP {path.name}: not a JSON object; add the notification hooks by hand"
    text = json.dumps(merge_hooks(settings), indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return f"UNCHANGED {path.name}: notification hooks"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"MERGE {path.name}: notification hooks"


def main() -> None:
    if sys.argv[1:2] == ["install"]:
        print(install(Path(sys.argv[2])))
        return
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
