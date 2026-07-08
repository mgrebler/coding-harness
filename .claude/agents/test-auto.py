#!/usr/bin/env python3
"""
.claude/agents/test-auto.py

Agentic orchestrator for the automated test phase loop.
Writes failing tests for all [TEST] tasks in tasks.md, then runs the test-critic
in a feedback loop until PASS or escalation.

Usage:
  python .claude/agents/test-auto.py
  python .claude/agents/test-auto.py --feature 015-job-description-rich-text

Requirements:
  pip install claude-agent-sdk

The script derives the feature from the current git branch if --feature
is not supplied, matching the behaviour of the speckit skills.

Loop structure:
  1. Test agent — writes failing tests for all unchecked [TEST] tasks in tasks.md,
     records red-output artifacts in specs/$FEATURE/test-results/<TASKID>-red.txt
  2. Test critic — validates test files against test-principles.md, spec.md, and constitution.md
  If critic FAIL: fix agent addresses violations in test files only, critic re-runs.
  Maximum 3 iterations before escalation.

Resume behaviour:
  Re-running after an interruption continues from the last incomplete step:
  - result files act as idempotency markers
  - If the last critic result was FAIL, fix agent runs before the next critic
  - Test agent is re-run only if unchecked [TEST] tasks remain in tasks.md
"""

import asyncio
import json
import sys
import argparse
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

from agent_common import (
    get_feature_from_branch,
    read_file,
    write_file,
    next_iteration,
    read_result,
    run_auto_commit,
    write_stage_complete,
    stage_is_complete,
    make_logger,
    log_sdk_message,
    setup_log_file,
    find_passing_iteration,
    load_prior_violations,
    format_violations_block,
    write_escalation,
    extend_iterations_if_reviewed,
    run_gate,
    require_spec_files,
)
from test_critic import build_test_critic_prompt

AGENT_NAME = "test-auto"
CRITIC_RESULT_PREFIX = "test-critic-result"
log = make_logger(AGENT_NAME)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

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
            response = input(
                f"[{AGENT_NAME}] WARNING: All [TEST] tasks appear complete but no critic result exists. "
                f"Run critic on existing test files? (yes/no): "
            ).strip().lower()
        else:
            log("Non-interactive mode: defaulting to running critic on existing test files.")
            response = "yes"
        if response != "yes":
            log("Aborted. No changes made.")
            sys.exit(0)


# ---------------------------------------------------------------------------
# Subagent definitions
# ---------------------------------------------------------------------------

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
backend/src/ or frontend/src/ directories.

Inputs already loaded for you:

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
- Backend tests: pnpm --filter backend test -- <test-file-path>
- Frontend component tests: pnpm --filter frontend test -- <test-file-path>
- E2E tests: pnpm test:e2e -- <test-file-path>
- Use only Vitest (unit/integration) or Playwright (e2e) — no other test libraries
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
        f"- After producing JSON, write it to specs/{feature}/test-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [test-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates test files against test-principles.md, spec.md, and constitution.md.",
        prompt=build_test_critic_prompt(
            constitution, spec, plan, tasks, test_principles, feature, iteration,
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
Fix ONLY test files. Do NOT touch implementation files (backend/src/, frontend/src/).
Do not add features. Do not change test assertions that are not part of a listed violation.

--- VIOLATIONS TO FIX ---
{json.dumps(violations, indent=2)}

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
- Read the file at each violation's location before modifying it
- Apply the minimum change that addresses the violation
- After all fixes, re-run the affected test files to confirm they still fail for the expected reason
- Update red-output artifacts in specs/*/test-results/ if the test output changes
- Commit fixed files: git add <test files> && git commit -m "fix: address test critic violations"
- Do not stop until every violation in the list is addressed and committed
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting test-auto for feature: {feature}")

    preflight(spec_dir, feature)

    constitution = read_file(Path(".specify/memory/constitution.md"))
    spec = read_file(spec_dir / "spec.md")
    plan = read_file(spec_dir / "plan.md")
    tasks = read_file(spec_dir / "tasks.md")

    test_principles_path = Path(".specify/memory/test-principles.md")
    test_principles = read_file(test_principles_path) if test_principles_path.exists() else "(test-principles.md not found)"

    MAX_ITERATIONS, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "test-critic-escalation-review.md", CRITIC_RESULT_PREFIX, 3, log
    )

    # --- Step 1: Run test agent if unchecked [TEST] tasks remain ---
    has_unchecked_tests = any(
        "- [ ]" in line and "[TEST]" in line
        for line in tasks.splitlines()
    )

    if has_unchecked_tests:
        log("Running test agent...")
        async for message in query(
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
        ):
            log_sdk_message(message, prefix="  ")

        tasks = read_file(spec_dir / "tasks.md")
        still_unchecked = any(
            "- [ ]" in line and "[TEST]" in line
            for line in tasks.splitlines()
        )
        if still_unchecked:
            log("WARNING: test agent did not complete all [TEST] tasks. Proceeding to critic anyway.")
    else:
        log("All [TEST] tasks already checked off — skipping test agent.")

    # --- Resume guard: done if critic already passed ---
    passing = find_passing_iteration(spec_dir, CRITIC_RESULT_PREFIX, MAX_ITERATIONS)
    if passing is not None:
        log(f"Already PASS from test critic iteration {passing}.")
        log("Test phase is ready for human review. No further action taken.")
        run_auto_commit("after_test", AGENT_NAME)
        write_stage_complete(spec_dir, "test")
        return

    # --- Resume state: load violations from last FAIL so fix agent runs before next critic ---
    iteration = next_iteration(spec_dir, CRITIC_RESULT_PREFIX)
    critic_violations = load_prior_violations(spec_dir, CRITIC_RESULT_PREFIX, iteration)
    if _skip_fix_agent and critic_violations:
        log("Escalation review present — skipping fix agent; violations were resolved externally.")
        critic_violations = None
    elif critic_violations:
        log(
            f"Resuming after FAIL at iteration {iteration - 1} "
            f"({len(critic_violations)} violation(s)) — fix agent will run before critic {iteration}."
        )

    while iteration <= MAX_ITERATIONS:
        critic_path = spec_dir / f"{CRITIC_RESULT_PREFIX}-{iteration}.json"
        tasks_content = read_file(spec_dir / "tasks.md")

        if not critic_path.exists():
            # Run fix agent first if violations are pending (skip on first pass after escalation review)
            if critic_violations and not _skip_fix_agent:
                log(f"Running fix agent for critic violations from iteration {iteration - 1} ({len(critic_violations)} issue(s))...")
                async for message in query(
                    prompt=(
                        f"Fix test critic violations for feature {feature}. "
                        f"Violations: {json.dumps(critic_violations)}"
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "test-fix-agent": test_fix_agent_definition(
                                constitution, spec, plan, tasks_content, test_principles, critic_violations
                            )
                        },
                        setting_sources=["project"],
                    ),
                ):
                    log_sdk_message(message, prefix="  ")

            prev_violations = critic_violations
            critic_violations = None
            _skip_fix_agent = False  # only skip on first new iteration after escalation review

            log(f"Running test critic (iteration {iteration})...")

            await run_gate(
                log, "test", "test_critic.py", feature, iteration, "test critic",
                lambda: query(
                    prompt=(
                        f"Validate the test files for feature {feature}. "
                        f"Write result to specs/{feature}/test-critic-result-{iteration}.json."
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "test-critic": test_critic_agent_definition(
                                constitution, spec, plan, tasks_content, test_principles,
                                feature, iteration, prev_violations
                            )
                        },
                        setting_sources=["project"],
                    ),
                ),
            )

            if not critic_path.exists():
                log(f"ERROR: test critic did not write result file for iteration {iteration}. Aborting.")
                sys.exit(1)
        else:
            log(f"Test critic result for iteration {iteration} already exists — reading status.")

        critic_result = read_result(spec_dir, CRITIC_RESULT_PREFIX, iteration)
        critic_status = critic_result.get("status", "FAIL")
        blocking = sum(1 for v in critic_result.get("violations", []) if v.get("severity") == "BLOCKING")
        warnings = sum(1 for v in critic_result.get("violations", []) if v.get("severity") == "WARNING")

        if critic_status == "FAIL":
            log(f"Test critic FAIL (iteration {iteration}) — {blocking} blocking, {warnings} warning(s).")
            critic_violations = critic_result.get("violations", [])
            iteration += 1
            continue

        log(f"Test critic PASS (iteration {iteration}) — {warnings} warning(s).")
        log("Test phase complete. Implementation may now begin.")
        run_auto_commit("after_test", AGENT_NAME)
        write_stage_complete(spec_dir, "test")
        return

    # --- Escalation ---
    write_escalation(
        spec_dir=spec_dir,
        feature=feature,
        escalation_filename="test-critic-escalation.md",
        log_description="test phase failed critic",
        review_history_prefixes=[(CRITIC_RESULT_PREFIX, "Test Critic")],
        max_iterations=MAX_ITERATIONS,
        title="Test Critic Escalation",
        summary=(
            "The automated test-critic loop exhausted its iteration limit without producing\n"
            "test files that passed all critic rules. Human review is required to resolve\n"
            "the outstanding violations before implementation can begin."
        ),
        required_action=(
            "1. Review the violations above.\n"
            "2. Fix the BLOCKING violations manually in the test files.\n"
            f"3. Re-run `python .claude/agents/test-auto.py` to restart the automated loop,\n"
            f"   or run `/speckit-test-critic` manually to verify your fixes.\n"
            "4. Once the critic passes, run `/speckit-implement-auto` to start implementation."
        ),
        log_fn=log,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test phase auto-orchestrator")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    args = parser.parse_args()

    feature = args.feature or get_feature_from_branch(AGENT_NAME)
    asyncio.run(run(feature))
