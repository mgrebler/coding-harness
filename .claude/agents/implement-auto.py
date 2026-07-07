#!/usr/bin/env python3
"""
.claude/agents/implement-auto.py

Agentic orchestrator for automated implementation and critic loop.
Run manually after tasks.md has been reviewed and is ready for implementation.
Runs independently of any Claude Code interactive session.

Usage:
  python .claude/agents/implement-auto.py
  python .claude/agents/implement-auto.py --feature 015-job-description-rich-text

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

import asyncio
import json
import subprocess
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
    find_two_gate_resume_state,
    format_violations_block,
    write_escalation,
    extend_iterations_if_reviewed,
    run_gate,
    require_spec_files,
)
from implement_critic import build_implement_critic_prompt
from quality_critic import build_quality_review_prompt

AGENT_NAME = "implement-auto"
CRITIC_RESULT_PREFIX = "implement-critic-result"
QUALITY_RESULT_PREFIX = "code-quality-review-result"
log = make_logger(AGENT_NAME)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def preflight(spec_dir: Path, feature: str):
    require_spec_files(log, spec_dir, "spec.md", "plan.md", "tasks.md")

    # Confirm test phase is complete
    test_critic_results = list(spec_dir.glob("test-critic-result-*.json"))
    if not test_critic_results:
        log("ERROR: Test phase not complete. Run /speckit-test-auto first.")
        sys.exit(1)
    passing = find_passing_iteration(spec_dir, "test-critic-result", 3)
    if passing is None:
        log("ERROR: No passing test-critic result found. Run /speckit-test-auto to resolve.")
        sys.exit(1)

    tasks_content = (spec_dir / "tasks.md").read_text(encoding="utf-8")
    all_done = "- [ ]" not in tasks_content
    existing_results = list(spec_dir.glob(f"{CRITIC_RESULT_PREFIX}-*.json"))

    if all_done and not existing_results:
        if sys.stdin.isatty():
            response = input(
                f"[{AGENT_NAME}] WARNING: All tasks in tasks.md appear complete but no critic result exists. "
                f"Run critic on existing implementation? (yes/no): "
            ).strip().lower()
        else:
            log("Non-interactive mode: defaulting to running critic on existing implementation.")
            response = "yes"
        if response != "yes":
            log("Aborted. No changes made.")
            sys.exit(0)


# ---------------------------------------------------------------------------
# Subagent definitions
# ---------------------------------------------------------------------------

def impl_agent_definition(constitution: str, spec: str, plan: str, tasks: str, quality_principles: str) -> AgentDefinition:
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

Inputs already loaded for you:

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
- Use only the approved stack: TypeScript, React, Hono, Prisma, tRPC + Zod, Vitest, Playwright, Tailwind
- No new npm dependencies
- Backend: router layer (backend/src/api/) calls service layer (backend/src/services/) only — no Prisma in the router
- Frontend: components (frontend/src/components/) receive props only — no tRPC hooks in components
- Styling: Tailwind utility classes only — no CSS Modules, no inline style props
- After each task, run: pnpm --filter backend exec tsc --noEmit && pnpm --filter frontend exec tsc --noEmit
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
        f"- After producing JSON, write it to specs/$FEATURE/implement-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [implement-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates implemented code against tasks.md, plan.md, spec.md, and constitution.md.",
        prompt=build_implement_critic_prompt(
            constitution, spec, plan, tasks, iteration,
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

Both the implement critic and code quality review have passed. However, the CI checks
(typecheck, unit tests, e2e) failed. Your sole function is to fix those failures.

Fix only what is broken by the failures below. Do not add features, do not refactor
passing code, do not change passing test assertions.

--- CI FAILURES ---
{ci_failures}

--- CONSTITUTION ---
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
- After all fixes, re-run the failing check to confirm it now passes:
    pnpm typecheck          (for typecheck failures)
    pnpm test:backend       (for backend unit test failures)
    pnpm test:frontend      (for frontend unit test failures)
    pnpm test:e2e           (for e2e failures)
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

--- CONSTITUTION ---
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
- After all fixes, run: pnpm --filter backend exec tsc --noEmit && pnpm --filter frontend exec tsc --noEmit
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
    violations_block = format_violations_block(quality_violations, iteration, "quality violations (already addressed by the fix agent)")

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/code-quality-review-result-{iteration}.json using Bash\n"
        f"- Print one line: [code-quality-review] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Reviews implemented code for quality, maintainability, and operational safety.",
        prompt=build_quality_review_prompt(
            constitution, spec, plan, tasks, quality_principles, iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


# ---------------------------------------------------------------------------
# CI check
# ---------------------------------------------------------------------------

CI_QUICK_COMMANDS = [
    ("typecheck", ["pnpm", "typecheck"]),
    ("backend unit tests", ["pnpm", "test:backend"]),
    ("frontend unit tests", ["pnpm", "test:frontend"]),
]

CI_FULL_COMMANDS = CI_QUICK_COMMANDS + [
    ("e2e tests", ["pnpm", "test:e2e"]),
]


def _run_commands(commands: list[tuple[str, list[str]]]) -> tuple[bool, str]:
    all_passed = True
    failures: list[str] = []
    for label, cmd in commands:
        log(f"CI: running {label}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
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
    """Run typecheck + unit tests only. Fast gate before the critic loop."""
    return _run_commands(CI_QUICK_COMMANDS)


def run_full_ci_checks() -> tuple[bool, str]:
    """Run all CI checks including e2e. Slow gate after both review gates pass."""
    return _run_commands(CI_FULL_COMMANDS)


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting implement-auto for feature: {feature}")

    preflight(spec_dir, feature)

    if stage_is_complete(spec_dir, "implement"):
        log("Implementation stage already complete — nothing to do.")
        return

    constitution = read_file(Path(".specify/memory/constitution.md"))
    spec = read_file(spec_dir / "spec.md")
    plan = read_file(spec_dir / "plan.md")
    tasks = read_file(spec_dir / "tasks.md")

    quality_principles_path = Path(".specify/memory/code-quality-principles.md")
    quality_principles = read_file(quality_principles_path) if quality_principles_path.exists() else "(code-quality-principles.md not found)"

    MAX_ITERATIONS, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "implement-critic-escalation-review.md", CRITIC_RESULT_PREFIX, 3, log
    )

    # --- Step 1: Run implementation if unchecked tasks remain ---
    if "- [ ]" in tasks:
        log("Running implementation agent...")
        async for message in query(
            prompt=(
                f"Implement all unchecked tasks in specs/{feature}/tasks.md. "
                f"Follow TDD order: write failing tests first, commit, then implement, commit. "
                f"Mark each task - [x] in tasks.md after completing it."
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "impl-agent": impl_agent_definition(constitution, spec, plan, tasks, quality_principles)
                },
                setting_sources=["project"],
            ),
        ):
            log_sdk_message(message, prefix="  ")

        tasks = read_file(spec_dir / "tasks.md")
        if "- [ ]" in tasks:
            log("WARNING: implementation agent did not complete all tasks. Proceeding to critic anyway.")
    else:
        log("All tasks already checked off — skipping implementation agent.")

    # --- Step 1b: Quick CI check (typecheck + unit tests) before critic loop ---
    # Catches compile errors and broken tests early — no point running the critic on broken code.
    # e2e runs later, once both review gates pass, to avoid the ~3 min cost on every iteration.
    if not find_passing_iteration(spec_dir, CRITIC_RESULT_PREFIX, MAX_ITERATIONS):
        log("Running quick CI checks (typecheck + unit tests) before critic loop...")
        quick_passed, quick_failure_summary = run_quick_ci_checks()
        if not quick_passed:
            log("Quick CI failed — running CI fix agent (one attempt) before entering critic loop...")
            async for message in query(
                prompt=(
                    f"Fix CI failures for feature {feature}. "
                    f"Failures:\n{quick_failure_summary}"
                ),
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={
                        "ci-fix-agent": ci_fix_agent_definition(
                            constitution, spec, plan, tasks, quick_failure_summary
                        )
                    },
                    setting_sources=["project"],
                ),
            ):
                log_sdk_message(message, prefix="  ")

            log("Re-running quick CI checks after fix attempt...")
            quick_passed, quick_failure_summary = run_quick_ci_checks()
            if not quick_passed:
                log("FAIL: Quick CI still failing after fix attempt. Human review required.")
                log(f"Remaining failures:\n{quick_failure_summary}")
                sys.exit(1)

        log("Quick CI passed — proceeding to critic loop.")

    # --- Resume guard: done if quality review already passed AND full CI is clean ---
    passing = find_passing_iteration(spec_dir, QUALITY_RESULT_PREFIX, MAX_ITERATIONS)
    if passing is not None:
        log(f"Already PASS from quality review iteration {passing}.")
        log("Running CI checks before finalising...")
        ci_passed, ci_failure_summary = run_full_ci_checks()
        if ci_passed:
            log("All CI checks passed. Implementation is ready for human review.")
            run_auto_commit("after_implement", AGENT_NAME)
            write_stage_complete(spec_dir, "implement")
            return
        log("CI checks failed — running CI fix agent (one attempt)...")
        async for message in query(
            prompt=(
                f"Fix CI failures for feature {feature}. "
                f"Failures:\n{ci_failure_summary}"
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "ci-fix-agent": ci_fix_agent_definition(
                        constitution, spec, plan, tasks, ci_failure_summary
                    )
                },
                setting_sources=["project"],
            ),
        ):
            log_sdk_message(message, prefix="  ")

        log("Re-running CI checks after fix attempt...")
        ci_passed, ci_failure_summary = run_full_ci_checks()
        if not ci_passed:
            log("FAIL: CI checks still failing after fix attempt. Human review required.")
            log(f"Remaining failures:\n{ci_failure_summary}")
            sys.exit(1)

        log("All CI checks passed. Implementation is ready for human review.")
        run_auto_commit("after_implement", AGENT_NAME)
        write_stage_complete(spec_dir, "implement")
        return

    # --- Resume state: determine where we left off ---
    # Use result files as idempotency markers; carry forward violations from any incomplete gate.
    iteration = next_iteration(spec_dir, CRITIC_RESULT_PREFIX)
    iteration, critic_violations, quality_violations = find_two_gate_resume_state(
        spec_dir, CRITIC_RESULT_PREFIX, QUALITY_RESULT_PREFIX, iteration
    )
    if _skip_fix_agent and (critic_violations or quality_violations):
        log("Escalation review present — skipping fix agent; violations were resolved externally.")
        critic_violations = None
        quality_violations = None
    elif critic_violations:
        log(f"Resuming after critic FAIL at iteration {iteration - 1} — fix agent will run before critic {iteration}.")
    elif quality_violations:
        log(f"Resuming after quality FAIL at iteration {iteration - 1} — fix agent will run before critic {iteration}.")
    elif iteration < next_iteration(spec_dir, CRITIC_RESULT_PREFIX):
        log(f"Resuming: critic {iteration} already PASS — quality review will run for iteration {iteration}.")

    # --- Step 2: Two-gate loop (implement critic → code quality review) ---
    while iteration <= MAX_ITERATIONS:
        critic_path = spec_dir / f"{CRITIC_RESULT_PREFIX}-{iteration}.json"
        quality_path_iter = spec_dir / f"{QUALITY_RESULT_PREFIX}-{iteration}.json"
        tasks_content = read_file(spec_dir / "tasks.md")

        # --- Gate 1: Implement critic ---
        if not critic_path.exists():
            # Apply fix first if violations are pending from a previous gate failure.
            if critic_violations or quality_violations:
                pending_violations = critic_violations or quality_violations
                pending_label = "critic" if critic_violations else "quality review"
                pending_iter = iteration - 1
                log(f"Running fix agent for {pending_label} violations from iteration {pending_iter} ({len(pending_violations)} issue(s))...")
                async for message in query(
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
                ):
                    log_sdk_message(message, prefix="  ")

            prev_critic_violations = critic_violations  # Pass as context to critic
            critic_violations = None
            quality_violations = None

            log(f"Running implement critic (iteration {iteration})...")

            await run_gate(
                log, "implement", "implement_critic.py", feature, iteration, "implement critic",
                lambda: query(
                    prompt=(
                        f"Validate the implementation for feature {feature}. "
                        f"Write result to specs/{feature}/implement-critic-result-{iteration}.json."
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "implement-critic": critic_agent_definition(
                                constitution, spec, plan, tasks_content, iteration, prev_critic_violations
                            )
                        },
                        setting_sources=["project"],
                    ),
                ),
            )

            if not critic_path.exists():
                log(f"ERROR: critic did not write result file for iteration {iteration}. Aborting.")
                sys.exit(1)
        else:
            log(f"Critic result for iteration {iteration} already exists — reading status.")

        critic_result = read_result(spec_dir, CRITIC_RESULT_PREFIX, iteration)
        critic_status = critic_result.get("status", "FAIL")
        blocking = sum(1 for v in critic_result.get("violations", []) if v.get("severity") == "BLOCKING")
        warnings = sum(1 for v in critic_result.get("violations", []) if v.get("severity") == "WARNING")

        if critic_status == "FAIL":
            log(f"Critic FAIL (iteration {iteration}) — {blocking} blocking, {warnings} warning(s).")
            critic_violations = critic_result.get("violations", [])
            iteration += 1
            continue

        log(f"Critic PASS (iteration {iteration}) — {warnings} warning(s).")

        # --- Gate 2: Code quality review ---
        if not quality_path_iter.exists():
            log(f"Running code quality review (iteration {iteration})...")

            await run_gate(
                log, "quality", "quality_critic.py", feature, iteration, "code quality review",
                lambda: query(
                    prompt=(
                        f"Review the implementation for feature {feature} for code quality. "
                        f"Write result to specs/{feature}/code-quality-review-result-{iteration}.json."
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "quality-review": quality_review_agent_definition(
                                constitution, spec, plan, tasks_content, quality_principles, iteration, quality_violations
                            )
                        },
                        setting_sources=["project"],
                    ),
                ),
            )

            if not quality_path_iter.exists():
                log(f"ERROR: quality review did not write result file for iteration {iteration}. Aborting.")
                sys.exit(1)
        else:
            log(f"Quality review result for iteration {iteration} already exists — reading status.")

        quality_result = read_result(spec_dir, QUALITY_RESULT_PREFIX, iteration)
        quality_status = quality_result.get("status", "FAIL")
        confidence = quality_result.get("confidence", 0)

        if quality_status == "PASS":
            log(f"Quality review PASS (iteration {iteration}, confidence {confidence}/10).")
            log("Running CI checks before finalising...")
            ci_passed, ci_failure_summary = run_full_ci_checks()
            if not ci_passed:
                log("CI checks failed — running CI fix agent (one attempt)...")
                tasks_content = read_file(spec_dir / "tasks.md")
                async for message in query(
                    prompt=(
                        f"Fix CI failures for feature {feature}. "
                        f"Failures:\n{ci_failure_summary}"
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "ci-fix-agent": ci_fix_agent_definition(
                                constitution, spec, plan, tasks_content, ci_failure_summary
                            )
                        },
                        setting_sources=["project"],
                    ),
                ):
                    log_sdk_message(message, prefix="  ")

                log("Re-running CI checks after fix attempt...")
                ci_passed, ci_failure_summary = run_full_ci_checks()
                if not ci_passed:
                    log("FAIL: CI checks still failing after fix attempt. Human review required.")
                    log(f"Remaining failures:\n{ci_failure_summary}")
                    sys.exit(1)

            log("All CI checks passed. Implementation is ready for human review.")
            run_auto_commit("after_implement", AGENT_NAME)
            write_stage_complete(spec_dir, "implement")
            return

        blocking_quality = len(quality_result.get("blocking_issues", []))
        log(f"Quality review FAIL (iteration {iteration}) — {blocking_quality} blocking issue(s), confidence {confidence}/10.")
        quality_violations = quality_result.get("blocking_issues", [])
        iteration += 1

    # --- Escalation ---
    write_escalation(
        spec_dir=spec_dir,
        feature=feature,
        escalation_filename="implement-critic-escalation.md",
        log_description="implementation failed review",
        review_history_prefixes=[
            (CRITIC_RESULT_PREFIX, "Implement Critic"),
            (QUALITY_RESULT_PREFIX, "Code Quality Review"),
        ],
        max_iterations=MAX_ITERATIONS,
        title="Implement Critic Escalation",
        summary=(
            "The automated implement-critic loop exhausted its iteration limit without producing\n"
            "an implementation that passed both the implement critic and the code quality review.\n"
            "Human review is required to resolve the outstanding violations before the branch can\n"
            "be merged."
        ),
        required_action=(
            "1. Review the violations above.\n"
            "2. Fix the BLOCKING violations manually in the relevant source files.\n"
            f"3. Re-run `python .claude/agents/implement-auto.py` to restart the automated loop,\n"
            f"   or run `/speckit-implement-critic` and `/code-quality-review` manually to verify your fixes."
        ),
        log_fn=log,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Implementation auto-orchestrator")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    args = parser.parse_args()

    feature = args.feature or get_feature_from_branch(AGENT_NAME)
    asyncio.run(run(feature))
