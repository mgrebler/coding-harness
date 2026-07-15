"""Console/logging plumbing: stdout/stderr teeing and Claude Agent SDK message printing."""

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

# Exit code reserved for "hit a Claude usage/session limit" (see SessionLimitError).
# 0 = clean abort/already-done, 1 = generic failure, 2 = already means "local LLM not
# configured" one layer down in ollama.py — never surfaced at this level.
USAGE_LIMIT_EXIT_CODE = 3

_SESSION_LIMIT_MARKERS = ("session limit", "usage limit", "rate limit")


class SessionLimitError(Exception):
    """
    Raised by stream_query() in place of the SDK's generic, unhelpful exception when
    the CLI's result indicates a session/usage limit was hit mid-turn. str(e) carries
    the original human-readable CLI text (e.g. "You've hit your session limit ·
    resets 3:20am (UTC)").
    """


def _is_session_limit_result(message: ResultMessage) -> bool:
    """
    True when a ResultMessage reflects the CLI's session/usage-limit quirk: is_error
    is set but errors[] is empty, which is exactly the combination that makes the SDK
    fall back to using subtype ("success") as the error text — see
    claude_agent_sdk/_internal/query.py's read-loop, around the ProcessError
    replacement logic. Confirmed by the marker text in .result, or (as a fallback,
    since wording may change) the tell-tale subtype=="success" alongside is_error.
    """
    if not message.is_error or message.errors:
        return False
    text = (message.result or "").lower()
    return any(marker in text for marker in _SESSION_LIMIT_MARKERS) or message.subtype == "success"


class _Tee:
    """Write to multiple file-like objects simultaneously."""

    def __init__(self, *files: IO):
        self._files = files

    def write(self, text: str):
        for f in self._files:
            f.write(text)
            f.flush()

    def flush(self):
        for f in self._files:
            f.flush()

    def fileno(self):
        return self._files[0].fileno()


def setup_log_file(path: Path):
    """
    Open *path* in append mode and tee all stdout/stderr to it.
    Call once per script after spec_dir is known.
    A run-separator line is written so successive runs are easy to distinguish.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = path.open("a", encoding="utf-8")
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_fh.write(f"\n{'=' * 60}\n[run started {ts}]\n{'=' * 60}\n")
    log_fh.flush()
    sys.stdout = _Tee(sys.__stdout__, log_fh)  # type: ignore[assignment]
    sys.stderr = _Tee(sys.__stderr__, log_fh)  # type: ignore[assignment]


def make_logger(agent_name: str):
    """Return a log function prefixed with the agent name."""

    def log(msg: str):
        print(f"[{agent_name}] {msg}", flush=True)

    return log


def log_sdk_message(message, prefix: str = ""):
    """Print a Claude Agent SDK message in a readable format."""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                for line in block.text.strip().splitlines():
                    print(f"{prefix}{line}", flush=True)
            elif isinstance(block, ToolUseBlock):
                args = ", ".join(f"{k}={str(v)[:80]!r}" for k, v in block.input.items())
                print(f"{prefix}→ {block.name}({args})", flush=True)
    elif isinstance(message, ResultMessage) and message.result:
        print(f"{prefix}[done] {message.result[:200]}", flush=True)


async def stream_query(messages, prefix: str = "  ") -> None:
    """
    Consume an SDK query() async iterator, logging each message via log_sdk_message.

    Replaces the `async for message in query(...): log_sdk_message(message, ...)`
    idiom used at every *-auto.py call site. When the CLI hits a session/usage limit
    mid-turn, the SDK raises a generic Exception("Claude Code returned an error
    result: success") instead of surfacing the readable text already present on the
    preceding ResultMessage — this re-raises that case as SessionLimitError with the
    original text, and lets any other exception propagate unchanged.
    """
    last_result = None
    try:
        async for message in messages:
            log_sdk_message(message, prefix=prefix)
            if isinstance(message, ResultMessage):
                last_result = message
    except Exception:
        if last_result is not None and _is_session_limit_result(last_result):
            raise SessionLimitError(last_result.result) from None
        raise
