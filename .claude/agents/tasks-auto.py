#!/usr/bin/env python3
"""
.claude/agents/tasks-auto.py

Agentic orchestrator for automated task generation and critic loop.
Run manually via /speckit-tasks-auto after reviewing the plan.
Runs independently of any Claude Code interactive session.

Usage:
  python .claude/agents/tasks-auto.py
  python .claude/agents/tasks-auto.py --feature 014-rich-text-formatting

Requirements:
  pip install claude-agent-sdk

The script derives the feature from the current git branch if --feature
is not supplied, matching the behaviour of the speckit skills.

Resume behaviour:
  Re-running after an interruption continues from the last incomplete step:
  - If tasks.md exists and no critic results exist, runs the critic on it (no regeneration)
  - If the last critic result was FAIL, re-runs the revision agent before the next critic
  - If a critic result was already PASS, exits immediately (no work to do)
"""

import asyncio
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
    load_local_llm_config,
    run_critic_subprocess,
)
from tasks_critic import build_tasks_critic_prompt

AGENT_NAME = "tasks-auto"
RESULT_PREFIX = "tasks-critic-result"
log = make_logger(AGENT_NAME)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def preflight(spec_dir: Path, feature: str) -> bool:
    """
    Returns True if tasks.md should be regenerated from scratch.
    Exits on unrecoverable conditions.
    """
    if not (spec_dir / "plan.md").exists():
        log(f"ERROR: {spec_dir}/plan.md not found. Cannot proceed.")
        sys.exit(1)

    existing_tasks = (spec_dir / "tasks.md").exists()
    existing_results = list(spec_dir.glob(f"{RESULT_PREFIX}-*.json"))

    if existing_tasks and not existing_results:
        print(
            f"[{AGENT_NAME}] WARNING: {spec_dir}/tasks.md exists with no critic results.\n"
            f"  resume — run critic on existing tasks.md (default)\n"
            f"  regen  — regenerate tasks.md from scratch\n"
            f"  abort  — exit without changes"
        )
        if sys.stdin.isatty():
            response = input(f"[{AGENT_NAME}] Choice [resume]: ").strip().lower()
        else:
            log("Non-interactive mode: defaulting to 'resume'.")
            response = "resume"
        if response == "abort":
            log("Aborted. No changes made.")
            sys.exit(0)
        return response == "regen"

    return False


# ---------------------------------------------------------------------------
# Subagent definitions
# ---------------------------------------------------------------------------

def tasks_agent_definition(constitution: str, spec: str, plan: str, data_model: str) -> AgentDefinition:
    data_model_block = f"\n--- DATA MODEL ---\n{data_model}" if data_model else ""
    return AgentDefinition(
        description="Generates tasks.md for the current feature from plan.md, spec.md, and supporting artifacts.",
        prompt=f"""You are the Tasks Agent for a spec-kit project.

Your sole function is to produce a complete and valid tasks.md for the current feature.

You must follow the structure defined in the tasks template at .specify/templates/tasks-template.md.
Read that template first, then produce tasks.md.

Inputs already loaded for you:

--- CONSTITUTION ---
{constitution}

--- SPEC ---
{spec}

--- PLAN ---
{plan}
{data_model_block}

Key rules:
- Use .specify/scripts/bash/setup-plan.sh --json to get FEATURE_SPEC, IMPL_PLAN, SPECS_DIR, BRANCH
- Also read contracts/ and research.md from the spec directory if they exist
- Organise tasks by phase: Setup → Foundational → one phase per user story (in spec priority order) → Polish
- Every task must follow the checklist format: - [ ] T### [P?] [US#?] Description — file/path
- Mark tasks [P] when they target different files with no dependencies
- Every implementation task must have a paired test task (Vitest unit/component or Playwright e2e)
- Include a Checkpoint at the end of each phase with a concrete runnable verification command
- Write tasks.md to the correct specs/$FEATURE/ path
- Do not stop until tasks.md is written to disk
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
    violations_block = format_violations_block(violations, iteration, "violations (already addressed by the tasks agent)")

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/tasks-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [tasks-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates tasks.md against plan.md, spec.md, and constitution.md. Returns structured JSON.",
        prompt=build_tasks_critic_prompt(
            constitution, spec, plan, tasks, iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting tasks-auto for feature: {feature}")

    force_regen = preflight(spec_dir, feature)

    constitution = read_file(Path(".specify/memory/constitution.md"))
    spec = read_file(spec_dir / "spec.md")
    plan = read_file(spec_dir / "plan.md")

    data_model_path = spec_dir / "data-model.md"
    data_model = read_file(data_model_path) if data_model_path.exists() else ""

    MAX_ITERATIONS, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "tasks-critic-escalation-review.md", RESULT_PREFIX, 3, log
    )

    # --- Step 1: Generate tasks.md if needed ---
    if not (spec_dir / "tasks.md").exists() or force_regen:
        log("Running tasks agent...")
        async for message in query(
            prompt=f"Generate tasks.md for feature {feature}. Write it to specs/{feature}/tasks.md.",
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "tasks-agent": tasks_agent_definition(constitution, spec, plan, data_model)
                },
                setting_sources=["project"],
            ),
        ):
            log_sdk_message(message, prefix="  ")

        if not (spec_dir / "tasks.md").exists():
            log("ERROR: tasks agent did not produce tasks.md. Aborting.")
            sys.exit(1)

    # --- Resume guard: exit if a previous run already achieved PASS ---
    passing = find_passing_iteration(spec_dir, RESULT_PREFIX, MAX_ITERATIONS)
    if passing is not None:
        log(f"Already PASS from iteration {passing}.")
        log("Tasks are ready for human review. No further action taken.")
        run_auto_commit("after_tasks", AGENT_NAME)
        write_stage_complete(spec_dir, "tasks")
        return

    # --- Resume state: load violations from last FAIL so revision runs before next critic ---
    iteration = next_iteration(spec_dir, RESULT_PREFIX)
    violations = load_prior_violations(spec_dir, RESULT_PREFIX, iteration)
    if _skip_fix_agent and violations:
        log("Escalation review present — skipping revision agent; violations were resolved externally.")
        violations = None
    elif violations:
        log(
            f"Resuming after FAIL at iteration {iteration - 1} "
            f"({len(violations)} violation(s)) — revision will run before critic {iteration}."
        )

    # --- Step 2: Critic loop ---
    while iteration <= MAX_ITERATIONS:
        result_path = spec_dir / f"{RESULT_PREFIX}-{iteration}.json"

        if not result_path.exists():
            # Apply revision if we have pending violations from the previous iteration.
            if violations:
                log(f"Running revision agent to address {len(violations)} violation(s) from iteration {iteration - 1}...")
                async for message in query(
                    prompt=(
                        f"Revise tasks.md for feature {feature} to fix critic violations. "
                        f"Read specs/{feature}/tasks-critic-result-{iteration - 1}.json for the violation list. "
                        f"Write updated tasks.md to specs/{feature}/tasks.md."
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "tasks-agent": tasks_agent_definition(constitution, spec, plan, data_model)
                        },
                        setting_sources=["project"],
                    ),
                ):
                    log_sdk_message(message, prefix="  ")

            prev_violations = violations  # Pass as context to critic (already addressed)
            violations = None

            log(f"Running critic agent (iteration {iteration})...")
            tasks = read_file(spec_dir / "tasks.md")

            llm_config = load_local_llm_config("tasks")
            if llm_config:
                log(f"Using local LLM ({llm_config['model']}) for tasks critic...")
                tasks_critic_script = Path(__file__).parent / "tasks_critic.py"
                returncode = run_critic_subprocess(
                    [sys.executable, str(tasks_critic_script),
                     "--feature", feature, "--iteration", str(iteration)],
                )
                if returncode == 2:
                    llm_config = None  # not configured; fall through to Claude
                elif returncode != 0:
                    log(f"ERROR: local LLM critic failed for iteration {iteration}. Aborting.")
                    sys.exit(1)

            if not llm_config:
                async for message in query(
                    prompt=(
                        f"Validate tasks.md for feature {feature}. "
                        f"Write result to specs/{feature}/tasks-critic-result-{iteration}.json."
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "tasks-critic": critic_agent_definition(
                                constitution, spec, plan, tasks, iteration, prev_violations
                            )
                        },
                        setting_sources=["project"],
                    ),
                ):
                    log_sdk_message(message, prefix="  ")

            if not result_path.exists():
                log(f"ERROR: critic did not write result file for iteration {iteration}. Aborting.")
                sys.exit(1)
        else:
            log(f"Critic result for iteration {iteration} already exists — reading status.")

        result = read_result(spec_dir, RESULT_PREFIX, iteration)
        status = result.get("status", "FAIL")
        blocking = sum(1 for v in result.get("violations", []) if v.get("severity") == "BLOCKING")
        warnings = sum(1 for v in result.get("violations", []) if v.get("severity") == "WARNING")

        if status == "PASS":
            log(f"PASS after {iteration} iteration(s) → {result_path}")
            log("Tasks are ready for human review. No further action taken.")
            run_auto_commit("after_tasks", AGENT_NAME)
            write_stage_complete(spec_dir, "tasks")
            return

        log(f"FAIL (iteration {iteration}) — {blocking} blocking, {warnings} warning(s).")
        violations = result.get("violations", [])
        iteration += 1

    # --- Escalation ---
    write_escalation(
        spec_dir=spec_dir,
        feature=feature,
        escalation_filename="tasks-critic-escalation.md",
        log_description="tasks.md failed critic review",
        review_history_prefixes=[(RESULT_PREFIX, "Tasks Critic")],
        max_iterations=MAX_ITERATIONS,
        title="Tasks Critic Escalation",
        summary=(
            "The automated tasks-critic loop exhausted its iteration limit without producing\n"
            "a passing tasks.md. Human review is required to resolve the outstanding violations\n"
            "before task execution can proceed."
        ),
        required_action=(
            f"1. Review the violations above.\n"
            f"2. Edit specs/{feature}/tasks.md manually to address the BLOCKING violations.\n"
            f"3. Re-run `python .claude/agents/tasks-auto.py` to restart the automated loop,\n"
            f"   or run `/speckit-tasks-critic` manually to verify your fixes."
        ),
        log_fn=log,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tasks auto-orchestrator")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    args = parser.parse_args()

    feature = args.feature or get_feature_from_branch(AGENT_NAME)
    asyncio.run(run(feature))
