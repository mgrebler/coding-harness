"""
Deterministic, non-LLM checks that run before a critic invocation.

Several blocking findings in this harness's critic history were pattern-
matchable and didn't need a full LLM critic round-trip to catch — burning an
iteration (and the wall-clock/cost that goes with it) on a pure regex-shaped
failure. This module holds those checks so the *_auto.py orchestrators can
gate on them directly:

- Task format (§T5): tasks.md entries not in the machine-readable
  `- [ ] TXXX [TEST|IMPL] [PY] [USZ]` form. Previously only surfaced as
  critic-prompt context (ch_2_tasks_critic.py's task_format_analysis) — an
  LLM had to notice and report it as a violation every time. Recurred across
  5+ features.
- Red-state artifact validity (§TQ2): a `*-red.txt` capture that shows an
  infrastructure failure (connection refused, service unreachable, "no
  tests found") rather than a genuine failing assertion doesn't prove the
  test would fail without the implementation. Recurred across 5+ features,
  including one case where an all-green run was mislabeled as red-state.
- Commit hygiene: an oversized diff (accidental binary, core dump, build
  artifact) is cheap to catch mechanically. One feature committed a 4.5GB
  core dump that a critic only caught after the fact.
"""

import re
import subprocess
from pathlib import Path

# --- Task format (§T5) -------------------------------------------------

# A compliant task line is a markdown checkbox (`- [ ]`, `- [x]`, or `- [X]`)
# immediately followed by a bare, unbracketed task ID (`T001`, not `[T001]`).
# This is not cosmetic: ch_3_test_auto.py and ch_4_implement_auto.py scan for
# the literal substring "- [ ]" to detect which tasks still need work, so a
# task line missing the checkbox is silently treated as already complete and
# permanently skipped by those downstream orchestrators.
_CHECKBOX_TXXX_RE = re.compile(r"^[-*]\s+\[[ xX]\]\s*T\d+\b")
_BARE_TXXX_RE = re.compile(r"\bT\d+\b")

# A task with no testable deliverable (orientation/read-only) is exempt from
# [TEST]/[IMPL] tagging per Constitution §5 "Non-deliverable tasks" — this
# check only flags a MISSING [TEST]/[IMPL] tag when the task line itself
# doesn't look like an orientation task (heuristic: mentions no output file
# path via " — " or ": " with a path-like segment). This heuristic is
# deliberately permissive — it exists to catch the mechanical, unambiguous
# cases (numbered lists, bracketed-ID-without-checkbox); genuinely ambiguous
# tagging calls are still left to the LLM critic.


def _classify_txxx_issues(stripped: str) -> list[str]:
    issues = []
    if not re.search(r"\[TEST\]|\[IMPL\]", stripped):
        issues.append("MISSING [TEST] or [IMPL]")
    if not re.search(r"\[US\d+\]", stripped):
        issues.append("MISSING [USX] story label")
    return issues


def _record_txxx_result(
    stripped: str, i: int, complete_tasks: list[str], incomplete_tasks: list[str]
) -> None:
    entry = f"  Line {i}: {stripped[:100]}"
    issues = _classify_txxx_issues(stripped)
    if issues:
        incomplete_tasks.append(f"{entry} ← {', '.join(issues)}")
    else:
        complete_tasks.append(entry)


def classify_task_lines(tasks: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Bucket each non-blank, non-comment line into complete_tasks,
    incomplete_tasks, numbered, or bullets."""
    complete_tasks: list[str] = []
    incomplete_tasks: list[str] = []
    numbered: list[str] = []
    bullets: list[str] = []

    for i, line in enumerate(tasks.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^\d+\.", stripped):
            numbered.append(f"  Line {i}: {stripped[:100]}")
        elif stripped.startswith(("- ", "* ")):
            if _CHECKBOX_TXXX_RE.match(stripped):
                _record_txxx_result(stripped, i, complete_tasks, incomplete_tasks)
            elif _BARE_TXXX_RE.search(stripped):
                incomplete_tasks.append(
                    f"  Line {i}: {stripped[:100]} ← MISSING checkbox "
                    f"(`- [ ]`/`- [x]` must immediately precede the bare task ID, "
                    f"e.g. `- [ ] T001 ...`, not `- [T001] ...`)"
                )
            else:
                bullets.append(f"  Line {i}: {stripped[:100]}")
        elif _CHECKBOX_TXXX_RE.match(stripped) or _BARE_TXXX_RE.match(stripped):
            _record_txxx_result(stripped, i, complete_tasks, incomplete_tasks)

    return complete_tasks, incomplete_tasks, numbered, bullets


def analyze_task_format(tasks: str) -> str:
    """Human-readable breakdown used as critic-prompt context."""
    complete_tasks, incomplete_tasks, numbered, bullets = classify_task_lines(tasks)

    parts = []
    if complete_tasks:
        parts.append(
            "Complete machine-readable tasks (`- [ ]`/`- [x]` Txxx [TEST|IMPL] [PX] [USX] format):\n"
            + "\n".join(complete_tasks)
        )
    if incomplete_tasks:
        parts.append(
            "Incomplete Txxx tasks — VIOLATE §T5 (missing checkbox and/or required components):\n"
            + "\n".join(incomplete_tasks)
        )
    if numbered:
        parts.append(
            "Numbered list entries found — THESE VIOLATE §T5 (must use `- [ ] Txxx [TEST|IMPL]` format):\n"
            + "\n".join(numbered)
        )
    if bullets and not complete_tasks and not incomplete_tasks:
        parts.append(
            "Plain bullet entries found (no Txxx ID, no machine-readable tasks in file) — may violate §T5:\n"
            + "\n".join(bullets)
        )
    if not parts:
        parts.append("No task entries detected.")
    return "\n\n".join(parts)


# Deliberately narrow: a task ID wrapped in its OWN brackets right after the
# bullet marker (`- [T001] ...`) — the LLM-generated mix-up between "checkbox
# brackets" and "ID brackets" that silently breaks downstream orchestrator
# scanning (see the docstring below). Tried a broader heuristic first (any
# bullet whose first word is a bare Txxx ID, or any numbered line carrying a
# [TEST]/[IMPL] tag) and validated it against every tasks.md under specs/ —
# it produced false positives on legitimate narrative "Dependencies"/
# "Within Each Phase" prose that explains task ordering in sentences like
# "T001 [TEST] must complete before T002 [IMPL] begins", which is
# structurally indistinguishable from a malformed task line without
# semantic judgment. The bracketed-ID form has zero such collisions across
# the full historical corpus, so it's the only pattern safe to auto-gate on
# without an LLM in the loop; the broader cases stay in analyze_task_format
# for the critic to judge with full context.
_BRACKETED_ID_NO_CHECKBOX_RE = re.compile(r"^[-*]\s*\[T\d+\]")


def task_format_violations(tasks: str) -> list[str]:
    """Return a flat list of unambiguous, mechanically-detected §T5 format
    violations — currently just the bracketed-ID-instead-of-checkbox case
    (`- [T001] ...`) — precise enough to auto-kick-back without LLM
    judgment. Deliberately conservative: false negatives here just mean the
    LLM critic catches it on the next iteration as before; false positives
    would waste an iteration reformatting narrative prose that was never
    broken, which is worse. An empty list means nothing unambiguous was
    found to gate on — not that the file is definitely §T5-clean."""
    violations = []
    for i, line in enumerate(tasks.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _BRACKETED_ID_NO_CHECKBOX_RE.match(stripped):
            violations.append(
                f"  Line {i}: {stripped[:100]} ← MISSING checkbox (`- [ ]`/`- [x]` must "
                f"immediately precede the task ID, e.g. `- [ ] T001 ...`, not `- [T001] ...`)"
            )
    return violations


# --- Red-state artifact validity (§TQ2) ---------------------------------

# Signatures that mean the test run never reached a real assertion — the
# test environment failed, not the test. These must never be accepted as a
# genuine red-state capture. Compiled case-insensitive since tooling casing
# varies (ECONNREFUSED vs econnrefused in some runners' JSON output).
_INFRA_FAILURE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ECONNREFUSED",
        r"ENOTFOUND",
        r"ETIMEDOUT",
        r"connection refused",
        r"could not connect",
        r"cannot connect",
        r"no tests found",
        r"no test files found",
        r"0 tests? (?:ran|passed|failed)",
    )
]

# A genuine red-state capture contains evidence the test actually ran and
# asserted something, or failed to resolve the not-yet-implemented target —
# both are legitimate TDD red states.
_GENUINE_FAILURE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"assertionerror",
        r"expect(?:ed)?.{0,40}(?:to (?:be|equal|contain)|received)",
        r"cannot find module",
        r"module not found",
        r"is not a function",
        r"is not defined",
        r"\d+ (?:failed|failing)",
    )
]


def red_state_issue(artifact_text: str) -> str | None:
    """Return a description of why this red-state artifact is invalid, or
    None if it looks like a genuine failing-assertion/module-not-found
    capture. Checked in this order: an infra-failure signature with no
    accompanying genuine-failure signature is rejected outright; an
    artifact with neither signature (e.g. a clean pass, or empty output)
    is rejected as inconclusive."""
    has_infra = any(p.search(artifact_text) for p in _INFRA_FAILURE_PATTERNS)
    has_genuine = any(p.search(artifact_text) for p in _GENUINE_FAILURE_PATTERNS)

    if has_infra and not has_genuine:
        return (
            "artifact shows an infrastructure failure (e.g. connection refused, "
            "service unreachable, no tests found) with no genuine assertion "
            "failure — the test environment did not run, not the test"
        )
    if not has_infra and not has_genuine:
        return (
            "artifact shows neither an infrastructure-failure signature nor a "
            "genuine assertion/module-not-found failure signature — cannot "
            "confirm this is a real red state (it may be a passing run "
            "mislabeled as red-state, or empty output)"
        )
    return None


def validate_red_state_artifacts(test_results_dir: Path) -> dict[str, str]:
    """Check every *-red.txt artifact under test_results_dir. Returns a dict
    of {filename: issue_description} for artifacts that fail validation.
    Empty dict means all artifacts are valid (or none exist to check)."""
    issues: dict[str, str] = {}
    if not test_results_dir.exists():
        return issues
    for artifact in sorted(test_results_dir.glob("*-red.txt")):
        text = artifact.read_text(encoding="utf-8", errors="replace")
        issue = red_state_issue(text)
        if issue:
            issues[artifact.name] = issue
    return issues


# --- Commit hygiene -------------------------------------------------------

DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5MB


def oversized_staged_files(max_bytes: int = DEFAULT_MAX_FILE_BYTES) -> list[tuple[str, int]]:
    """Return [(path, size_bytes)] for any currently staged file exceeding
    max_bytes. Uses `git diff --cached --name-only` so this only looks at
    what is about to be committed, not the whole working tree."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    oversized = []
    for name in result.stdout.splitlines():
        path = Path(name)
        if not path.exists():
            continue
        size = path.stat().st_size
        if size > max_bytes:
            oversized.append((name, size))
    return oversized


def oversized_committed_files(
    base_ref: str = "main", max_bytes: int = DEFAULT_MAX_FILE_BYTES
) -> list[tuple[str, int]]:
    """Return [(path, size_bytes)] for any file changed on this branch
    relative to its merge-base with base_ref that currently exceeds
    max_bytes on disk. Catches an oversized file (accidental binary, core
    dump, build artifact) already committed by an earlier agent step,
    before the pipeline stage is marked complete. Returns [] (not an error)
    if base_ref doesn't exist or this isn't a git repo — callers should
    treat an empty result as "nothing to flag", not "definitely clean"."""
    merge_base = subprocess.run(
        ["git", "merge-base", base_ref, "HEAD"],
        capture_output=True,
        text=True,
    )
    if merge_base.returncode != 0:
        return []
    base_sha = merge_base.stdout.strip()

    diff = subprocess.run(
        ["git", "diff", "--name-only", base_sha, "HEAD"],
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        return []

    oversized = []
    for name in diff.stdout.splitlines():
        path = Path(name)
        if not path.exists():
            continue
        size = path.stat().st_size
        if size > max_bytes:
            oversized.append((name, size))
    return oversized
