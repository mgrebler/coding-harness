"""Tool runtime for the local agentic generation loop (local_agent_loop.py):
Read/Write/Edit/Glob/Grep/Bash implementations, their OpenAI-style
function-calling schemas, and dispatch(). Read/Write/Edit mirror Claude
Code's own tool contracts (1-indexed "cat -n" reads, exact old_string
uniqueness for Edit) since models are commonly trained on that convention.

Every filesystem tool is confined to the project root — no path may escape
it. Bash runs with a fixed cwd, a timeout, an output cap, and a denylist for
a small set of catastrophic one-liners. This is defense-in-depth, not a
jail: the real backstops are that local-model generation is opt-in per
.specify/local-llm.json, the devcontainer is the actual sandbox boundary,
and the existing critic gates plus mandatory human PR review are unchanged
regardless of which backend generated the code."""

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agent_common import files

_DEFAULT_DENY_PATTERNS = [
    r"\brm\s+-rf\s+/(\s|$)",
    r"\brm\s+-rf\s+~(\s|$)",
    r"\bgit\s+push\b.*(--force\b|-f\b)",
    r"\bgit\s+reset\s+--hard\s+origin\b",
    r"\bsudo\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bchmod\s+-R\s+777\s+/(\s|$)",
    r"\b(curl|wget)\b[^|]*\|\s*(ba)?sh\b",
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:",  # fork bomb
]

_SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv"}

MAX_GLOB_RESULTS = 200
MAX_GREP_RESULTS = 200


class ToolError(Exception):
    """Model-recoverable tool failure. Caught by dispatch() and turned into
    an 'Error: ...' tool-result string fed back to the model, never crashes
    the loop."""


def _project_root() -> Path:
    """Not cached: mirrors ollama.py's per-call re-read of
    .specify/local-llm.json, so tests can chdir per-test/per-call."""
    return Path.cwd().resolve()


def _resolve_in_root(path_str: str) -> Path:
    """Resolve path_str (relative or absolute) against the project root,
    rejecting anything that escapes it via '..' traversal or an
    out-of-root absolute path."""
    if not path_str:
        raise ToolError("path must not be empty")
    root = _project_root()
    candidate = Path(path_str)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ToolError(f"path {path_str!r} escapes the project root ({root})") from None
    return resolved


def _parse_int(args: dict, key: str) -> int | None:
    if args.get(key) is None:
        return None
    try:
        return int(args[key])
    except (TypeError, ValueError) as e:
        raise ToolError(f"{key} must be an integer, got {args[key]!r}") from e


def _is_skipped(path: Path, root: Path) -> bool:
    return any(part in _SKIP_DIR_NAMES for part in path.relative_to(root).parts)


def tool_read(args: dict) -> str:
    path = _resolve_in_root(args.get("file_path", ""))
    if not path.exists():
        raise ToolError(f"file not found: {args.get('file_path')}")
    if path.is_dir():
        raise ToolError(f"{args.get('file_path')} is a directory, not a file")
    try:
        text = files.read_file(path)
    except UnicodeDecodeError as e:
        raise ToolError(f"{args.get('file_path')} is not valid UTF-8 text") from e

    lines = text.splitlines()
    offset = _parse_int(args, "offset") or 0
    limit = _parse_int(args, "limit")
    selected = lines[offset : offset + limit] if limit is not None else lines[offset:]
    return "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(selected))


def tool_write(args: dict) -> str:
    file_path = args.get("file_path", "")
    path = _resolve_in_root(file_path)
    content = args.get("content", "")
    files.write_file(path, content)
    return f"wrote {len(content)} bytes to {file_path}"


def tool_edit(args: dict) -> str:
    file_path = args.get("file_path", "")
    path = _resolve_in_root(file_path)
    if not path.exists():
        raise ToolError(f"file not found: {file_path} (use Write to create a new file)")

    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    if not old_string:
        raise ToolError("old_string must not be empty (use Write to create new files)")
    if old_string == new_string:
        raise ToolError("old_string and new_string must differ")

    text = files.read_file(path)
    count = text.count(old_string)
    if count == 0:
        raise ToolError(f"old_string not found in {file_path}")
    if count > 1 and not replace_all:
        raise ToolError(
            f"old_string is not unique in {file_path} ({count} matches) — include more "
            "surrounding context to make it unique, or pass replace_all=true"
        )

    updated = (
        text.replace(old_string, new_string)
        if replace_all
        else text.replace(old_string, new_string, 1)
    )
    files.write_file(path, updated)
    return f"edited {file_path} ({count} replacement{'s' if count != 1 else ''})"


def tool_glob(args: dict) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        raise ToolError("pattern must not be empty")
    root = _project_root()
    base = _resolve_in_root(args["path"]) if args.get("path") else root
    if not base.exists():
        raise ToolError(f"path not found: {args.get('path', '.')}")

    matches = [m for m in base.glob(pattern) if m.is_file() and not _is_skipped(m.resolve(), root)]
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    rel = [str(m.relative_to(root)) for m in matches]
    truncated = len(rel) > MAX_GLOB_RESULTS
    rel = rel[:MAX_GLOB_RESULTS]
    result = "\n".join(rel) if rel else "(no matches)"
    if truncated:
        result += f"\n... truncated to {MAX_GLOB_RESULTS} results"
    return result


def _grep_file(p: Path, rel_path: Path, regex: re.Pattern) -> list[str]:
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return [
        f"{rel_path}:{lineno}:{line}"
        for lineno, line in enumerate(text.splitlines(), start=1)
        if regex.search(line)
    ]


def tool_grep(args: dict) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        raise ToolError("pattern must not be empty")
    flags = re.IGNORECASE if args.get("case_insensitive") else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        raise ToolError(f"invalid regex: {e}") from e

    root = _project_root()
    base = _resolve_in_root(args["path"]) if args.get("path") else root
    if not base.exists():
        raise ToolError(f"path not found: {args.get('path', '.')}")
    glob_filter = args.get("glob")

    candidates = base.rglob("*") if base.is_dir() else [base]
    results: list[str] = []
    for p in candidates:
        if not p.is_file() or _is_skipped(p.resolve(), root):
            continue
        if glob_filter and not fnmatch.fnmatch(p.name, glob_filter):
            continue
        results.extend(_grep_file(p, p.relative_to(root), regex))
        if len(results) >= MAX_GREP_RESULTS:
            break

    if not results:
        return "(no matches)"
    truncated = len(results) > MAX_GREP_RESULTS
    results = results[:MAX_GREP_RESULTS]
    result = "\n".join(results)
    if truncated:
        result += f"\n... truncated to {MAX_GREP_RESULTS} results"
    return result


@dataclass
class BashSandboxConfig:
    timeout_s: int = 120
    output_max_bytes: int = 200_000
    deny_patterns: list = field(default_factory=lambda: list(_DEFAULT_DENY_PATTERNS))


def tool_bash(args: dict, sandbox: BashSandboxConfig) -> str:
    command = args.get("command", "")
    if not command.strip():
        raise ToolError("command must not be empty")
    for pattern in sandbox.deny_patterns:
        if re.search(pattern, command):
            raise ToolError(f"command rejected by safety denylist (matched pattern: {pattern!r})")

    try:
        proc = subprocess.run(  # noqa: S602 — shell=True is required: this tool's contract is
            # "run an arbitrary shell command," identical in spirit to Claude Code's own Bash
            # tool; the denylist/timeout/output-cap above and cwd confinement below are the
            # mitigations, not argument-list sanitization.
            command,
            shell=True,
            cwd=_project_root(),
            capture_output=True,
            text=True,
            timeout=sandbox.timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"command timed out after {sandbox.timeout_s}s") from e

    output = (proc.stdout or "") + (proc.stderr or "")
    output_bytes = output.encode("utf-8")
    if len(output_bytes) > sandbox.output_max_bytes:
        # Tail-biased: the actionable failure (e.g. a failing test's assertion) is usually
        # at the end of build/test output, not the beginning.
        output = "... (truncated) ...\n" + output_bytes[-sandbox.output_max_bytes :].decode(
            "utf-8", errors="replace"
        )
    return f"exit code: {proc.returncode}\n{output}"


TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": (
                "Read a file from the project, relative to the project root. "
                "Returns content with 1-indexed line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relative to the project root.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "0-indexed line to start reading from.",
                    },
                    "limit": {"type": "integer", "description": "Maximum number of lines to read."},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Write",
            "description": "Write content to a file, overwriting it if it exists and creating parent directories as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relative to the project root.",
                    },
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Edit",
            "description": (
                "Replace an exact string in an existing file. old_string must match "
                "uniquely in the file unless replace_all is set — include enough "
                "surrounding context to make it unique."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relative to the project root.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find, must be unique unless replace_all.",
                    },
                    "new_string": {"type": "string", "description": "Text to replace it with."},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence instead of requiring uniqueness.",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": "Find files by glob pattern (e.g. 'specs/**/*.md'), relative to the project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern."},
                    "path": {
                        "type": "string",
                        "description": "Directory to search from (default: project root).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": "Search file contents by regex, returning matching 'path:line:text' rows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search (default: project root).",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Only search files matching this filename glob, e.g. '*.py'.",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Case-insensitive match.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": (
                "Run a shell command in the project root (e.g. to run tests, or "
                "`git add`/`git commit`). Subject to a timeout, an output size cap, "
                "and a denylist for destructive commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                },
                "required": ["command"],
            },
        },
    },
]

_TOOL_FUNCS = {
    "Read": tool_read,
    "Write": tool_write,
    "Edit": tool_edit,
    "Glob": tool_glob,
    "Grep": tool_grep,
}


def dispatch(name: str, arguments: dict, sandbox: BashSandboxConfig) -> str:
    """Execute one tool call and return its result as a string. Never raises:
    ToolError and any other exception are caught and turned into an
    'Error: ...' tool-result string fed back to the model, so a single bad
    call (or a bug in a tool implementation) can't crash the whole
    generation loop."""
    try:
        if name == "Bash":
            return tool_bash(arguments, sandbox)
        func = _TOOL_FUNCS.get(name)
        if func is None:
            raise ToolError(f"unknown tool: {name}")
        return func(arguments)
    except ToolError as e:
        return f"Error: {e}"
    except Exception as e:  # defensive: a tool bug must not crash the whole generation loop
        return f"Error: unexpected failure in {name}: {e}"
