#!/usr/bin/env python3
"""
.claude/agents/ch_4_implement_auto.py

Agentic orchestrator for automated implementation and critic loop.
Run manually after tasks.md has been reviewed and is ready for implementation.
Runs independently of any Claude Code interactive session.

Usage:
  python .claude/agents/ch_4_implement_auto.py
  python .claude/agents/ch_4_implement_auto.py --feature 015-job-description-rich-text

Requirements:
  pip install claude-agent-sdk

The script derives the feature from the current git branch if --feature
is not supplied, matching the behaviour of the speckit skills.

Loop structure per iteration:
  1. Implement critic  — validates task traceability, TDD, layer separation, coverage, etc.
  2. Code quality review — validates code quality, maintainability, and operational safety
  Both gates must PASS in the same iteration before implementation is committed.

Resume behaviour:
  Re-running after an interruption continues from the last incomplete step:
  - Result files act as idempotency markers: a gate is skipped if its result already exists
  - If the last critic result was FAIL, fix agent runs before the next critic
  - If critic PASS but quality review not yet run, resumes at the same iteration number
  - If the last quality result was FAIL, fix agent runs before the next critic
  - Implementation agent is re-run only if unchecked tasks (- [ ]) remain in tasks.md
"""

import functools
import json
import shutil
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path
from typing import NamedTuple

from ch_4_implement_critic import build_implement_critic_prompt
from ch_4_implement_quality_critic import build_quality_review_prompt
from claude_agent_sdk import AgentDefinition

from agent_common import git, local_agent_loop, ollama, pipeline_status
from agent_common.console import HUMAN_ACTION_EXIT_CODE, make_logger, setup_log_file
from agent_common.critic_loop import (
    claude_gate,
    confirm_review_existing,
    finish_stage,
    run_cli,
    run_two_gate_loop,
)
from agent_common.driving_agent import (
    FIX_AGENT_GIT_AND_EXTERNAL_SYSTEM_GUARDRAILS,
    HUMAN_REVIEW_ATTESTATION_GUARDRAIL,
)
from agent_common.files import CONSTITUTION_PATH, read_file, read_memory, require_spec_files
from agent_common.followup import record_non_blocking_concerns
from agent_common.preflight_checks import (
    ci_environment_failure,
    oversized_committed_files,
    test_case_edits,
    unchecked_task_lines,
    wholly_skipped_test_files,
)
from agent_common.project_conventions import is_slow_check, resolve_ci_commands
from agent_common.resume_state import (
    apply_escalation_review,
    find_passing_iteration,
    find_two_gate_resume_state,
    next_iteration,
    read_result,
    stage_dirty_baseline,
    stage_is_complete,
    stage_start_commit,
)

AGENT_NAME = "ch-4-implement-auto"
TEST_UPDATES_FILENAME = "test-updates-needed.md"
# Below this, a CI run fails on the disk, not the code (three features ran out
# of /tmp to leftover cdk.out directories).
MIN_FREE_DISK_BYTES = 2 * 1024**3
CRITIC_RESULT_PREFIX = "ch-4-implement-critic-result"
QUALITY_RESULT_PREFIX = "ch-4-implement-code-quality-review-result"
log = make_logger(AGENT_NAME)


def preflight(spec_dir: Path, feature: str):
    require_spec_files(log, spec_dir, "spec.md", "plan.md", "tasks.md")

    if ollama.is_stage_enabled("test"):
        test_quality_results = list(spec_dir.glob("ch-3-test-quality-review-result-*.json"))
        if not test_quality_results:
            log("ERROR: Test phase not complete. Run /ch-3-test-auto first.")
            sys.exit(1)
        passing = find_passing_iteration(spec_dir, "ch-3-test-quality-review-result")
        if passing is None:
            log(
                "ERROR: No passing test-quality-review result found. Run /ch-3-test-auto to resolve."
            )
            sys.exit(1)
    else:
        log(
            "Test stage is disabled (.specify/pipeline.json stages.test.enabled=false) — "
            "skipping test-gate check."
        )

    tasks_content = (spec_dir / "tasks.md").read_text(encoding="utf-8")
    all_done = not unchecked_task_lines(tasks_content, exclude_tag="[MANUAL]")
    if all_done:
        confirm_review_existing(
            log, AGENT_NAME, spec_dir, CRITIC_RESULT_PREFIX, "All tasks in tasks.md"
        )


def impl_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    quality_principles: str,
    model: str | None = None,
    test_stage_enabled: bool = True,
) -> AgentDefinition:
    load_test_artifact_step = (
        "1. Load the paired [TEST] task's red-output artifact from specs/*/test-results/<PAIRED-TASKID>-red.txt\n"
        "2. Read the test file(s) already written by the test phase for this behaviour\n"
        "3. Write the minimum implementation to make the failing tests pass\n"
        "4. Run the relevant test suite to confirm green"
        if test_stage_enabled
        else (
            "1. Skip this step — the test stage is disabled for this project "
            "(.specify/pipeline.json stages.test.enabled=false), so no paired [TEST] "
            "task or red-output artifact exists\n"
            "2. Write the implementation for this task's behaviour, per spec.md/plan.md\n"
            "3. Run this project's typecheck/build command to confirm it's sound"
        )
    )
    test_updates_rule = (
        "\nIf an existing test fails because this feature deliberately changes the behaviour it "
        "asserts, leave it failing: add `- <test file>: <test name>: <what the spec changed>` "
        f"to {TEST_UPDATES_FILENAME} in the feature's specs/ directory and carry on. The test "
        "phase owns that change."
        if test_stage_enabled
        else ""
    )
    tests_intro = (
        "Tests have already been written and confirmed failing by the test phase."
        if test_stage_enabled
        else "The test stage is disabled for this project — there are no pre-written failing tests."
    )
    return AgentDefinition(
        description="Implements all unchecked [IMPL] tasks in tasks.md.",
        model=model,
        prompt=f"""You are the Implementation Agent for a spec-kit project.

{tests_intro}
Your sole function is to implement all unchecked [IMPL] tasks (marked - [ ] and containing [IMPL]) in tasks.md, in order.
Also process any unchecked tasks with no [TEST]/[IMPL] label (infrastructure/setup tasks).

DO NOT write new test files. DO NOT modify existing test files.{test_updates_rule}

For each [IMPL] task:
{load_test_artifact_step}
5. Commit the implementation: git add <impl files> && git commit -m "feat: <task description>"
6. Mark the task as - [x] in tasks.md and commit: git add specs/.../tasks.md && git commit -m "chore: complete <task id>"

Inputs already loaded for you (this project's constitution.md is human-customized, so any
section number referenced below may not match — locate the section by heading text instead):

--- CONSTITUTION ---
{constitution}

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

--- CODE QUALITY PRINCIPLES ---
{quality_principles}

Key rules:
- Read the full tasks.md to understand all tasks and their dependencies before starting
- Respect task order; honour [P] markers (parallel tasks may be done in any order relative to each other)
- Use only the approved stack and respect the layer-separation boundaries defined in the CONSTITUTION above — do not introduce a dependency, framework, or architectural pattern it doesn't cover
- No new dependencies beyond what the constitution's approved stack allows
- Any call whose result does not gate the primary operation's success/failure — a
  best-effort side effect like a badge refresh, telemetry ping, or cache warm — must be
  wrapped in its own try/catch that cannot propagate into or mask the primary operation's
  result. A failure in a best-effort side effect must never be reported to the user as a
  failure of the primary operation it accompanied.
- After each task, run this project's typecheck command as declared in constitution §12 (CI Requirements)
- The code will be evaluated by a Code Quality Review agent using the principles above — implement accordingly
- Do not stop until all - [ ] tasks are marked - [x] in tasks.md
{HUMAN_REVIEW_ATTESTATION_GUARDRAIL}
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def ci_fix_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    ci_failures: str,
    model: str | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        description="Fixes failing CI checks (typecheck, unit tests, e2e) after both review gates passed.",
        model=model,
        prompt=f"""You are the CI Fix Agent for a spec-kit project.

Both the implement critic and code quality review have passed. However, one or more of
this project's CI checks (see the constitution's CI Requirements section, §12) failed.
Your sole function is to fix those failures.

Fix only what is broken by the failures below. Do not add features, do not refactor
passing code, do not change passing test assertions.

--- CI FAILURES ---
{ci_failures}

--- CONSTITUTION --- (this project's constitution.md is human-customized, so any section
number referenced above/below may not match — locate the section by heading text instead)
{constitution}

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

Key rules:
- Read every file mentioned in the failure output before modifying it
- Apply the minimum change that makes the failing check pass
- After all fixes, re-run each failing check listed above (its exact command is defined in
  constitution §12 CI Requirements) to confirm it now passes
- Commit all fixed files: git add <files> && git commit -m "fix: address CI failures"
- Do not stop until every failing check listed above passes

{FIX_AGENT_GIT_AND_EXTERNAL_SYSTEM_GUARDRAILS}
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def fix_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    violations: list,
    model: str | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        description="Fixes specific implementation violations found by the critic or quality review.",
        model=model,
        prompt=f"""You are the Fix Agent for a spec-kit project.

Your sole function is to fix the specific violations listed below.
Fix only what is listed. Do not add features. Do not change passing test assertions.

--- VIOLATIONS TO FIX ---
{json.dumps(violations, indent=2)}

--- CONSTITUTION --- (this project's constitution.md is human-customized, so any section
number referenced above/below may not match — locate the section by heading text instead)
{constitution}

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

Key rules:
- Read the file at each violation's location before modifying it
- Apply the minimum change that addresses the violation
- If a violation genuinely requires a new or modified test to pin down a regression, you MAY
  add one — this is a sanctioned exception (see constitution §18, "Regression tests during a
  fix cycle") — but you MUST follow red-green discipline: write the test first, run it and
  confirm it FAILS for the expected reason, save the failing output to
  specs/*/test-results/<id>-red.txt (same convention as the test phase), THEN apply the code
  fix and confirm the test goes green. State in the commit message that this is a sanctioned
  regression-test-during-fix. Do not add a test without first recording its red state.
- After all fixes, run this project's typecheck command as declared in constitution §12 (CI Requirements)
- Run the relevant test suite to confirm nothing is broken
- Before committing, re-check your diff against every item in the violations list above and
  account for each one explicitly — do not stop after addressing only the newest or most
  prominent finding
- Commit fixed files: git add <files> && git commit -m "fix: address violations"
- Do not stop until every violation in the list is addressed and committed

{FIX_AGENT_GIT_AND_EXTERNAL_SYSTEM_GUARDRAILS}
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def _build_ci_commands(*, include_slow: bool) -> list[tuple[str, list[str]]]:
    """Every CI check the project declared (constitution §12 / README.md), in its
    own words — not forced into a fixed typecheck/lint/unit/e2e taxonomy. The quick
    pre-critic gate excludes anything that looks e2e/integration-shaped (see
    is_slow_check); the full gate after both review gates pass runs everything."""
    checks = resolve_ci_commands()
    if include_slow:
        return checks
    return [(label, cmd) for label, cmd in checks if not is_slow_check(label)]


_SHELL_METACHARACTERS = ("&&", "||", ";", "|", ">", "<")


# A failing e2e run's output is mostly accumulated dev-server noise (86% of
# specs/086's 1.7 MB log); the failure summary is at the end. Keeping only the
# tail keeps both the log and the fix agent's prompt readable.
_CI_OUTPUT_TAIL_LINES = 200
_CI_OUTPUT_TAIL_CHARS = 20_000


def _tail(output: str) -> str:
    lines = output.splitlines()
    tail = "\n".join(lines[-_CI_OUTPUT_TAIL_LINES:])[-_CI_OUTPUT_TAIL_CHARS:]
    if len(tail) == len(output):
        return output
    return f"[... {len(output) - len(tail)} earlier characters omitted ...]\n{tail}"


class CiRun(NamedTuple):
    passed: bool
    failures: str
    environment_failure: str | None = None
    skipped_files: tuple[str, ...] = ()


def _run_commands(commands: list[tuple[str, list[str]]]) -> CiRun:
    all_passed = True
    failures: list[str] = []
    environment_failure = None
    skipped: set[str] = set()
    for label, cmd in commands:
        log(f"CI: running {label}...")
        # A command parsed from a single constitution.md/README.md bullet (e.g.
        # "pnpm test:backend && pnpm test:frontend") comes through shlex.split as
        # a flat argv list where "&&" is just another token — subprocess.run(cmd)
        # would pass it as a literal CLI argument rather than a shell operator.
        # Re-join and run through the shell whenever a shell metacharacter is present.
        needs_shell = any(tok in _SHELL_METACHARACTERS for tok in cmd)
        # Plain space-join, not shlex.join: shlex.join would re-quote "&&" itself
        # (e.g. into '&&'), which the shell then treats as a literal argument
        # again instead of an operator — exactly the bug being fixed here.
        run_target = " ".join(cmd) if needs_shell else cmd
        result = subprocess.run(run_target, shell=needs_shell, capture_output=True, text=True)
        output = f"{result.stdout}\n{result.stderr}"
        skipped.update(wholly_skipped_test_files(output))
        if result.returncode == 0:
            log(f"CI: {label} — PASSED")
        else:
            log(f"CI: {label} — FAILED (exit {result.returncode})")
            section = [f"=== {label} (exit {result.returncode}) ==="]
            for stream_name, output in (("stdout", result.stdout), ("stderr", result.stderr)):
                if output.strip():
                    tail = _tail(output.strip())
                    log(f"  {stream_name}:\n{tail}")
                    section.append(tail)
            failures.append("\n".join(section))
            all_passed = False
            environment_failure = environment_failure or ci_environment_failure(output)
    return CiRun(all_passed, "\n\n".join(failures), environment_failure, tuple(sorted(skipped)))


def _wait_for_human(*lines: str) -> None:
    for line in lines:
        log(line)
    sys.exit(HUMAN_ACTION_EXIT_CODE)


def _stop_on_ci_environment(run: CiRun) -> None:
    if run.environment_failure:
        _wait_for_human(
            "WAITING: CI failed on its environment, not the code, so no fix agent was run:",
            f"  {run.environment_failure}",
            "Start the missing service or free the disk, then re-run this script.",
        )


def _stop_on_low_disk() -> None:
    for path in {Path.cwd(), Path(tempfile.gettempdir())}:
        free = shutil.disk_usage(path).free
        if free < MIN_FREE_DISK_BYTES:
            _wait_for_human(
                f"WAITING: only {free / 1024**3:.1f} GB free under {path}, too little to run CI. "
                "Remove leftover build output (e.g. old cdk.out directories in /tmp), then "
                "re-run this script."
            )


def _stop_on_skipped_suites(skipped_files: tuple[str, ...]) -> None:
    allowed = ollama.load_allowed_skipped_tests()
    unexpected = [f for f in skipped_files if not any(fnmatch(f, p) for p in allowed)]
    if unexpected:
        _wait_for_human(
            f"WAITING: CI passed but skipped every test in {len(unexpected)} file(s), so they "
            "were never run:",
            *(f"  {f}" for f in unexpected),
            "Usually the run lacked a service or variable the suite needs (e.g. DATABASE_URL). "
            "Provide it where this stage runs and re-run, or list the files in "
            ".specify/pipeline.json ci.allow_skipped if skipping them is intended.",
        )


def _stop_on_test_updates(spec_dir: Path, base: str) -> None:
    """Stop when the implementation stage needed an existing test changed: it
    noted one in TEST_UPDATES_FILENAME, or edited a test case itself."""
    if not ollama.is_stage_enabled("test"):
        return
    notes = (
        read_file(spec_dir / TEST_UPDATES_FILENAME)
        if (spec_dir / TEST_UPDATES_FILENAME).exists()
        else ""
    )
    edits = test_case_edits(base)
    if not notes.strip() and not edits:
        return
    _wait_for_human(
        "WAITING: this feature changes behaviour that existing tests assert, and the test "
        "phase didn't update them. The implementation stage may not change a test case.",
        *(f"  {line}" for line in notes.splitlines() if line.strip()),
        *(f"  {path} (edited during implementation): {lines[0]}" for path, lines in edits.items()),
        "To resolve: add a [TEST] task per affected test file to tasks.md; make each update as a "
        "test-phase commit (subject `test: ...`) with its red output in test-results/, reverting "
        f"any implementation-phase edits to those tests; delete {spec_dir / TEST_UPDATES_FILENAME}; "
        "then re-run this script.",
    )


def _check_commit_hygiene() -> None:
    """Hard-fail if this branch has committed a file over the size
    threshold — cheap to catch mechanically (specs/027 committed a 4.5GB
    core dump that a critic only caught after the fact). Runs right before
    a stage is marked complete, not as an LLM judgment call."""
    oversized = oversized_committed_files()
    if not oversized:
        return
    log("FAIL: oversized file(s) committed on this branch — human review required.")
    for name, size in oversized:
        log(f"  {name} ({size / (1024 * 1024):.1f} MB)")
    log(
        "Remove or replace these files (e.g. `git rm` + amend, or a follow-up commit) "
        "before this stage can be marked complete."
    )
    sys.exit(1)


async def _run_implementation_agent(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    quality_principles: str,
    test_stage_enabled: bool = True,
) -> str:
    """Run the implementation agent if unchecked tasks remain, retrying once more if
    tasks are still unchecked afterward. Returns the latest tasks.md content. Exits
    the process if tasks remain unchecked after both attempts, rather than proceeding
    into a CI run that's guaranteed to fail on missing work. Tasks tagged
    `[MANUAL]` are excluded from this check — their task text itself states
    an agent cannot perform them (out-of-repo, human-only actions), so
    requiring them checked before proceeding would permanently block the
    stage."""
    if not unchecked_task_lines(tasks, exclude_tag="[MANUAL]"):
        log("All agent-executable tasks already checked off — skipping implementation agent.")
        return tasks

    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        log(f"Running implementation agent (attempt {attempt}/{max_attempts})...")
        pipeline_status.step("implementing", attempt, max_attempts)
        tdd_instruction = (
            "Follow TDD order: write failing tests first, commit, then implement, commit."
            if test_stage_enabled
            else "The test stage is disabled for this project — implement directly per spec.md/plan.md."
        )
        user_prompt = (
            f"Implement all unchecked tasks in specs/{feature}/tasks.md, EXCEPT any task tagged "
            f"[MANUAL] (those require out-of-repo human action and must stay unchecked). "
            f"{tdd_instruction} "
            f"Mark each non-[MANUAL] task - [x] in tasks.md after completing it."
        )
        await local_agent_loop.run_agent(
            log,
            "implement",
            "impl-agent",
            lambda model, tasks=tasks: impl_agent_definition(
                constitution,
                spec,
                plan,
                tasks,
                quality_principles,
                model=model,
                test_stage_enabled=test_stage_enabled,
            ),
            user_prompt,
        )

        tasks = read_file(spec_dir / "tasks.md")
        if not unchecked_task_lines(tasks, exclude_tag="[MANUAL]"):
            return tasks
        log(
            f"WARNING: implementation agent left unchecked tasks after attempt {attempt}/{max_attempts}."
        )

    log(
        "FAIL: implementation agent did not complete all tasks after "
        f"{max_attempts} attempts. Proceeding to CI would only fail on the missing "
        "work with misleading errors — aborting instead. Re-run this script to "
        "resume from the remaining unchecked tasks."
    )
    sys.exit(1)


async def _run_ci_fix_agent(
    feature: str, constitution: str, spec: str, plan: str, tasks: str, failure_summary: str
) -> None:
    user_prompt = f"Fix CI failures for feature {feature}. Failures:\n{failure_summary}"
    await local_agent_loop.run_agent(
        log,
        "implement",
        "ci-fix-agent",
        lambda model: ci_fix_agent_definition(
            constitution, spec, plan, tasks, failure_summary, model=model
        ),
        user_prompt,
    )


async def _ci_gate(
    feature: str, spec_dir: Path, constitution: str, spec: str, plan: str, include_slow: bool
) -> str:
    """Run the project's CI checks (quick: everything but e2e/integration; full:
    all), giving the CI fix agent one attempt on failure. Exits the process if CI
    still fails. Returns the outcome line for the stage's ready message — no
    resolvable CI commands is reported as SKIPPED (and recorded as a follow-up),
    never as passed: specs/004 and 005 logged "All CI checks passed" with nothing run."""
    scope = "CI checks" if include_slow else "quick CI checks"
    commands = _build_ci_commands(include_slow=include_slow)
    if not commands:
        log(f"CI SKIPPED — no CI commands resolved for {scope}.")
        record_non_blocking_concerns(
            spec_dir,
            feature,
            "CI",
            0,
            [
                {
                    "title": "CI was not run",
                    "severity": "High",
                    "location": "constitution.md CI Requirements",
                    "finding": "No backtick-quoted CI commands were found in constitution.md "
                    "or README.md, so the implement stage ran no typecheck, lint, or tests.",
                }
            ],
        )
        return "CI SKIPPED (no CI commands declared)"

    log(f"Running {scope}...")
    pipeline_status.step(scope)
    _stop_on_low_disk()
    run = _run_commands(commands)
    _stop_on_ci_environment(run)
    if not run.passed:
        log(f"{scope} failed — running CI fix agent (one attempt)...")
        pipeline_status.step(f"fixing {scope}")
        tasks = read_file(spec_dir / "tasks.md")
        await _run_ci_fix_agent(feature, constitution, spec, plan, tasks, run.failures)

        log(f"Re-running {scope} after fix attempt...")
        pipeline_status.step(f"re-running {scope}")
        run = _run_commands(commands)
        _stop_on_ci_environment(run)
        if not run.passed:
            log(f"FAIL: {scope} still failing after fix attempt. Human review required.")
            log(f"Remaining failures:\n{run.failures}")
            sys.exit(1)
    _stop_on_skipped_suites(run.skipped_files)
    log(f"{scope} passed.")
    return "All CI checks passed"


async def _finish_with_full_ci(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    quality_result: dict,
    baseline_dirty: list[str],
    base: str,
) -> None:
    """Record the passing quality review's follow-ups, run full CI, and finish
    the stage — the shared tail of a fresh both-gates PASS and a resumed one."""
    outcome = await _ci_gate(feature, spec_dir, constitution, spec, plan, include_slow=True)
    _stop_on_test_updates(spec_dir, base)
    _check_commit_hygiene()
    finish_stage(
        log,
        spec_dir,
        AGENT_NAME,
        "after_implement",
        "ch-4-implement",
        f"{outcome}. Implementation is ready for human review.",
        exclude_from_commit=baseline_dirty,
    )


async def _run_revision(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    pending_iter: int,
    pending_violations: list,
    pending_label: str,
) -> None:
    tasks_content = read_file(spec_dir / "tasks.md")
    log(
        f"Running fix agent for {pending_label} violations from iteration {pending_iter} ({len(pending_violations)} issue(s))..."
    )
    user_prompt = (
        f"Fix violations for feature {feature}. Violations: {json.dumps(pending_violations)}"
    )
    await local_agent_loop.run_agent(
        log,
        "implement",
        "fix-agent",
        lambda model: fix_agent_definition(
            constitution, spec, plan, tasks_content, pending_violations, model=model
        ),
        user_prompt,
    )


async def _on_both_pass(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    quality_result: dict,
    baseline_dirty: list[str],
    base: str,
) -> None:
    await _finish_with_full_ci(
        feature, spec_dir, constitution, spec, plan, quality_result, baseline_dirty, base
    )


async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting ch-4-implement-auto for feature: {feature}")

    preflight(spec_dir, feature)

    if stage_is_complete(spec_dir, "ch-4-implement"):
        log("Implementation stage already complete — nothing to do.")
        return

    # Snapshot what's dirty before this stage first touched anything, so the
    # final commit can tell its own work apart from unrelated pre-existing
    # drift and leave the latter out. See FOLLOWUP_HARNESS.md Bug 4.
    baseline_dirty = stage_dirty_baseline(spec_dir, "ch-4-implement", git.dirty_files)
    base = stage_start_commit(spec_dir, "ch-4-implement", git.head_commit)

    constitution = read_file(CONSTITUTION_PATH)
    spec = read_file(spec_dir / "spec.md")
    plan = read_file(spec_dir / "plan.md")
    tasks = read_file(spec_dir / "tasks.md")

    quality_principles = read_memory("code-quality-principles")

    budget = apply_escalation_review(
        spec_dir, "ch-4-implement-critic-escalation-review.md", CRITIC_RESULT_PREFIX, 3, log
    )

    tasks = await _run_implementation_agent(
        feature,
        spec_dir,
        constitution,
        spec,
        plan,
        tasks,
        quality_principles,
        test_stage_enabled=ollama.is_stage_enabled("test"),
    )

    _stop_on_test_updates(spec_dir, base)

    # Skipped once a critic iteration has passed: e2e is deferred until both
    # review gates pass, to avoid its ~3 min cost on every iteration.
    if not find_passing_iteration(spec_dir, CRITIC_RESULT_PREFIX):
        await _ci_gate(feature, spec_dir, constitution, spec, plan, include_slow=False)
        _stop_on_test_updates(spec_dir, base)

    passing_quality = find_passing_iteration(spec_dir, QUALITY_RESULT_PREFIX)
    if passing_quality is not None:
        log(f"Already PASS from quality review iteration {passing_quality}.")
        await _finish_with_full_ci(
            feature,
            spec_dir,
            constitution,
            spec,
            plan,
            read_result(spec_dir, QUALITY_RESULT_PREFIX, passing_quality),
            baseline_dirty,
            base,
        )
        return

    iteration = next_iteration(spec_dir, CRITIC_RESULT_PREFIX)
    resume_state = find_two_gate_resume_state(
        spec_dir, CRITIC_RESULT_PREFIX, QUALITY_RESULT_PREFIX, iteration
    )

    await run_two_gate_loop(
        log,
        spec_dir,
        feature,
        budget,
        gate1=claude_gate(
            feature=feature,
            result_prefix=CRITIC_RESULT_PREFIX,
            script_name="ch_4_implement_critic.py",
            critic_type="implement",
            label="implement critic",
            summary_style="violations",
            agent_name="implement-critic",
            description="Validates implemented code against tasks.md, plan.md, spec.md, and "
            "constitution.md.",
            task=f"Validate the implementation for feature {feature}.",
            prompt=lambda iteration, violations_block, output_instructions: (
                build_implement_critic_prompt(
                    constitution,
                    spec,
                    plan,
                    read_file(spec_dir / "tasks.md"),
                    iteration,
                    violations_block=violations_block,
                    output_instructions=output_instructions,
                )
            ),
            context=lambda: [
                ("CONSTITUTION", constitution),
                ("SPEC", spec),
                ("PLAN", plan),
                ("TASKS", read_file(spec_dir / "tasks.md")),
            ],
        ),
        gate2=claude_gate(
            feature=feature,
            result_prefix=QUALITY_RESULT_PREFIX,
            script_name="ch_4_implement_quality_critic.py",
            critic_type="implement-quality-review",
            label="code quality review",
            summary_style="confidence",
            agent_name="code-quality-review",
            description="Reviews implemented code for quality, maintainability, and "
            "operational safety.",
            task=f"Review the implementation for feature {feature} for code quality.",
            violations_context="quality violations (already addressed by the fix agent)",
            prompt=lambda iteration, violations_block, output_instructions: (
                build_quality_review_prompt(
                    constitution,
                    spec,
                    plan,
                    read_file(spec_dir / "tasks.md"),
                    quality_principles,
                    iteration,
                    violations_block=violations_block,
                    output_instructions=output_instructions,
                )
            ),
            context=lambda: [
                ("CONSTITUTION", constitution),
                ("SPEC", spec),
                ("PLAN", plan),
                ("TASKS", read_file(spec_dir / "tasks.md")),
                ("CODE QUALITY PRINCIPLES", quality_principles),
            ],
        ),
        resume_state=resume_state,
        run_revision=functools.partial(_run_revision, feature, spec_dir, constitution, spec, plan),
        on_both_pass=functools.partial(
            _on_both_pass,
            feature,
            spec_dir,
            constitution,
            spec,
            plan,
            baseline_dirty=baseline_dirty,
            base=base,
        ),
        escalation_kwargs={
            "escalation_filename": "ch-4-implement-critic-escalation.md",
            "log_description": "implementation failed review",
            "review_history_prefixes": [
                (CRITIC_RESULT_PREFIX, "Implement Critic"),
                (QUALITY_RESULT_PREFIX, "Code Quality Review"),
            ],
            "title": "Implement Critic Escalation",
            "summary": (
                "The automated implement-critic loop exhausted its iteration limit without producing\n"
                "an implementation that passed both the implement critic and the code quality review.\n"
                "Human review is required to resolve the outstanding violations before the branch can\n"
                "be merged."
            ),
            "required_action": (
                "1. Review the violations above.\n"
                "2. Fix the BLOCKING violations manually in the relevant source files.\n"
                "3. Re-run `python .claude/agents/ch_4_implement_auto.py` to restart the automated loop,\n"
                "   or run `/ch-4-implement-critic` and `/ch-4-implement-code-quality-review` manually to verify your fixes."
            ),
        },
    )


if __name__ == "__main__":
    run_cli(AGENT_NAME, "Implementation auto-orchestrator", run)
