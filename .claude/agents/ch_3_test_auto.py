#!/usr/bin/env python3
"""
.claude/agents/ch_3_test_auto.py

Agentic orchestrator for the automated test phase loop.
Writes failing tests for all [TEST] tasks in tasks.md, then runs the test critic and
test quality review in a two-gate feedback loop until both PASS or escalation.

Usage:
  python .claude/agents/ch_3_test_auto.py
  python .claude/agents/ch_3_test_auto.py --feature 015-job-description-rich-text

Requirements:
  pip install claude-agent-sdk

The script derives the feature from the current git branch if --feature
is not supplied, matching the behaviour of the speckit skills.

Loop structure:
  1. Test agent — writes failing tests for all unchecked [TEST] tasks in tasks.md,
     records red-output artifacts in specs/$FEATURE/test-results/<TASKID>-red.txt
  2. Test critic — validates test files against test-principles.md, spec.md, and constitution.md
  3. Test quality review — validates assertion quality, test naming, and CI readiness
  Gates 2 and 3 must both PASS in the same iteration before the test phase is committed.
  If either gate FAILs: fix agent addresses violations in test files only, both gates re-run.
  Maximum 3 iterations before escalation.

Resume behaviour:
  Re-running after an interruption continues from the last incomplete step:
  - Result files act as idempotency markers: a gate is skipped if its result already exists
  - If the last critic result was FAIL, fix agent runs before the next critic
  - If critic PASS but quality review not yet run, resumes at the same iteration number
  - If the last quality review result was FAIL, fix agent runs before the next critic
  - Test agent is re-run only if unchecked [TEST] tasks remain in tasks.md
"""

import json
import sys
from pathlib import Path

from ch_3_test_critic import build_test_critic_prompt
from ch_3_test_quality_critic import build_test_quality_review_prompt
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query

from agent_common.console import make_logger, setup_log_file, stream_query
from agent_common.critic_loop import (
    GateSpec,
    finish_if_already_passing,
    finish_stage,
    run_cli,
    run_two_gate_loop,
)
from agent_common.files import read_file, require_spec_files
from agent_common.followup import record_from_result_file, record_non_blocking_concerns
from agent_common.preflight_checks import oversized_committed_files, validate_red_state_artifacts
from agent_common.resume_state import (
    extend_iterations_if_reviewed,
    find_passing_iteration,
    find_two_gate_resume_state,
    format_violations_block,
    max_existing_iteration,
    next_iteration,
)

AGENT_NAME = "ch-3-test-auto"
CRITIC_RESULT_PREFIX = "ch-3-test-critic-result"
TEST_QUALITY_RESULT_PREFIX = "ch-3-test-quality-review-result"
log = make_logger(AGENT_NAME)


# Pre-flight checks


def preflight(spec_dir: Path, feature: str):
    require_spec_files(log, spec_dir, "spec.md", "plan.md", "tasks.md")

    tasks_content = (spec_dir / "tasks.md").read_text(encoding="utf-8")
    test_tasks_done = all(
        "[x]" in line or "[X]" in line
        for line in tasks_content.splitlines()
        if "[TEST]" in line and ("- [ ]" in line or "- [x]" in line or "- [X]" in line)
    )
    has_test_tasks = any("[TEST]" in line for line in tasks_content.splitlines())

    if not has_test_tasks:
        log("WARNING: No [TEST] tasks found in tasks.md. Is the task format correct?")

    existing_results = list(spec_dir.glob(f"{CRITIC_RESULT_PREFIX}-*.json"))

    if test_tasks_done and not existing_results:
        if sys.stdin.isatty():
            response = (
                input(
                    f"[{AGENT_NAME}] WARNING: All [TEST] tasks appear complete but no critic result exists. "
                    f"Run critic on existing test files? (yes/no): "
                )
                .strip()
                .lower()
            )
        else:
            log("Non-interactive mode: defaulting to running critic on existing test files.")
            response = "yes"
        if response != "yes":
            log("Aborted. No changes made.")
            sys.exit(0)


# Subagent definitions


def test_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    test_principles: str,
    feature: str,
) -> AgentDefinition:
    return AgentDefinition(
        description="Writes failing tests for all unchecked [TEST] tasks in tasks.md.",
        prompt=f"""You are the Test Agent for a spec-kit project.

Your sole function is to write failing tests for all unchecked [TEST] tasks in tasks.md.

For each [TEST] task (lines containing "[TEST]" and marked "- [ ]"):
1. Read test-principles.md carefully — every test must conform to these principles
2. Write the failing test file(s) for this behaviour only — NO implementation code
3. Run the test suite targeting the new file to confirm it FAILS for the expected reason:
   - Acceptable failures: assertion failure, "not found" / "not implemented" error
   - Unacceptable failures: syntax errors in the test file itself (fix these first)
   - If a test passes immediately without any implementation, flag it — do NOT record it as red
4. Save the failing output to specs/{feature}/test-results/<TASKID>-red.txt
   (create the test-results/ directory if needed)
5. Mark the [TEST] task as [x] in tasks.md
6. Commit: git add <test file> specs/{feature}/tasks.md specs/{feature}/test-results/<TASKID>-red.txt
           git commit -m "test: <TASKID> write failing tests for <description>"

Do NOT touch [IMPL] tasks. Do NOT write any implementation code. Do NOT modify
any of the source (non-test) directories declared under the constitution's
"Test file location" bullets (§5, Test-Driven Development).

Inputs already loaded for you (this project's constitution.md is human-customized, so any
section number referenced above/below may not match — locate the section by heading text instead):

--- CONSTITUTION ---
{constitution}

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

--- TEST PRINCIPLES ---
{test_principles}

Key rules:
- Read test-principles.md fully before writing any test
- Run tests using the commands declared in the constitution's CI Requirements section (§12), targeting the new file
- Use only the test libraries in the constitution's Stack Constraints section (§2) — no other test libraries
- Test names must describe behaviour, not implementation details
- No shared mutable state between test cases
- Do not stop until all [TEST] tasks are marked [x] in tasks.md
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def test_critic_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    test_principles: str,
    feature: str,
    iteration: int,
    violations: list | None = None,
) -> AgentDefinition:
    violations_block = format_violations_block(violations, iteration)

    output_instructions = (
        f"- After producing JSON, write it to specs/{feature}/ch-3-test-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-3-test-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates test files against test-principles.md, spec.md, and constitution.md.",
        prompt=build_test_critic_prompt(
            constitution,
            spec,
            plan,
            tasks,
            test_principles,
            feature,
            iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


def test_quality_review_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    test_principles: str,
    feature: str,
    iteration: int,
    quality_violations: list | None = None,
) -> AgentDefinition:
    violations_block = format_violations_block(
        quality_violations, iteration, "test quality issues (already addressed by the test agent)"
    )

    output_instructions = (
        f"- After producing JSON, write it to specs/{feature}/ch-3-test-quality-review-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-3-test-quality-review] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Reviews test files for assertion quality, test naming, and CI readiness.",
        prompt=build_test_quality_review_prompt(
            constitution,
            spec,
            plan,
            tasks,
            test_principles,
            iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


def test_fix_agent_definition(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    test_principles: str,
    violations: list,
) -> AgentDefinition:
    return AgentDefinition(
        description="Fixes specific test violations found by the test critic.",
        prompt=f"""You are the Test Fix Agent for a spec-kit project.

Your sole function is to fix the specific violations listed below in the test files.
Fix ONLY test files. Do NOT touch implementation (non-test) files, per the source
directories declared under the constitution's "Test file location" bullets (§5, Test-Driven Development).
Do not add features. Do not change test assertions that are not part of a listed violation.

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

--- TEST PRINCIPLES ---
{test_principles}

Key rules:
- Read the file at each violation's location before modifying it
- Apply the minimum change that addresses the violation
- After all fixes, re-run the affected test files to confirm they still fail for the expected reason
- Update red-output artifacts in specs/*/test-results/ if the test output changes
- Commit fixed files: git add <test files> && git commit -m "fix: address test critic violations"
- Do not stop until every violation in the list is addressed and committed
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


# Main orchestration loop


async def _run_red_state_gate(
    feature: str,
    spec_dir: Path,
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    test_principles: str,
) -> None:
    """Deterministic §TQ2 pre-check: a *-red.txt artifact showing only an
    infrastructure failure (connection refused, unreachable service, no
    tests found) doesn't prove the test would fail without the
    implementation — it proves the test never ran. Kick back to the test
    agent directly (up to 2 attempts) before the critic loop, so the fix is
    framed as "your test environment isn't running" rather than requiring
    an LLM to infer that from a critic pass."""
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        issues = validate_red_state_artifacts(spec_dir / "test-results")
        if not issues:
            return

        issue_lines = "\n".join(f"- {name}: {reason}" for name, reason in issues.items())
        log(
            f"Deterministic §TQ2 red-state check found {len(issues)} invalid artifact(s) "
            f"(attempt {attempt}/{max_attempts}) — kicking back to test agent before critic loop..."
        )
        await stream_query(
            query(
                prompt=(
                    f"The following red-state artifacts for feature {feature} do not prove a "
                    f"genuine test failure — most likely the test environment (database, dev "
                    f"server, or similar dependency) was not running when the test was captured, "
                    f"or the test actually passed:\n\n{issue_lines}\n\n"
                    f"For each one: ensure the required test environment is running, re-run the "
                    f"corresponding test, and re-save a genuine failing-assertion (or module-not-found) "
                    f"capture to the same specs/{feature}/test-results/<TASKID>-red.txt path. Do not "
                    f"write implementation code."
                ),
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={
                        "test-agent": test_agent_definition(
                            constitution, spec, plan, tasks, test_principles, feature
                        )
                    },
                    setting_sources=["project"],
                ),
            )
        )

    remaining = validate_red_state_artifacts(spec_dir / "test-results")
    if remaining:
        log(
            f"§TQ2 red-state check still finds {len(remaining)} invalid artifact(s) after "
            f"{max_attempts} attempts — proceeding to critic loop, which will report them formally."
        )


async def _run_test_agent_if_needed(
    feature: str,
    spec_dir: Path,
    tasks: str,
    constitution: str,
    spec: str,
    plan: str,
    test_principles: str,
) -> str:
    """Run the test-writing agent if any [TEST] task is still unchecked, then
    return the current tasks.md content (re-read if the agent ran)."""
    has_unchecked_tests = any("- [ ]" in line and "[TEST]" in line for line in tasks.splitlines())
    if not has_unchecked_tests:
        log("All [TEST] tasks already checked off — skipping test agent.")
        return tasks

    log("Running test agent...")
    await stream_query(
        query(
            prompt=(
                f"Write failing tests for all unchecked [TEST] tasks in specs/{feature}/tasks.md. "
                f"No implementation code. Confirm each test fails for the expected reason. "
                f"Save failing output to specs/{feature}/test-results/<TASKID>-red.txt. "
                f"Mark each [TEST] task [x] in tasks.md after completing it."
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "test-agent": test_agent_definition(
                        constitution, spec, plan, tasks, test_principles, feature
                    )
                },
                setting_sources=["project"],
            ),
        )
    )

    tasks = read_file(spec_dir / "tasks.md")
    still_unchecked = any("- [ ]" in line and "[TEST]" in line for line in tasks.splitlines())
    if still_unchecked:
        log("WARNING: test agent did not complete all [TEST] tasks. Proceeding to critic anyway.")
    return tasks


async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting ch-3-test-auto for feature: {feature}")

    preflight(spec_dir, feature)

    constitution = read_file(Path(".specify/memory/constitution.md"))
    spec = read_file(spec_dir / "spec.md")
    plan = read_file(spec_dir / "plan.md")
    tasks = read_file(spec_dir / "tasks.md")

    test_principles_path = Path(".specify/memory/test-principles.md")
    test_principles = (
        read_file(test_principles_path)
        if test_principles_path.exists()
        else "(test-principles.md not found)"
    )

    max_iterations, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "ch-3-test-critic-escalation-review.md", CRITIC_RESULT_PREFIX, 3, log
    )

    # --- Step 1: Run test agent if unchecked [TEST] tasks remain ---
    tasks = await _run_test_agent_if_needed(
        feature, spec_dir, tasks, constitution, spec, plan, test_principles
    )

    # --- Step 1b: Deterministic §TQ2 red-state validity gate before the critic loop ---
    await _run_red_state_gate(feature, spec_dir, constitution, spec, plan, tasks, test_principles)

    # --- Resume guard: done if test quality review already passed ---
    passing_quality_iter = find_passing_iteration(
        spec_dir,
        TEST_QUALITY_RESULT_PREFIX,
        max_existing_iteration(spec_dir, TEST_QUALITY_RESULT_PREFIX, max_iterations),
    )
    if passing_quality_iter is not None:
        record_from_result_file(
            spec_dir,
            feature,
            "Test Quality Review",
            spec_dir / f"{TEST_QUALITY_RESULT_PREFIX}-{passing_quality_iter}.json",
        )
    if finish_if_already_passing(
        log,
        spec_dir,
        AGENT_NAME,
        TEST_QUALITY_RESULT_PREFIX,
        max_iterations,
        "test quality review",
        "Test phase is ready for human review. No further action taken.",
        "after_test",
        "ch-3-test",
    ):
        return

    # --- Resume state: determine where we left off ---
    # Use result files as idempotency markers; carry forward violations from any incomplete gate.
    iteration = next_iteration(spec_dir, CRITIC_RESULT_PREFIX)
    resume_state = find_two_gate_resume_state(
        spec_dir, CRITIC_RESULT_PREFIX, TEST_QUALITY_RESULT_PREFIX, iteration
    )

    async def run_revision(pending_iter: int, pending_violations: list, pending_label: str) -> None:
        tasks_content = read_file(spec_dir / "tasks.md")
        log(
            f"Running fix agent for {pending_label} violations from iteration {pending_iter} "
            f"({len(pending_violations)} issue(s))..."
        )
        await stream_query(
            query(
                prompt=(
                    f"Fix {pending_label} violations for feature {feature}. "
                    f"Violations: {json.dumps(pending_violations)}"
                ),
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={
                        "test-fix-agent": test_fix_agent_definition(
                            constitution,
                            spec,
                            plan,
                            tasks_content,
                            test_principles,
                            pending_violations,
                        )
                    },
                    setting_sources=["project"],
                ),
            )
        )

    async def on_both_pass(quality_result: dict) -> None:
        record_non_blocking_concerns(
            spec_dir,
            feature,
            "Test Quality Review",
            quality_result.get("iteration", 0),
            quality_result.get("non_blocking_concerns", []),
        )
        oversized = oversized_committed_files()
        if oversized:
            log("FAIL: oversized file(s) committed on this branch — human review required.")
            for name, size in oversized:
                log(f"  {name} ({size / (1024 * 1024):.1f} MB)")
            log(
                "Remove or replace these files (e.g. `git rm` + amend, or a follow-up commit) "
                "before this stage can be marked complete."
            )
            sys.exit(1)
        finish_stage(
            log,
            spec_dir,
            AGENT_NAME,
            "after_test",
            "ch-3-test",
            "Test phase complete. Implementation may now begin.",
        )

    def build_critic_query(iteration: int, prev_violations):
        tasks_content = read_file(spec_dir / "tasks.md")
        return query(
            prompt=(
                f"Validate the test files for feature {feature}. "
                f"Write result to specs/{feature}/ch-3-test-critic-result-{iteration}.json."
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "test-critic": test_critic_agent_definition(
                        constitution,
                        spec,
                        plan,
                        tasks_content,
                        test_principles,
                        feature,
                        iteration,
                        prev_violations,
                    )
                },
                setting_sources=["project"],
            ),
        )

    def build_quality_query(iteration: int, quality_violations):
        tasks_content = read_file(spec_dir / "tasks.md")
        return query(
            prompt=(
                f"Review the test files for feature {feature} for test quality "
                f"(assertion quality, naming, CI readiness). "
                f"Write result to specs/{feature}/ch-3-test-quality-review-result-{iteration}.json."
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "test-quality-review": test_quality_review_agent_definition(
                        constitution,
                        spec,
                        plan,
                        tasks_content,
                        test_principles,
                        feature,
                        iteration,
                        quality_violations,
                    )
                },
                setting_sources=["project"],
            ),
        )

    # --- Step 2: Two-gate loop (test critic → test quality review) ---
    await run_two_gate_loop(
        log,
        spec_dir,
        feature,
        max_iterations,
        gate1=GateSpec(
            CRITIC_RESULT_PREFIX, "ch_3_test_critic.py", "test", "test critic", build_critic_query
        ),
        gate2=GateSpec(
            TEST_QUALITY_RESULT_PREFIX,
            "ch_3_test_quality_critic.py",
            "test-quality-review",
            "test quality review",
            build_quality_query,
        ),
        resume_state=resume_state,
        skip_fix_agent=_skip_fix_agent,
        run_revision=run_revision,
        on_both_pass=on_both_pass,
        escalation_kwargs={
            "escalation_filename": "ch-3-test-critic-escalation.md",
            "log_description": "test phase failed review",
            "review_history_prefixes": [
                (CRITIC_RESULT_PREFIX, "Test Critic"),
                (TEST_QUALITY_RESULT_PREFIX, "Test Quality Review"),
            ],
            "title": "Test Review Escalation",
            "summary": (
                "The automated test review loop exhausted its iteration limit without producing\n"
                "test files that passed both the test critic and the test quality review. Human\n"
                "review is required to resolve the outstanding violations before implementation\n"
                "can begin."
            ),
            "required_action": (
                "1. Review the violations above.\n"
                "2. Fix the BLOCKING violations manually in the test files.\n"
                "3. Re-run `python .claude/agents/ch_3_test_auto.py` to restart the automated loop,\n"
                "   or run `/ch-3-test-critic` and `/ch-3-test-quality-review` manually to verify your fixes.\n"
                "4. Once both gates pass, run `/ch-4-implement-auto` to start implementation."
            ),
        },
    )


# Entry point

if __name__ == "__main__":
    run_cli(AGENT_NAME, "Test phase auto-orchestrator", run)
