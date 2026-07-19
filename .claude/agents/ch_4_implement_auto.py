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
import subprocess
import sys
from pathlib import Path

from ch_4_implement_critic import build_implement_critic_prompt
from ch_4_implement_quality_critic import build_quality_review_prompt
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query

from agent_common.console import make_logger, setup_log_file, stream_query
from agent_common.critic_loop import GateSpec, finish_stage, run_cli, run_two_gate_loop
from agent_common.files import read_file, require_spec_files
from agent_common.followup import record_from_result_file, record_non_blocking_concerns
from agent_common.preflight_checks import oversized_committed_files
from agent_common.project_conventions import is_slow_check, resolve_ci_commands
from agent_common.resume_state import (
    extend_iterations_if_reviewed,
    find_passing_iteration,
    find_two_gate_resume_state,
    format_violations_block,
    max_existing_iteration,
    next_iteration,
    stage_is_complete,
)

AGENT_NAME = "ch-4-implement-auto"
CRITIC_RESULT_PREFIX = "ch-4-implement-critic-result"
QUALITY_RESULT_PREFIX = "ch-4-implement-code-quality-review-result"
log = make_logger(AGENT_NAME)


# Pre-flight checks


def preflight(spec_dir: Path, feature: str):
    require_spec_files(log, spec_dir, "spec.md", "plan.md", "tasks.md")

    test_quality_results = list(spec_dir.glob("ch-3-test-quality-review-result-*.json"))
    if not test_quality_results:
        log("ERROR: Test phase not complete. Run /ch-3-test-auto first.")
        sys.exit(1)
    max_quality_iteration = max_existing_iteration(spec_dir, "ch-3-test-quality-review-result")
    passing = find_passing_iteration(
        spec_dir, "ch-3-test-quality-review-result", max_quality_iteration
    )
    if passing is None:
        log("ERROR: No passing test-quality-review result found. Run /ch-3-test-auto to resolve.")
        sys.exit(1)

    tasks_content = (spec_dir / "tasks.md").read_text(encoding="utf-8")
    all_done = "- [ ]" not in tasks_content
    existing_results = list(spec_dir.glob(f"{CRITIC_RESULT_PREFIX}-*.json"))

    if all_done and not existing_results:
        if sys.stdin.isatty():
            response = (
                input(
                    f"[{AGENT_NAME}] WARNING: All tasks in tasks.md appear complete but no critic result exists. "
                    f"Run critic on existing implementation? (yes/no): "
                )
                .strip()
                .lower()
            )
        else:
            log("Non-interactive mode: defaulting to running critic on existing implementation.")
            response = "yes"
        if response != "yes":
            log("Aborted. No changes made.")
            sys.exit(0)


# Subagent definitions


def impl_agent_definition(
    constitution: str, spec: str, plan: str, tasks: str, quality_principles: str
) -> AgentDefinition:
    return AgentDefinition(
        description="Implements all unchecked [IMPL] tasks in tasks.md.",
        prompt=f"""You are the Implementation Agent for a spec-kit project.

Tests have already been written and confirmed failing by the test phase.
Your sole function is to implement all unchecked [IMPL] tasks (marked - [ ] and containing [IMPL]) in tasks.md, in order.
Also process any unchecked tasks with no [TEST]/[IMPL] label (infrastructure/setup tasks).

DO NOT write new test files. DO NOT modify existing test files.

For each [IMPL] task:
1. Load the paired [TEST] task's red-output artifact from specs/*/test-results/<PAIRED-TASKID>-red.txt
2. Read the test file(s) already written by the test phase for this behaviour
3. Write the minimum implementation to make the failing tests pass
4. Run the relevant test suite to confirm green
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
- After each task, run this project's typecheck command as declared in constitution §12 (CI Requirements)
- The code will be evaluated by a Code Quality Review agent using the principles above — implement accordingly
- Do not stop until all - [ ] tasks are marked - [x] in tasks.md
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def critic_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    iteration: int,
    violations: list | None = None,
) -> AgentDefinition:
    violations_block = format_violations_block(violations, iteration)

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/ch-4-implement-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-4-implement-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates implemented code against tasks.md, plan.md, spec.md, and constitution.md.",
        prompt=build_implement_critic_prompt(
            constitution,
            spec,
            plan,
            tasks,
            iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


def ci_fix_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    ci_failures: str,
) -> AgentDefinition:
    return AgentDefinition(
        description="Fixes failing CI checks (typecheck, unit tests, e2e) after both review gates passed.",
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
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def fix_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    violations: list,
) -> AgentDefinition:
    return AgentDefinition(
        description="Fixes specific implementation violations found by the critic or quality review.",
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
- After all fixes, run this project's typecheck command as declared in constitution §12 (CI Requirements)
- Run the relevant test suite to confirm nothing is broken
- Commit fixed files: git add <files> && git commit -m "fix: address violations"
- Do not stop until every violation in the list is addressed and committed
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def quality_review_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    quality_principles: str,
    iteration: int,
    quality_violations: list | None = None,
) -> AgentDefinition:
    violations_block = format_violations_block(
        quality_violations, iteration, "quality violations (already addressed by the fix agent)"
    )

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/ch-4-implement-code-quality-review-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-4-implement-code-quality-review] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Reviews implemented code for quality, maintainability, and operational safety.",
        prompt=build_quality_review_prompt(
            constitution,
            spec,
            plan,
            tasks,
            quality_principles,
            iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


# CI check


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


def _run_commands(commands: list[tuple[str, list[str]]]) -> tuple[bool, str]:
    all_passed = True
    failures: list[str] = []
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
        if result.returncode == 0:
            log(f"CI: {label} — PASSED")
        else:
            log(f"CI: {label} — FAILED (exit {result.returncode})")
            section = [f"=== {label} (exit {result.returncode}) ==="]
            if result.stdout.strip():
                log(f"  stdout:\n{result.stdout.strip()}")
                section.append(result.stdout.strip())
            if result.stderr.strip():
                log(f"  stderr:\n{result.stderr.strip()}")
                section.append(result.stderr.strip())
            failures.append("\n".join(section))
            all_passed = False
    return all_passed, "\n\n".join(failures)


def run_quick_ci_checks() -> tuple[bool, str]:
    """Run all declared CI checks except e2e/integration-shaped ones. Fast gate
    before the critic loop."""
    return _run_commands(_build_ci_commands(include_slow=False))


def run_full_ci_checks() -> tuple[bool, str]:
    """Run every declared CI check, including e2e. Slow gate after both review
    gates pass."""
    return _run_commands(_build_ci_commands(include_slow=True))


# Commit hygiene


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


# Main orchestration loop


async def _run_implementation_agent(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    quality_principles: str,
) -> str:
    """Run the implementation agent if unchecked tasks remain. Returns the latest
    tasks.md content."""
    if "- [ ]" not in tasks:
        log("All tasks already checked off — skipping implementation agent.")
        return tasks

    log("Running implementation agent...")
    await stream_query(
        query(
            prompt=(
                f"Implement all unchecked tasks in specs/{feature}/tasks.md. "
                f"Follow TDD order: write failing tests first, commit, then implement, commit. "
                f"Mark each task - [x] in tasks.md after completing it."
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "impl-agent": impl_agent_definition(
                        constitution, spec, plan, tasks, quality_principles
                    )
                },
                setting_sources=["project"],
            ),
        )
    )

    tasks = read_file(spec_dir / "tasks.md")
    if "- [ ]" in tasks:
        log(
            "WARNING: implementation agent did not complete all tasks. Proceeding to critic anyway."
        )
    return tasks


async def _run_ci_fix_agent(
    feature: str, constitution: str, spec: str, plan: str, tasks: str, failure_summary: str
) -> None:
    """Run the CI fix agent once for the given failure summary."""
    await stream_query(
        query(
            prompt=f"Fix CI failures for feature {feature}. Failures:\n{failure_summary}",
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "ci-fix-agent": ci_fix_agent_definition(
                        constitution, spec, plan, tasks, failure_summary
                    )
                },
                setting_sources=["project"],
            ),
        )
    )


async def _run_quick_ci_gate(
    feature: str, spec_dir: Path, constitution: str, spec: str, plan: str, max_iterations: int
) -> None:
    """Run quick CI checks (typecheck + unit tests) before the critic loop, unless a
    passing critic iteration already exists. Exits the process if CI still fails
    after one fix attempt. e2e is deferred until both review gates pass, to avoid
    its ~3 min cost on every iteration."""
    if find_passing_iteration(spec_dir, CRITIC_RESULT_PREFIX, max_iterations):
        return

    log("Running quick CI checks (typecheck + unit tests) before critic loop...")
    quick_passed, quick_failure_summary = run_quick_ci_checks()
    if not quick_passed:
        log("Quick CI failed — running CI fix agent (one attempt) before entering critic loop...")
        tasks = read_file(spec_dir / "tasks.md")
        await _run_ci_fix_agent(feature, constitution, spec, plan, tasks, quick_failure_summary)

        log("Re-running quick CI checks after fix attempt...")
        quick_passed, quick_failure_summary = run_quick_ci_checks()
        if not quick_passed:
            log("FAIL: Quick CI still failing after fix attempt. Human review required.")
            log(f"Remaining failures:\n{quick_failure_summary}")
            sys.exit(1)

    log("Quick CI passed — proceeding to critic loop.")


async def _finalize_if_quality_already_passed(
    spec_dir: Path, feature: str, constitution: str, spec: str, plan: str, max_iterations: int
) -> bool:
    """If quality review already passed in a prior iteration, run full CI (with one
    fix-agent attempt) and finish the stage. Returns True if it finalized the stage,
    in which case the caller should return immediately."""
    passing = find_passing_iteration(spec_dir, QUALITY_RESULT_PREFIX, max_iterations)
    if passing is None:
        return False

    log(f"Already PASS from quality review iteration {passing}.")
    record_from_result_file(
        spec_dir, feature, "Code Quality Review", spec_dir / f"{QUALITY_RESULT_PREFIX}-{passing}.json"
    )
    log("Running CI checks before finalising...")
    ci_passed, ci_failure_summary = run_full_ci_checks()
    if not ci_passed:
        log("CI checks failed — running CI fix agent (one attempt)...")
        tasks = read_file(spec_dir / "tasks.md")
        await _run_ci_fix_agent(feature, constitution, spec, plan, tasks, ci_failure_summary)

        log("Re-running CI checks after fix attempt...")
        ci_passed, ci_failure_summary = run_full_ci_checks()
        if not ci_passed:
            log("FAIL: CI checks still failing after fix attempt. Human review required.")
            log(f"Remaining failures:\n{ci_failure_summary}")
            sys.exit(1)

    _check_commit_hygiene()
    finish_stage(
        log,
        spec_dir,
        AGENT_NAME,
        "after_implement",
        "ch-4-implement",
        "All CI checks passed. Implementation is ready for human review.",
    )
    return True


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
    await stream_query(
        query(
            prompt=(
                f"Fix violations for feature {feature}. "
                f"Violations: {json.dumps(pending_violations)}"
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "fix-agent": fix_agent_definition(
                        constitution, spec, plan, tasks_content, pending_violations
                    )
                },
                setting_sources=["project"],
            ),
        )
    )


async def _on_both_pass(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    quality_result: dict,
) -> None:
    record_non_blocking_concerns(
        spec_dir,
        feature,
        "Code Quality Review",
        quality_result.get("iteration", 0),
        quality_result.get("non_blocking_concerns", []),
    )
    log("Running CI checks before finalising...")
    ci_passed, ci_failure_summary = run_full_ci_checks()
    if not ci_passed:
        log("CI checks failed — running CI fix agent (one attempt)...")
        tasks_content = read_file(spec_dir / "tasks.md")
        await _run_ci_fix_agent(
            feature, constitution, spec, plan, tasks_content, ci_failure_summary
        )

        log("Re-running CI checks after fix attempt...")
        ci_passed, ci_failure_summary = run_full_ci_checks()
        if not ci_passed:
            log("FAIL: CI checks still failing after fix attempt. Human review required.")
            log(f"Remaining failures:\n{ci_failure_summary}")
            sys.exit(1)

    _check_commit_hygiene()
    finish_stage(
        log,
        spec_dir,
        AGENT_NAME,
        "after_implement",
        "ch-4-implement",
        "All CI checks passed. Implementation is ready for human review.",
    )


def _build_critic_query(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    iteration: int,
    prev_violations,
):
    tasks_content = read_file(spec_dir / "tasks.md")
    return query(
        prompt=(
            f"Validate the implementation for feature {feature}. "
            f"Write result to specs/{feature}/ch-4-implement-critic-result-{iteration}.json."
        ),
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
            agents={
                "implement-critic": critic_agent_definition(
                    constitution, spec, plan, tasks_content, iteration, prev_violations
                )
            },
            setting_sources=["project"],
        ),
    )


def _build_quality_query(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    quality_principles: str,
    iteration: int,
    quality_violations,
):
    tasks_content = read_file(spec_dir / "tasks.md")
    return query(
        prompt=(
            f"Review the implementation for feature {feature} for code quality. "
            f"Write result to specs/{feature}/ch-4-implement-code-quality-review-result-{iteration}.json."
        ),
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
            agents={
                "quality-review": quality_review_agent_definition(
                    constitution,
                    spec,
                    plan,
                    tasks_content,
                    quality_principles,
                    iteration,
                    quality_violations,
                )
            },
            setting_sources=["project"],
        ),
    )


async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting ch-4-implement-auto for feature: {feature}")

    preflight(spec_dir, feature)

    if stage_is_complete(spec_dir, "ch-4-implement"):
        log("Implementation stage already complete — nothing to do.")
        return

    constitution = read_file(Path(".specify/memory/constitution.md"))
    spec = read_file(spec_dir / "spec.md")
    plan = read_file(spec_dir / "plan.md")
    tasks = read_file(spec_dir / "tasks.md")

    quality_principles_path = Path(".specify/memory/code-quality-principles.md")
    quality_principles = (
        read_file(quality_principles_path)
        if quality_principles_path.exists()
        else "(code-quality-principles.md not found)"
    )

    max_iterations, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "ch-4-implement-critic-escalation-review.md", CRITIC_RESULT_PREFIX, 3, log
    )

    # --- Step 1: Run implementation if unchecked tasks remain ---
    tasks = await _run_implementation_agent(
        feature, spec_dir, constitution, spec, plan, tasks, quality_principles
    )

    # --- Step 1b: Quick CI check before the critic loop ---
    await _run_quick_ci_gate(feature, spec_dir, constitution, spec, plan, max_iterations)

    # --- Resume guard: done if quality review already passed AND full CI is clean ---
    if await _finalize_if_quality_already_passed(
        spec_dir, feature, constitution, spec, plan, max_iterations
    ):
        return

    # --- Resume state: determine where we left off ---
    # Use result files as idempotency markers; carry forward violations from any incomplete gate.
    iteration = next_iteration(spec_dir, CRITIC_RESULT_PREFIX)
    resume_state = find_two_gate_resume_state(
        spec_dir, CRITIC_RESULT_PREFIX, QUALITY_RESULT_PREFIX, iteration
    )

    # --- Step 2: Two-gate loop (implement critic → code quality review) ---
    await run_two_gate_loop(
        log,
        spec_dir,
        feature,
        max_iterations,
        gate1=GateSpec(
            CRITIC_RESULT_PREFIX,
            "ch_4_implement_critic.py",
            "implement",
            "implement critic",
            functools.partial(_build_critic_query, feature, spec_dir, constitution, spec, plan),
        ),
        gate2=GateSpec(
            QUALITY_RESULT_PREFIX,
            "ch_4_implement_quality_critic.py",
            "implement-quality-review",
            "code quality review",
            functools.partial(
                _build_quality_query,
                feature,
                spec_dir,
                constitution,
                spec,
                plan,
                quality_principles,
            ),
        ),
        resume_state=resume_state,
        skip_fix_agent=_skip_fix_agent,
        run_revision=functools.partial(_run_revision, feature, spec_dir, constitution, spec, plan),
        on_both_pass=functools.partial(_on_both_pass, feature, spec_dir, constitution, spec, plan),
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


# Entry point

if __name__ == "__main__":
    run_cli(AGENT_NAME, "Implementation auto-orchestrator", run)
