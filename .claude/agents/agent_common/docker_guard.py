#!/usr/bin/env python3
"""Block Bash commands that would stop or remove containers the agent doesn't own.

Dev containers mount the host's /var/run/docker.sock, so `docker ps` lists every
container on the machine — other projects' dev containers, databases, and the
agent's own container. An implement-critic once ran
`docker ps -q | xargs -r docker stop` to clear stale test databases and took all
of them down. Stopping containers by explicit name or by a filtered `docker ps`
stays allowed.

    docker_guard.py hook                 — Claude Code PreToolUse hook (stdin payload)
    docker_guard.py install <settings>   — merge the hook into a settings file

Stdlib only: the hook runs under the system python3, outside the agents' venv."""

import json
import re
import shlex
import sys
from pathlib import Path

_DESTRUCTIVE = r"docker\s+(?:container\s+)?(?:stop|kill|rm|restart|pause)\b"
_LIST = r"docker\s+(?:ps|container\s+(?:ls|list|ps))\b(?P<list_args>[^;&|)`\n]*)"
_DESTRUCTIVE_CMD = re.compile(rf"\b{_DESTRUCTIVE}(?P<args>[^;&|\n]*)")
_LIST_CMD = re.compile(rf"\b{_LIST}")
_LIST_PIPED_TO_XARGS = re.compile(
    rf"\b{_LIST}[^;&\n]*?\|(?!\|)[^;&\n]*\bxargs\b[^;&|\n]*\b{_DESTRUCTIVE}"
)
_LIST_INTO_VAR = re.compile(
    rf"(?:\b(?P<assigned>\w+)=|\bfor\s+(?P<looped>\w+)\s+in\s+)[\"']?(?:\$\(|`)\s*{_LIST}"
)
_FILTER_FLAG = re.compile(r"(?:^|\s)(?:--filter|-[a-zA-Z]*f)(?:\s|=|$)")
_PRUNE = re.compile(r"\bdocker\s+(?:system|container|volume|network)\s+prune\b")
_COMPOSE = re.compile(r"\bdocker(?:\s+|-)compose\b")
_COMPOSE_TEARDOWN = {"down", "stop", "kill", "rm"}
_COMPOSE_SCOPE_FLAGS = {"-f", "--file", "-p", "--project-name"}
_COMPOSE_VALUE_FLAGS = _COMPOSE_SCOPE_FLAGS | {
    "--env-file",
    "--profile",
    "--project-directory",
    "--ansi",
    "--progress",
    "--parallel",
}

_ADVICE = (
    " The Docker socket is the host's, so `docker ps` lists every container on the "
    "machine — other projects, their databases, and your own dev container. Stop or "
    "remove only containers you created, by name or with `docker ps --filter name=...`."
)


def _unfiltered(list_args: str) -> bool:
    return not _FILTER_FLAG.search(list_args)


def _targets_every_container(command: str) -> bool:
    """True when an unfiltered `docker ps` feeds a stop/kill/rm — inline as
    `$(...)`, piped through xargs, or via a variable or for-loop."""
    if any(_unfiltered(m.group("list_args")) for m in _LIST_PIPED_TO_XARGS.finditer(command)):
        return True
    destructive_args = [m.group("args") for m in _DESTRUCTIVE_CMD.finditer(command)]
    for args in destructive_args:
        if any(_unfiltered(m.group("list_args")) for m in _LIST_CMD.finditer(args)):
            return True
    for m in _LIST_INTO_VAR.finditer(command):
        if not _unfiltered(m.group("list_args")):
            continue
        var = re.escape(m.group("assigned") or m.group("looped"))
        if any(re.search(rf"\$\{{?{var}\b", args) for args in destructive_args):
            return True
    return False


def _compose_teardown_unscoped(command: str) -> bool:
    for match in _COMPOSE.finditer(command):
        rest = command[match.end() :]
        rest = re.split(r"[;&|\n]", rest, maxsplit=1)[0]
        try:
            tokens = shlex.split(rest)
        except ValueError:
            tokens = rest.split()
        scoped = False
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if not token.startswith("-"):
                if token in _COMPOSE_TEARDOWN and not scoped:
                    return True
                break
            name = token.split("=", 1)[0]
            scoped = scoped or name in _COMPOSE_SCOPE_FLAGS
            i += 2 if name in _COMPOSE_VALUE_FLAGS and "=" not in token else 1
    return False


def check(command: str) -> str | None:
    """Return why command is blocked, or None if it may run."""
    if _PRUNE.search(command):
        return "Blocked: `docker ... prune` acts on every container/volume on the host." + _ADVICE
    if _targets_every_container(command):
        return (
            "Blocked: this stops/removes containers selected by an unfiltered `docker ps`."
            + _ADVICE
        )
    if _compose_teardown_unscoped(command):
        return (
            "Blocked: `docker compose down/stop/kill/rm` without `-f <file>` or `-p <project>` "
            "acts on the workspace's own compose project, which includes your dev container."
            + _ADVICE
        )
    return None


async def sdk_pre_tool_use_hook(input_data: dict, tool_use_id, context) -> dict:
    """claude_agent_sdk PreToolUse callback form of check()."""
    reason = check((input_data.get("tool_input") or {}).get("command", ""))
    if reason is None:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


_SCRIPT = '"$CLAUDE_PROJECT_DIR/.claude/agents/agent_common/docker_guard.py"'
# The script is gitignored in installed projects; a fresh clone without it must
# not have every Bash call fail on a missing file.
HOOK = {
    "type": "command",
    "command": f"[ ! -f {_SCRIPT} ] || python3 {_SCRIPT} hook",
    "timeout": 10,
}


def _is_ours(handler: dict) -> bool:
    return "agent_common/docker_guard.py" in handler.get("command", "")


def merge_hook(settings: dict) -> dict:
    """Replace any earlier copy of HOOK, keeping every other key and hook."""
    hooks = settings.setdefault("hooks", {})
    kept = []
    for group in hooks.get("PreToolUse", []):
        handlers = [h for h in group.get("hooks", []) if not _is_ours(h)]
        if handlers:
            kept.append({**group, "hooks": handlers})
    hooks["PreToolUse"] = [*kept, {"matcher": "Bash", "hooks": [dict(HOOK)]}]
    return settings


def install(path: Path) -> str:
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as e:
        return f"SKIP {path.name}: not valid JSON ({e}); add the docker guard hook by hand"
    if not isinstance(settings, dict):
        return f"SKIP {path.name}: not a JSON object; add the docker guard hook by hand"
    text = json.dumps(merge_hook(settings), indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return f"UNCHANGED {path.name}: docker guard hook"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"MERGE {path.name}: docker guard hook"


def main() -> int:
    if sys.argv[1:2] == ["install"]:
        print(install(Path(sys.argv[2])))
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    reason = check((payload.get("tool_input") or {}).get("command", ""))
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
