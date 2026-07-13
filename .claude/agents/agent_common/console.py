"""Console/logging plumbing: stdout/stderr teeing and Claude Agent SDK message printing."""

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock


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
    log_fh = open(path, "a", encoding="utf-8")  # noqa: SIM115 (kept open for process lifetime)
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
