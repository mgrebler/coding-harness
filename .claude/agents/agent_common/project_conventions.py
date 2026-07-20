"""
Discover project-specific CI commands and test-file locations by parsing the
target project's own `.specify/memory/constitution.md` and `README.md`, rather
than hardcoding any particular stack (pnpm/Vitest/Playwright/backend-frontend
split, etc.) into the harness itself.

CI checks are NOT forced into a fixed taxonomy (typecheck/lint/unit/e2e) — a
project may have more, fewer, or differently-named checks than that. Instead,
each bullet in the CI Requirements section becomes one (label, command) pair,
whatever its label says. The only thing this module classifies is "slow": a
label matching e2e/integration/browser-ish keywords is excluded from the quick
pre-critic gate, everything else runs in both the quick and full gates.
"""

import re
import shlex
import subprocess
from pathlib import Path

CONSTITUTION_PATH = Path(".specify/memory/constitution.md")
README_PATH = Path("README.md")

_LOG_PREFIX = "[project-conventions]"

# Used only to decide what's excluded from the fast pre-critic gate — not to
# bucket checks into a fixed set of categories.
_SLOW_KEYWORDS = ("e2e", "end-to-end", "end to end", "playwright", "integration", "browser")

# Heading/label keywords that mark a README fenced code block as CI-relevant.
_README_CI_KEYWORDS = (
    "typecheck",
    "type check",
    "type-check",
    "lint",
    "test",
    "e2e",
    "playwright",
    "ci",
    "continuous integration",
)

_HEADING_RE = re.compile(r"(?im)^#{1,6}\s*.*$")
_CI_SECTION_HEADING_RE = re.compile(
    r"(?im)^#{1,6}.*\b(ci requirements|continuous integration)\b.*$"
)
_TEST_LOCATION_HEADING_RE = re.compile(r"(?im)^#{1,6}.*\btest file location\b.*$")
_BULLET_RE = re.compile(r"(?m)^-\s+(.*)$")
_LABEL_RE = re.compile(r"^([^`(:]+)")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_HEADING_FENCE_RE = re.compile(
    # Heading capture is deliberately restricted to a single line ([^\n]+?, no
    # DOTALL): with DOTALL applied to `.+?` here, a heading not directly
    # followed by a fence (e.g. one with a prose paragraph before its fenced
    # block) would swallow that prose into the "heading" text on its way to
    # the next fence anywhere in the doc — and a stray keyword substring in
    # that prose (e.g. "...set it explicitly:" containing "ci") could then
    # false-match a CI keyword and misfire on an unrelated code block.
    r"(?im)^#{1,6}[ \t]*([^\n]+?)[ \t]*\n+```(?:bash|sh|shell|zsh)?\n(.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


def _log(msg: str) -> None:
    print(f"{_LOG_PREFIX} {msg}", flush=True)


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _section_after(text: str, heading_match: re.Match) -> str:
    """Return the text between a matched heading and the next heading or a '---' rule."""
    rest = text[heading_match.end() :]
    end = len(rest)
    next_heading = _HEADING_RE.search(rest)
    if next_heading:
        end = min(end, next_heading.start())
    next_rule = re.search(r"(?m)^---\s*$", rest)
    if next_rule:
        end = min(end, next_rule.start())
    return rest[:end]


def _is_placeholder(command: str) -> bool:
    return "[PROJECT" in command.upper() or not command.strip()


def _label_from_bullet(bullet: str) -> str:
    match = _LABEL_RE.match(bullet)
    return (match.group(1) if match else bullet).strip()


def _commands_from_constitution(constitution_text: str) -> list[tuple[str, list[str]]]:
    resolved: list[tuple[str, list[str]]] = []
    heading = _CI_SECTION_HEADING_RE.search(constitution_text)
    if not heading:
        return resolved
    section = _section_after(constitution_text, heading)
    for bullet in _BULLET_RE.findall(section):
        backticks = _BACKTICK_RE.findall(bullet)
        if not backticks:
            continue
        command = backticks[-1].strip()
        if _is_placeholder(command):
            continue
        resolved.append((_label_from_bullet(bullet), shlex.split(command)))
    return resolved


def _commands_from_readme(readme_text: str) -> list[tuple[str, list[str]]]:
    resolved: list[tuple[str, list[str]]] = []
    for match in _HEADING_FENCE_RE.finditer(readme_text):
        heading, body = match.group(1), match.group(2)
        if not any(kw in heading.lower() for kw in _README_CI_KEYWORDS):
            continue
        first_line = next(
            (
                line.strip()
                for line in body.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ),
            None,
        )
        if first_line:
            resolved.append((heading.strip("# ").strip(), shlex.split(first_line)))
    return resolved


def is_slow_check(label: str) -> bool:
    """True if label looks like an e2e/integration/browser-style check — used to
    exclude it from the fast pre-critic gate, not to decide whether it ever runs."""
    return any(kw in label.lower() for kw in _SLOW_KEYWORDS)


def resolve_ci_commands() -> list[tuple[str, list[str]]]:
    """
    Resolve the project's CI checks as an ordered list of (label, command) pairs.
    Every bullet with an explicit backtick-quoted command in constitution.md's CI
    Requirements section becomes one entry, using the project's own label for it —
    no fixed typecheck/lint/unit/e2e taxonomy is assumed. Any README.md fenced code
    block under a CI-ish heading not already covered by a constitution entry (by
    label) is added too. An empty list means no explicit CI commands could be
    found — callers should skip CI enforcement and log why, not guess a command.
    """
    constitution_text = _read_optional(CONSTITUTION_PATH)
    readme_text = _read_optional(README_PATH)

    resolved = _commands_from_constitution(constitution_text)
    seen = {label.lower() for label, _ in resolved}

    for label, cmd in _commands_from_readme(readme_text):
        if label.lower() in seen:
            continue
        resolved.append((label, cmd))
        seen.add(label.lower())

    if not resolved:
        _log(
            "no CI commands found in constitution.md or README.md — CI enforcement is "
            "disabled until commands are declared. Add explicit backtick-quoted commands "
            "to the CI Requirements section of constitution.md to enable it."
        )
    else:
        for label, cmd in resolved:
            _log(f"CI check '{label}' → {' '.join(cmd)}")

    return resolved


def _test_dirs_from_constitution(constitution_text: str) -> tuple[str, ...]:
    heading = _TEST_LOCATION_HEADING_RE.search(constitution_text)
    section = _section_after(constitution_text, heading) if heading else constitution_text
    dirs: list[str] = []
    for bullet in _BULLET_RE.findall(section):
        if "test" not in bullet.lower():
            continue
        for path in _BACKTICK_RE.findall(bullet):
            path = path.strip()
            if _is_placeholder(path) or not path.endswith("/"):
                continue
            if path not in dirs:
                dirs.append(path)
    return tuple(dirs)


def _test_dirs_from_git() -> tuple[str, ...]:
    result = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    if result.returncode != 0:
        return ()
    dirs: list[str] = []
    for line in result.stdout.splitlines():
        parts = Path(line).parts
        for i, part in enumerate(parts[:-1]):
            if part.lower() in ("test", "tests", "__tests__", "spec"):
                d = "/".join(parts[: i + 1]) + "/"
                if d not in dirs:
                    dirs.append(d)
    return tuple(dirs)


def resolve_test_dirs() -> tuple[str, ...]:
    """
    Resolve the set of directory prefixes that hold test files, preferring
    explicit backtick-quoted paths in constitution.md's "Test file location"
    bullets, falling back to a generic scan of tracked files for directories
    literally named test/tests/__tests__/spec. Never assumes backend/frontend.
    """
    constitution_text = _read_optional(CONSTITUTION_PATH)
    dirs = _test_dirs_from_constitution(constitution_text)
    if dirs:
        _log(f"test dirs: resolved from constitution.md → {', '.join(dirs)}")
        return dirs

    dirs = _test_dirs_from_git()
    if dirs:
        _log(f"test dirs: resolved from repo layout → {', '.join(dirs)}")
    else:
        _log(
            "test dirs: could not resolve from constitution.md or repo layout — "
            "test-file filtering will find nothing. Add explicit paths to the "
            "'Test file location' bullets in constitution.md to fix this."
        )
    return dirs
