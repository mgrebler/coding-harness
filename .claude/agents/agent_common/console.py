"""Console/logging plumbing: stdout/stderr teeing and Claude Agent SDK message printing."""

import contextlib
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock


def _restore_blocking_stdio() -> None:
    for stream in (sys.__stdout__, sys.__stderr__):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            os.set_blocking(stream.fileno(), True)


def _safe_print(text: str, end: str = "\n", retries: int = 20, delay: float = 0.05) -> None:
    """print(text, end=end, flush=True), surviving BlockingIOError.

    A subprocess sharing our stdout (e.g. a webServer's Node process) can flip
    its fd to O_NONBLOCK; a large write then hits EAGAIN, which print() doesn't
    retry. Sleeping alone wasn't enough — specs/086 and 088 crashed the
    orchestrator logging a multi-MB e2e failure — so the fd is put back into
    blocking mode before each retry, which lets the write simply wait."""
    for attempt in range(retries):
        try:
            print(text, end=end, flush=True)
            return
        except BlockingIOError:
            if attempt == retries - 1:
                raise
            _restore_blocking_stdio()
            time.sleep(delay)


# Exit code reserved for "hit a Claude usage/session limit" (see SessionLimitError).
# 0 = clean abort/already-done, 1 = generic failure, 2 = already means "local LLM not
# configured" one layer down in ollama.py — never surfaced at this level.
USAGE_LIMIT_EXIT_CODE = 3
# A stage refused to start because a human must act first (e.g. approve a
# plan's proposed governance amendments) — not a failure, nothing to escalate.
HUMAN_ACTION_EXIT_CODE = 4

_SESSION_LIMIT_MARKERS = ("session limit", "usage limit", "rate limit")


class SessionLimitError(Exception):
    """
    Raised by stream_query() in place of the SDK's generic, unhelpful exception when
    the CLI's result indicates a session/usage limit was hit mid-turn. str(e) carries
    the original human-readable CLI text (e.g. "You've hit your session limit ·
    resets 3:20am (UTC)").
    """


_LIMIT_RESET_RE = re.compile(r"(?i)\bresets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(UTC\)")


def limit_reset_time(text: str, now: datetime) -> datetime | None:
    """When a SessionLimitError's limit resets, from its "resets 9:20am (UTC)"
    text: the next such time after `now`, or None if the text has none."""
    match = _LIMIT_RESET_RE.search(text)
    if not match:
        return None
    hour = int(match[1]) % 12 + (12 if match[3].lower() == "pm" else 0)
    reset = now.astimezone(UTC).replace(
        hour=hour, minute=int(match[2] or 0), second=0, microsecond=0
    )
    return reset if reset > now else reset + timedelta(days=1)


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


def _tolerate_unencodable(stream: IO) -> IO:
    """On Windows a piped stdout defaults to cp1252, which cannot encode the "→"
    log_sdk_message prints for every tool call — the resulting UnicodeEncodeError
    killed a whole stage mid-run. Replace such characters instead of raising."""
    # AttributeError: not a TextIOWrapper (e.g. a test double), so leave it as it is.
    with contextlib.suppress(AttributeError, ValueError):
        stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    return stream


def setup_log_file(path: Path):
    """
    Open *path* in append mode and tee all stdout/stderr to it.
    Call once per script after spec_dir is known.
    A run-separator line is written so successive runs are easy to distinguish.
    """
    _tolerate_unencodable(sys.__stdout__)
    _tolerate_unencodable(sys.__stderr__)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = path.open("a", encoding="utf-8")
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_fh.write(f"\n{'=' * 60}\n[run started {ts}]\n{'=' * 60}\n")
    log_fh.flush()
    sys.stdout = _Tee(sys.__stdout__, log_fh)  # type: ignore[assignment]
    sys.stderr = _Tee(sys.__stderr__, log_fh)  # type: ignore[assignment]


def stream_subprocess(cmd: list[str], prefix: str = "", idle_timeout: float | None = None) -> int:
    """
    Run *cmd* as a subprocess, streaming stdout+stderr line-by-line through
    the current sys.stdout (which may be a _Tee writing to both terminal and
    log file). Returns the process exit code.

    prefix is prepended to each streamed line — used when multiple critic
    subprocesses run concurrently (parallel multi-critic fan-out) so their
    interleaved output stays attributable to a critic id, e.g. "[qwen] ".
    Empty by default, which reproduces the original unprefixed output.

    idle_timeout, if set, kills the subprocess and raises TimeoutError once
    idle_timeout seconds pass with no new output line — a genuine "no progress"
    guard, not a flat wall-clock cap, so a slow-but-still-streaming call is never
    killed. None (the default) reproduces the exact prior behavior: block on
    proc.stdout with no timeout.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.stdout is None:
        raise RuntimeError("subprocess.Popen with stdout=PIPE did not provide a stdout stream")

    if idle_timeout is None:
        for line in proc.stdout:
            _safe_print(f"{prefix}{line}", end="")
        proc.wait()
        return proc.returncode

    lines: queue.Queue = queue.Queue()

    def _read_lines() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)  # EOF sentinel

    reader = threading.Thread(target=_read_lines, daemon=True)
    reader.start()

    while True:
        try:
            line = lines.get(timeout=idle_timeout)
        except queue.Empty:
            proc.kill()
            proc.wait()
            raise TimeoutError(
                f"subprocess produced no output for {idle_timeout:.0f}s, killed: {cmd}"
            ) from None
        if line is None:
            break
        _safe_print(f"{prefix}{line}", end="")

    proc.wait()
    return proc.returncode


def make_logger(agent_name: str):

    def log(msg: str):
        _safe_print(f"[{agent_name}] {msg}")

    return log


def log_sdk_message(message, prefix: str = ""):
    """Print a Claude Agent SDK message in a readable format."""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                for line in block.text.strip().splitlines():
                    _safe_print(f"{prefix}{line}")
            elif isinstance(block, ToolUseBlock):
                args = ", ".join(f"{k}={str(v)[:80]!r}" for k, v in block.input.items())
                _safe_print(f"{prefix}→ {block.name}({args})")
    elif isinstance(message, ResultMessage) and message.result:
        _safe_print(f"{prefix}[done] {message.result[:200]}")


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
