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

from agent_common.git import resolve_base_ref
from agent_common.project_conventions import looks_like_test_file

# A compliant task line is a markdown checkbox (`- [ ]`, `- [x]`, or `- [X]`)
# immediately followed by a bare, unbracketed task ID (`T001`, not `[T001]`).
# This is not cosmetic: ch_3_test_auto.py and ch_4_implement_auto.py scan for
# the literal substring "- [ ]" to detect which tasks still need work, so a
# task line missing the checkbox is silently treated as already complete and
# permanently skipped by those downstream orchestrators.
_CHECKBOX_TXXX_RE = re.compile(r"^[-*]\s+\[[ xX]\]\s*T\d+\b")
_BARE_TXXX_RE = re.compile(r"\bT\d+\b")
# A bullet that opens with a task ID but lacks the checkbox (`- T001 ...`,
# `- [T001] ...`). Prose bullets that merely cite an ID mid-sentence are not tasks.
_LEADING_TXXX_RE = re.compile(r"^[-*]\s+\**\[?T\d+\b")

# The one statement of the task tag grammar. The generator, fix and critic prompts
# embed it, and a unit test keeps the copies in the skills and the tasks template
# verbatim: five hand-written versions drifted apart and the tasks critic flapped
# between them (27 self-reversing iterations across the 024-098 runs).
_TASK_TAG_GRAMMAR = """\
- `[TEST]`: the task writes failing tests only, no implementation code.
- `[IMPL]`: the task writes production code to pass its paired `[TEST]` task's tests and names that task (e.g. "depends on T010"). An `[IMPL]` task with no testable behaviour (a schema migration, infrastructure) says so instead: "no test required — <reason>".
- No type tag: a task with no testable deliverable of its own — setup, config, orientation, read-and-confirm. The implement stage still does it, and it needs no paired task.
- `[MANUAL]`, alone and never with `[TEST]` or `[IMPL]`: work an agent cannot do inside the repo — waiting for a human approval, a live/cloud deploy, a real external account, a manual verification checklist, an evaluation run. Agents never perform or check off `[MANUAL]` tasks, so every such task must carry it, and no task that writes repo files may.
- `[P]`: the task can run in parallel (different files, no dependency on an incomplete task).
- `[USn]`: the user story a `[TEST]`/`[IMPL]` task in a user-story phase serves; `[US1, US2]` for one pair serving several. Setup, Foundational and Polish tasks carry none.
- No other tags. Work another pipeline stage owns (docs, deployed validation) is not a task."""

_NO_TEST_STAGE_TYPE_TAGS = """\
- `[IMPL]`: the task writes production code. The test stage is disabled, so there are no `[TEST]` tasks and no pairing.
- No type tag: a task with no deliverable of its own — setup, config, orientation, read-and-confirm. The implement stage still does it."""


def task_tag_grammar(test_stage_enabled: bool = True) -> str:
    if test_stage_enabled:
        return _TASK_TAG_GRAMMAR
    lines = _TASK_TAG_GRAMMAR.splitlines()
    return "\n".join([_NO_TEST_STAGE_TYPE_TAGS, *lines[3:]])


# The grammar above, as code. A task with no testable deliverable
# (setup, config, orientation) carries no type tag, and only [TEST]/[IMPL] tasks
# in a user-story phase need a story label. Flagging every other line made the
# tasks critic flap: it demanded [TEST]/[IMPL] on [MANUAL] tasks and [USn] on
# Setup/Polish tasks, then the reverse (032, 033, 049, 072 and ~15 others).
_TYPE_TAGS = {"[TEST]", "[IMPL]"}
_KNOWN_TAGS = _TYPE_TAGS | {"[MANUAL]", "[P]"}
_STORY_LABEL_RE = re.compile(r"\[US\d+(?:\s*[,/+]\s*US\d+)*\]")
# Tasks live under `## Phase N: ...` headings; bullets in the Dependencies,
# Parallel Opportunities and Notes sections are prose that cites task IDs.
_PHASE_HEADING_RE = re.compile(r"(?im)^## .*\bphase\b")
_NON_STORY_PHASE_RE = re.compile(r"(?i)^## .*\b(setup|foundation(al)?|polish)\b")


def _classify_txxx_issues(stripped: str, story_phase: bool) -> list[str]:
    tags = task_tags(stripped)
    issues = []
    if "[MANUAL]" in tags and tags & _TYPE_TAGS:
        issues.append("[MANUAL] combined with [TEST]/[IMPL]")
    if story_phase and tags & _TYPE_TAGS and not _STORY_LABEL_RE.search(stripped):
        issues.append("MISSING [USX] story label")
    return issues


def _record_txxx_result(
    stripped: str,
    i: int,
    story_phase: bool,
    complete_tasks: list[str],
    incomplete_tasks: list[str],
) -> None:
    entry = f"  Line {i}: {stripped[:100]}"
    issues = _classify_txxx_issues(stripped, story_phase)
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

    has_phases = bool(_PHASE_HEADING_RE.search(tasks))
    in_phase = not has_phases
    story_phase = True
    for i, line in enumerate(tasks.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("## "):
            in_phase = not has_phases or bool(_PHASE_HEADING_RE.match(stripped))
            story_phase = not _NON_STORY_PHASE_RE.match(stripped)
        if not stripped or stripped.startswith("#"):
            continue
        if not in_phase:
            bullets.append(f"  Line {i}: {stripped[:100]}")
            continue
        if re.match(r"^\d+\.", stripped):
            numbered.append(f"  Line {i}: {stripped[:100]}")
        elif stripped.startswith(("- ", "* ")):
            if _CHECKBOX_TXXX_RE.match(stripped):
                _record_txxx_result(stripped, i, story_phase, complete_tasks, incomplete_tasks)
            elif _LEADING_TXXX_RE.match(stripped):
                incomplete_tasks.append(
                    f"  Line {i}: {stripped[:100]} ← MISSING checkbox "
                    f"(`- [ ]`/`- [x]` must immediately precede the bare task ID, "
                    f"e.g. `- [ ] T001 ...`, not `- [T001] ...`)"
                )
            else:
                bullets.append(f"  Line {i}: {stripped[:100]}")
        elif _CHECKBOX_TXXX_RE.match(stripped) or _BARE_TXXX_RE.match(stripped):
            _record_txxx_result(stripped, i, story_phase, complete_tasks, incomplete_tasks)

    return complete_tasks, incomplete_tasks, numbered, bullets


# A task ID cited in a finding — not a §T rule number like "§T5".
_CITED_TASK_ID_RE = re.compile(r"(?<![§\w])T\d+\b")
_ENTRY_TASK_ID_RE = re.compile(r"\[[ xX]\]\s*(T\d+)\b")
# The one §T5 call the code leaves to the critic: whether a task is out-of-repo
# work, so it must (or must not) be [MANUAL].
_MANUAL_JUDGEMENT_RE = re.compile(
    r"(?i)((missing|lacks?|without|add|needs?|requires?|should (be|carry)|must (be|carry))"
    r"\s+(tagged\s+)?(a\s+|the\s+)?`?\[MANUAL\]"
    r"|(must not|should not|shouldn't|not) (be )?(tagged )?`?\[MANUAL\]"
    r"|(remove|drop)\s+(the\s+)?`?\[MANUAL\])"
)


def discard_accepted_format_findings(result: dict, tasks: str) -> list[dict]:
    """Drop a critic's §T5 findings that only cite tasks the format analysis
    accepts, other than a [MANUAL] judgement, and return what was dropped. A
    FAIL left with no BLOCKING finding becomes a PASS. The critic still
    demanded [TEST]/[IMPL] on [MANUAL] tasks and flagged prose bullets after
    being told the analysis was authoritative (031, 032, 048, 071)."""
    complete, _, _, _ = classify_task_lines(tasks)
    well_formed = {m.group(1) for line in complete if (m := _ENTRY_TASK_ID_RE.search(line))}
    kept, dropped = [], []
    for v in result.get("violations", []):
        text = f"{v.get('location', '')} {v.get('finding', '')}" if isinstance(v, dict) else ""
        cited = set(_CITED_TASK_ID_RE.findall(text))
        if (
            str(v.get("rule", "") if isinstance(v, dict) else "").startswith("§T5")
            and cited
            and cited <= well_formed
            and not _MANUAL_JUDGEMENT_RE.search(text)
        ):
            dropped.append(v)
        else:
            kept.append(v)
    if dropped:
        result["violations"] = kept
        result["discarded"] = result.get("discarded", []) + dropped
        if result.get("status") == "FAIL" and not any(
            isinstance(v, dict) and v.get("severity") == "BLOCKING" for v in kept
        ):
            result["status"] = "PASS"
    return dropped


def analyze_task_format(tasks: str) -> str:
    """Human-readable breakdown used as critic-prompt context."""
    complete_tasks, incomplete_tasks, numbered, bullets = classify_task_lines(tasks)

    parts = []
    if complete_tasks:
        parts.append("Well-formed tasks (no format problem found):\n" + "\n".join(complete_tasks))
    if incomplete_tasks:
        parts.append("Txxx tasks that VIOLATE §T5:\n" + "\n".join(incomplete_tasks))
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


# Phrasing that only ever describes work an agent can't do. Kept narrow for
# the same false-positive reason as above; validated against the specs/001,
# 003, 080, 081, 083 tasks.md files, where every untagged hit was a task that
# later blocked ch_4_implement_auto's completion gate until a human tagged it.
_MANUAL_PHRASE_RE = re.compile(
    r"(?i)\b(manual(ly)?\b|wait(ing)? for (explicit )?(human )?approval|human approval"
    r"|live[\s`]+(cdk )?deploy|real (aws|dev) account)"
)


def task_format_violations(tasks: str) -> list[str]:
    """Return a flat list of unambiguous, mechanically-detected §T5 format
    violations — the bracketed-ID-instead-of-checkbox case (`- [T001] ...`),
    an unknown tag (051's `[non-deliverable]`, 040's `[FOUNDATION]`), `[MANUAL]`
    combined with `[TEST]`/`[IMPL]` (033, 047, 057: a task no stage can finish),
    and an unchecked, non-[TEST] task described as human/out-of-repo work but
    missing its [MANUAL] tag — precise enough to auto-kick-back without LLM
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
            continue
        tags = task_tags(stripped)
        unknown = sorted(
            t for t in tags if t not in _KNOWN_TAGS and not _STORY_LABEL_RE.fullmatch(t)
        )
        if unknown:
            violations.append(
                f"  Line {i}: {stripped[:100]} ← unknown tag {', '.join(unknown)} (use only "
                f"[TEST], [IMPL], [MANUAL], [P] and [USn]; a task with no testable deliverable "
                f"carries no type tag)"
            )
        if "[MANUAL]" in tags and tags & _TYPE_TAGS:
            violations.append(
                f"  Line {i}: {stripped[:100]} ← [MANUAL] combined with [TEST]/[IMPL] (no agent "
                f"may check off a [MANUAL] task, so the test/implement stage can never finish it; "
                f"keep [MANUAL] alone for out-of-repo work, or drop it if the task writes repo files)"
            )
        elif (
            _UNCHECKED_TXXX_RE.match(stripped)
            and not tags & {"[MANUAL]", "[TEST]"}
            and _MANUAL_PHRASE_RE.search(stripped)
        ):
            violations.append(
                f"  Line {i}: {stripped[:100]} ← MISSING [MANUAL] tag (task describes "
                f"human or out-of-repo work an agent cannot complete)"
            )
    return violations


_HEADING_RE = re.compile(r"^#{2,3} .+$", re.MULTILINE)
_TASK_LINE_RE = re.compile(r"^[-*]\s+\[[ xX]\]\s*T\d+\b", re.MULTILINE)


def revision_regressions(before: str, after: str) -> list[str]:
    """What a revision of plan.md/tasks.md destroyed rather than fixed: no
    change at all (specs/005's reviser printed tasks instead of writing them),
    removed sections (specs/082's plan revision dropped the Constitution Check
    table), or collapsed tasks (specs/001 and 004 rewrote tasks.md without its
    phases or [TEST]/[IMPL] tasks)."""
    if after == before:
        return ["the file was not changed — write the revised file to disk"]
    problems = [
        f"removed section `{heading}` — keep every existing section"
        for heading in _HEADING_RE.findall(before)
        if heading not in after
    ]
    tasks_before = len(_TASK_LINE_RE.findall(before))
    tasks_after = len(_TASK_LINE_RE.findall(after))
    if tasks_after < tasks_before / 2:
        problems.append(
            f"task count dropped from {tasks_before} to {tasks_after} — revise tasks in place, "
            "do not replace them"
        )
    return problems


_PENDING_APPROVALS_RE = re.compile(
    r"^## Pending Human Approvals\s*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
)


_NONE_PENDING_RE = re.compile(r"(?i)^[*_]*(none|no open items|nothing pending)\b")


def pending_human_approvals(plan: str) -> list[str]:
    """The bullets under plan.md's "## Pending Human Approvals" section —
    governance amendments only a human may apply. specs/001, 005 and 006 each
    generated tasks while such amendments were still pending, and the tasks
    critic then blocked on them every iteration."""
    m = _PENDING_APPROVALS_RE.search(plan)
    if not m:
        return []
    body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.DOTALL)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    # The plan agent writes "None." and then lists informational notes or
    # already-ratified amendments beneath it (030, 098); the bullets then aren't pending.
    if lines and _NONE_PENDING_RE.match(lines[0]):
        return []
    return [line for line in lines if line.startswith(("- ", "* "))]


# Anchored on the checkbox + task ID starting the (stripped) line, mirroring
# _CHECKBOX_TXXX_RE above. A naive "- [ ]" substring scan false-positives on
# prose that merely *mentions* the checkbox/tag syntax as an example (e.g.
# tasks.md documenting the non-deliverable-task convention: "keeps its
# `- [ ] T002` checkbox and ID, omits only the `[TEST]`/`[IMPL]` tag") —
# such a line contains both "- [ ]" and "[TEST]" as substrings without being
# an actual unchecked task, which caused ch_3_test_auto.py to treat a fully
# completed test phase as having outstanding [TEST] work.
_UNCHECKED_TXXX_RE = re.compile(r"^[-*]\s+\[ \]\s*T\d+\b")

# The tag block is the run of bracketed tags right after the task ID
# (`T005 [IMPL] [P] [US1]`, sometimes bolded as `**[MANUAL]**`). Tags are only
# matched there: specs/005 had an [IMPL] line whose prose said "no separate
# [TEST] task", which a substring scan counted as an outstanding [TEST] task.
_TAG = r"\[[\w-]+(?:\s*[,/+]\s*[\w-]+)*\]"
_TASK_TAG_BLOCK_RE = re.compile(rf"^[-*]\s+\[[ xX]\]\s*T\d+\b((?:\s*\**{_TAG}\**)*)")
_TAG_RE = re.compile(_TAG)


def task_tags(line: str) -> set[str]:
    """The tags (e.g. {"[TEST]", "[P]", "[US1]"}) in a task line's tag block."""
    m = _TASK_TAG_BLOCK_RE.match(line.strip())
    return set(_TAG_RE.findall(m.group(1))) if m else set()


def unchecked_task_lines(
    tasks: str, tag: str | None = None, exclude_tag: str | None = None
) -> list[str]:
    """Return every unchecked task line (`- [ ] Txxx ...`) in tasks.md,
    optionally filtered to lines tagged `tag` (e.g. "[TEST]") and/or excluding
    lines tagged `exclude_tag` (e.g. "[MANUAL]"); see task_tags. Centralizes
    the task-completion scan that ch_3_test_auto.py and ch_4_implement_auto.py
    each used independently to detect outstanding work, so preflight checks
    and post-agent verification can't drift out of sync with each other.

    `exclude_tag="[MANUAL]"` exists because the tasks-agent has, across
    multiple features (e.g. 054, 055, 061), used a `[MANUAL]` tag on tasks
    whose task text itself says an agent cannot perform them (out-of-repo,
    human-only actions like a Chrome Web Store Dashboard update). Before this
    parameter existed, ch_4_implement_auto.py's completion gate required
    every such task to be checked off before it would even attempt the
    critic/quality-review/CI gates — something no agent could ever satisfy,
    permanently blocking the stage."""
    return [
        line
        for line in tasks.splitlines()
        if _UNCHECKED_TXXX_RE.match(line.strip())
        and (tag is None or tag in task_tags(line))
        and (exclude_tag is None or exclude_tag not in task_tags(line))
    ]


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
        r"expect(?:ed)?[\s\S]{0,120}(?:to\s*(?:be|equal|contain)|received)",
        r"cannot find module",
        r"module not found",
        r"is not a function",
        r"is not defined",
        r"\d+ (?:failed|failing)",
    )
]


# An artifact that declares this records a deliberately PASSING run of a
# no-regression test (a constitution "regression-guard exception"). Every
# artifact the check rejected in specs/087, 016 and 017 was one, which cost two
# wasted kickback sessions each; whether the exception is justified is the
# test critic's call, not a pattern match's.
_REGRESSION_GUARD_RE = re.compile(r"(?i)regression[- ]guard exception")
_REGRESSION_GUARD_HEADER_CHARS = 2000


def red_state_issue(artifact_text: str) -> str | None:
    """Return a description of why this red-state artifact is invalid, or
    None if it looks like a genuine failing-assertion/module-not-found
    capture, or a declared regression-guard capture that ran. Checked in this
    order: an infra-failure signature with no accompanying genuine-failure
    signature is rejected outright; an artifact with neither signature (e.g. a
    clean pass, or empty output) is rejected as inconclusive unless its header
    declares a regression-guard exception."""
    has_infra = any(p.search(artifact_text) for p in _INFRA_FAILURE_PATTERNS)
    has_genuine = any(p.search(artifact_text) for p in _GENUINE_FAILURE_PATTERNS)
    declares_guard = bool(
        _REGRESSION_GUARD_RE.search(artifact_text[:_REGRESSION_GUARD_HEADER_CHARS])
    )

    if has_infra and not has_genuine:
        return (
            "artifact shows an infrastructure failure (e.g. connection refused, "
            "service unreachable, no tests found) with no genuine assertion "
            "failure — the test environment did not run, not the test"
        )
    if not has_infra and not has_genuine and not declares_guard:
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


DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024


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
    resolved_ref = resolve_base_ref(base_ref)
    merge_base = subprocess.run(
        ["git", "merge-base", resolved_ref, "HEAD"],
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


# A test case's declaration or one of its assertions (JS/TS and Python). Setup
# lines (mocks, fixtures, helpers) don't match: the implementation stage may
# change those, but never a test case.
_TEST_CASE_LINE_RE = re.compile(
    r"(?<![.\w])(?:it|test|describe)(?:\.\w+)*\s*\(|\bexpect\s*\(|^\s*def test_\w+|^\s*assert\b"
)
_TEST_PHASE_SUBJECT_RE = re.compile(r"^test(\(.*\))?:")


def _git_out(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def _test_case_lines(diff: str, existing: set[str]) -> dict[str, list[str]]:
    edits: dict[str, list[str]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
        elif line.startswith(("---", "+++")) or not line.startswith(("-", "+")):
            continue
        elif path in existing and _TEST_CASE_LINE_RE.search(line[1:]):
            edits.setdefault(path, []).append(line[:160])
    return edits


def test_case_edits(base: str) -> dict[str, list[str]]:
    """Test-case lines added or removed, in test files that already existed at
    `base`, by any commit since `base` that isn't a test-phase commit (subject
    `test: ...`), or by uncommitted changes. A feature that changes behaviour an
    existing test pins needs that test updated in the test phase; the
    implementation stage editing it breaks the constitution's TDD rule, and the
    implement critic then failed three iterations on it with no way out (043,
    057, 060, 066)."""
    existing = {
        p
        for p in _git_out("ls-tree", "-r", "--name-only", base).splitlines()
        if looks_like_test_file(p)
    }
    if not existing:
        return {}
    edits: dict[str, list[str]] = {}
    for entry in _git_out("log", "--format=%H %s", f"{base}..HEAD").splitlines():
        sha, _, subject = entry.partition(" ")
        if _TEST_PHASE_SUBJECT_RE.match(subject):
            continue
        for path, lines in _test_case_lines(
            _git_out("show", "-U0", "--format=", sha), existing
        ).items():
            edits.setdefault(path, []).extend(lines)
    for path, lines in _test_case_lines(_git_out("diff", "-U0", "HEAD"), existing).items():
        edits.setdefault(path, []).extend(lines)
    return edits


# Failures of the machine CI runs on, not of the code: a database that isn't
# up, a full disk. 10 of the 16 features whose ch-4 CI failed matched one, and
# each went to the code-fix agent anyway. Narrower than _INFRA_FAILURE_PATTERNS:
# a CI run's ENOTFOUND or timeout is as often the code's fault.
_CI_ENVIRONMENT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ECONNREFUSED",
        r"connection refused",
        r"could not connect to (?:server|database)",
        r"ENOSPC",
        r"no space left on device",
    )
]


def ci_environment_failure(output: str) -> str | None:
    """The first output line showing the CI environment, not the code, failed."""
    for line in output.splitlines():
        if any(p.search(line) for p in _CI_ENVIRONMENT_PATTERNS):
            return line.strip()[:200]
    return None


# Vitest's line for a file whose every test was skipped, optionally with a
# workspace project label: `↓ |api| tests/db/x.test.ts (4 tests | 4 skipped)`.
_WHOLLY_SKIPPED_FILE_RE = re.compile(
    r"↓\s+(?:\|[^|]*\|\s+)?(\S+)\s+\((\d+) tests?\s*\|\s*\2 skipped\)"
)


def wholly_skipped_test_files(output: str) -> list[str]:
    """Test files a passing run skipped entirely — almost always a suite gated
    on a service or variable the run lacked. 045's ch-4 CI skipped 310
    Postgres-backed tests for want of DATABASE_URL and reported PASS while one
    of them was failing."""
    return sorted({m.group(1) for m in _WHOLLY_SKIPPED_FILE_RE.finditer(output)})
