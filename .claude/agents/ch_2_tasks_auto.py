#!/usr/bin/env python3
"""
.claude/agents/ch_2_tasks_auto.py

Agentic orchestrator for automated task generation and critic loop.
Run manually via /ch-2-tasks-auto after reviewing the plan.
Runs independently of any Claude Code interactive session.

Usage:
  python .claude/agents/ch_2_tasks_auto.py
  python .claude/agents/ch_2_tasks_auto.py --feature 014-rich-text-formatting

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

import sys
from pathlib import Path

from ch_2_tasks_critic import build_tasks_critic_prompt
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query

from agent_common.console import make_logger, setup_log_file, stream_query
from agent_common.critic_loop import (
    GateSpec,
    finish_if_already_passing,
    finish_stage,
    run_cli,
    run_single_gate_loop,
)
from agent_common.files import read_file, require_spec_files
from agent_common.preflight_checks import task_format_violations
from agent_common.resume_state import (
    extend_iterations_if_reviewed,
    format_violations_block,
    load_prior_violations,
    next_iteration,
)

AGENT_NAME = "ch-2-tasks-auto"
RESULT_PREFIX = "ch-2-tasks-critic-result"
log = make_logger(AGENT_NAME)


# Pre-flight checks


def preflight(spec_dir: Path, feature: str) -> bool:
    """
    Returns True if tasks.md should be regenerated from scratch.
    Exits on unrecoverable conditions.
    """
    require_spec_files(log, spec_dir, "plan.md")

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


# Subagent definitions


def tasks_agent_definition(
    constitution: str, spec: str, plan: str, data_model: str
) -> AgentDefinition:
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
- Every implementation task must have a paired test task (unit/component or e2e, per the project's approved test stack in the constitution's Stack Constraints section)
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
    violations_block = format_violations_block(
        violations, iteration, "violations (already addressed by the tasks agent)"
    )

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/ch-2-tasks-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-2-tasks-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates tasks.md against plan.md, spec.md, and constitution.md. Returns structured JSON.",
        prompt=build_tasks_critic_prompt(
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


async def _run_format_gate(
    feature: str, spec_dir: Path, constitution: str, spec: str, plan: str, data_model: str
) -> None:
    """Deterministic §T5 pre-check: numbered lists and Txxx lines with a
    missing/misplaced checkbox are unambiguous regex-level violations that
    don't need an LLM critic round-trip to catch. Kick back to the tasks
    agent directly (up to 2 attempts) before entering the critic loop —
    this is a mechanical reformat, not a judgment call, so no violation
    reasoning is needed beyond quoting the offending lines."""
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        tasks = read_file(spec_dir / "tasks.md")
        violations = task_format_violations(tasks)
        if not violations:
            return

        log(
            f"Deterministic §T5 format check found {len(violations)} violation(s) "
            f"(attempt {attempt}/{max_attempts}) — kicking back to tasks agent before critic loop..."
        )
        await stream_query(
            query(
                prompt=(
                    f"Reformat specs/{feature}/tasks.md to comply with the machine-readable task "
                    f"format `- [ ] TXXX [TEST|IMPL] [PY] [USZ] description` (see .specify/templates/"
                    f"tasks-template.md). Fix ONLY the lines below — do not change task content, order, "
                    f"or numbering beyond what's needed to fix the format:\n\n"
                    + "\n".join(violations)
                ),
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={
                        "tasks-agent": tasks_agent_definition(constitution, spec, plan, data_model)
                    },
                    setting_sources=["project"],
                ),
            )
        )

    remaining = task_format_violations(read_file(spec_dir / "tasks.md"))
    if remaining:
        log(
            f"§T5 format check still finds {len(remaining)} violation(s) after "
            f"{max_attempts} attempts — proceeding to critic loop, which will report them formally."
        )


# Main orchestration loop


async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting ch-2-tasks-auto for feature: {feature}")

    force_regen = preflight(spec_dir, feature)

    constitution = read_file(Path(".specify/memory/constitution.md"))
    spec = read_file(spec_dir / "spec.md")
    plan = read_file(spec_dir / "plan.md")

    data_model_path = spec_dir / "data-model.md"
    data_model = read_file(data_model_path) if data_model_path.exists() else ""

    max_iterations, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "ch-2-tasks-critic-escalation-review.md", RESULT_PREFIX, 3, log
    )

    # --- Step 1: Generate tasks.md if needed ---
    if not (spec_dir / "tasks.md").exists() or force_regen:
        log("Running tasks agent...")
        await stream_query(
            query(
                prompt=f"Generate tasks.md for feature {feature}. Write it to specs/{feature}/tasks.md.",
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={
                        "tasks-agent": tasks_agent_definition(constitution, spec, plan, data_model)
                    },
                    setting_sources=["project"],
                ),
            )
        )

        if not (spec_dir / "tasks.md").exists():
            log("ERROR: tasks agent did not produce tasks.md. Aborting.")
            sys.exit(1)

    # --- Step 1b: Deterministic §T5 format gate before the critic loop ---
    await _run_format_gate(feature, spec_dir, constitution, spec, plan, data_model)

    # --- Resume guard: exit if a previous run already achieved PASS ---
    if finish_if_already_passing(
        log,
        spec_dir,
        AGENT_NAME,
        RESULT_PREFIX,
        max_iterations,
        "tasks critic",
        "Tasks are ready for human review. No further action taken.",
        "after_tasks",
        "ch-2-tasks",
    ):
        return

    # --- Resume state: load violations from last FAIL so revision runs before next critic ---
    iteration = next_iteration(spec_dir, RESULT_PREFIX)
    violations = load_prior_violations(spec_dir, RESULT_PREFIX, iteration)

    async def run_fix(pending_iter: int, pending_violations: list) -> None:
        log(
            f"Running revision agent to address {len(pending_violations)} violation(s) from iteration {pending_iter}..."
        )
        await stream_query(
            query(
                prompt=(
                    f"Revise tasks.md for feature {feature} to fix critic violations. "
                    f"Read specs/{feature}/ch-2-tasks-critic-result-{pending_iter}.json for the violation list. "
                    f"Write updated tasks.md to specs/{feature}/tasks.md."
                ),
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={
                        "tasks-agent": tasks_agent_definition(constitution, spec, plan, data_model)
                    },
                    setting_sources=["project"],
                ),
            )
        )

    async def on_pass(result: dict) -> None:
        finish_stage(
            log,
            spec_dir,
            AGENT_NAME,
            "after_tasks",
            "ch-2-tasks",
            "Tasks are ready for human review. No further action taken.",
        )

    def build_critic_query(iteration: int, prev_violations):
        tasks = read_file(spec_dir / "tasks.md")
        return query(
            prompt=(
                f"Validate tasks.md for feature {feature}. "
                f"Write result to specs/{feature}/ch-2-tasks-critic-result-{iteration}.json."
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
        )

    # --- Step 2: Critic loop ---
    await run_single_gate_loop(
        log,
        spec_dir,
        feature,
        max_iterations,
        gate=GateSpec(
            RESULT_PREFIX, "ch_2_tasks_critic.py", "tasks", "tasks critic", build_critic_query
        ),
        resume_state=(iteration, violations),
        skip_fix_agent=_skip_fix_agent,
        run_fix=run_fix,
        on_pass=on_pass,
        escalation_kwargs={
            "escalation_filename": "ch-2-tasks-critic-escalation.md",
            "log_description": "tasks.md failed critic review",
            "review_history_prefixes": [(RESULT_PREFIX, "Tasks Critic")],
            "title": "Tasks Critic Escalation",
            "summary": (
                "The automated tasks-critic loop exhausted its iteration limit without producing\n"
                "a passing tasks.md. Human review is required to resolve the outstanding violations\n"
                "before task execution can proceed."
            ),
            "required_action": (
                f"1. Review the violations above.\n"
                f"2. Edit specs/{feature}/tasks.md manually to address the BLOCKING violations.\n"
                f"3. Re-run `python .claude/agents/ch_2_tasks_auto.py` to restart the automated loop,\n"
                f"   or run `/ch-2-tasks-critic` manually to verify your fixes."
            ),
        },
    )


# Entry point

if __name__ == "__main__":
    run_cli(AGENT_NAME, "Tasks auto-orchestrator", run)
