#!/usr/bin/env python3
"""
.claude/agents/ch_1_plan_auto.py

Agentic orchestrator for automated plan generation and critic loop.
Run manually via /ch-1-plan-auto after reviewing the spec.
Runs independently of any Claude Code interactive session.

Usage:
  python .claude/agents/ch_1_plan_auto.py
  python .claude/agents/ch_1_plan_auto.py --feature 013-job-list-sort-filter

Requirements:
  pip install claude-agent-sdk

The script derives the feature from the current git branch if --feature
is not supplied, matching the behaviour of the speckit skills.

Loop structure per iteration:
  1. Plan critic  — validates spec/constitution/traceability compliance
  2. Architecture review — validates architecture quality and best practices
  Both gates must PASS in the same iteration before the plan is committed.

Resume behaviour:
  Re-running after an interruption continues from the last incomplete step:
  - Result files act as idempotency markers: a gate is skipped if its result already exists
  - If the last critic result was FAIL, revision runs before the next critic
  - If critic PASS but arch review not yet run, resumes at the same iteration number
  - If the last arch result was FAIL, revision runs before the next critic
"""

import sys
from pathlib import Path

from ch_1_plan_architecture_critic import build_architecture_review_prompt
from ch_1_plan_critic import build_plan_critic_prompt
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
from agent_common.resume_state import (
    extend_iterations_if_reviewed,
    find_two_gate_resume_state,
    format_violations_block,
    next_iteration,
)

AGENT_NAME = "ch-1-plan-auto"
CRITIC_RESULT_PREFIX = "ch-1-plan-critic-result"
ARCH_RESULT_PREFIX = "ch-1-plan-architecture-review-result"
log = make_logger(AGENT_NAME)


# Pre-flight checks


def preflight(spec_dir: Path, feature: str) -> bool:
    """
    Returns True if plan.md should be regenerated from scratch.
    Exits on unrecoverable conditions.
    """
    require_spec_files(log, spec_dir, "spec.md")

    existing_plan = (spec_dir / "plan.md").exists()
    existing_results = list(spec_dir.glob(f"{CRITIC_RESULT_PREFIX}-*.json"))

    if existing_plan and not existing_results:
        print(
            f"[{AGENT_NAME}] WARNING: {spec_dir}/plan.md exists with no critic results.\n"
            f"  resume — run critic on existing plan.md (default)\n"
            f"  regen  — regenerate plan.md from scratch\n"
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


def plan_agent_definition(constitution: str, spec: str, arch_principles: str) -> AgentDefinition:
    return AgentDefinition(
        description="Generates plan.md for the current feature from spec.md and constitution.md.",
        prompt=f"""You are the Plan Agent for a spec-kit project.

Your sole function is to produce a complete and valid plan.md for the current feature.

You must follow the structure defined in the plan template at .specify/templates/plan.md.
Read that template first, then produce plan.md.

Inputs already loaded for you:

--- CONSTITUTION ---
{constitution}

--- SPEC ---
{spec}

--- ARCHITECTURE PRINCIPLES ---
{arch_principles}

Key rules:
- Use .specify/scripts/bash/setup-plan.sh --json to get FEATURE_SPEC, IMPL_PLAN, SPECS_DIR, BRANCH
- Execute all planning phases: research.md, data-model.md, contracts/, quickstart.md
- Write plan.md to the correct specs/$FEATURE/ path
- Include a Constitution Check section covering every applicable section
- The plan will be evaluated by an Architecture Review agent using the principles above — design accordingly
- Do not stop until plan.md is written to disk
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def critic_agent_definition(
    constitution: str,
    architecture: str,
    spec: str,
    plan: str,
    iteration: int,
    violations: list | None = None,
) -> AgentDefinition:
    violations_block = format_violations_block(
        violations, iteration, "violations (already addressed by the plan agent)"
    )

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/ch-1-plan-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-1-plan-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates plan.md against constitution.md, architecture.md, and spec.md. Returns structured JSON.",
        prompt=build_plan_critic_prompt(
            constitution,
            architecture,
            spec,
            plan,
            iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


def arch_review_agent_definition(
    constitution: str,
    architecture: str,
    spec: str,
    plan: str,
    arch_principles: str,
    iteration: int,
    arch_violations: list | None = None,
) -> AgentDefinition:
    violations_block = format_violations_block(
        arch_violations, iteration, "architecture violations (already addressed by the plan agent)"
    )

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/ch-1-plan-architecture-review-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-1-plan-architecture-review] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Reviews plan.md for architecture quality, best practices, and operational safety.",
        prompt=build_architecture_review_prompt(
            constitution,
            architecture,
            spec,
            plan,
            arch_principles,
            iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


# Main orchestration loop


async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting ch-1-plan-auto for feature: {feature}")

    force_regen = preflight(spec_dir, feature)

    constitution = read_file(Path(".specify/memory/constitution.md"))
    spec = read_file(spec_dir / "spec.md")

    arch_path = Path(".specify/memory/architecture.md")
    architecture = read_file(arch_path) if arch_path.exists() else "(architecture.md not found)"

    arch_principles_path = Path(".specify/memory/architecture-principles.md")
    arch_principles = (
        read_file(arch_principles_path)
        if arch_principles_path.exists()
        else "(architecture-principles.md not found)"
    )

    MAX_ITERATIONS, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "ch-1-plan-critic-escalation-review.md", CRITIC_RESULT_PREFIX, 3, log
    )

    # --- Step 1: Generate plan.md if needed ---
    if not (spec_dir / "plan.md").exists() or force_regen:
        log("Running plan agent...")
        await stream_query(
            query(
                prompt=f"Generate plan.md for feature {feature}. Write it to specs/{feature}/plan.md.",
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={
                        "plan-agent": plan_agent_definition(constitution, spec, arch_principles)
                    },
                    setting_sources=["project"],
                ),
            )
        )

        if not (spec_dir / "plan.md").exists():
            log("ERROR: plan agent did not produce plan.md. Aborting.")
            sys.exit(1)

    # --- Resume guard: done if architecture review already passed ---
    if finish_if_already_passing(
        log,
        spec_dir,
        AGENT_NAME,
        ARCH_RESULT_PREFIX,
        MAX_ITERATIONS,
        "architecture review",
        "Plan is ready for human review. No further action taken.",
        "after_plan",
        "ch-1-plan",
    ):
        return

    # --- Resume state: determine where we left off ---
    # Use result files as idempotency markers; carry forward violations from any incomplete gate.
    iteration = next_iteration(spec_dir, CRITIC_RESULT_PREFIX)
    resume_state = find_two_gate_resume_state(
        spec_dir, CRITIC_RESULT_PREFIX, ARCH_RESULT_PREFIX, iteration
    )

    async def run_revision(pending_iter: int, pending_violations: list, pending_label: str) -> None:
        pending_file = (
            f"specs/{feature}/ch-1-plan-critic-result-{pending_iter}.json"
            if pending_label == "plan critic"
            else f"specs/{feature}/ch-1-plan-architecture-review-result-{pending_iter}.json"
        )
        log(
            f"Running plan revision for {pending_label} violations from iteration {pending_iter}..."
        )
        await stream_query(
            query(
                prompt=(
                    f"Revise plan.md for feature {feature}. "
                    f"Read {pending_file} for the full violation list. "
                    f"Write updated plan.md to specs/{feature}/plan.md."
                ),
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={
                        "plan-agent": plan_agent_definition(constitution, spec, arch_principles)
                    },
                    setting_sources=["project"],
                ),
            )
        )

    async def on_both_pass(arch_result: dict) -> None:
        finish_stage(
            log,
            spec_dir,
            AGENT_NAME,
            "after_plan",
            "ch-1-plan",
            "Plan is ready for human review. No further action taken.",
        )

    def build_critic_query(iteration: int, prev_violations):
        plan = read_file(spec_dir / "plan.md")
        return query(
            prompt=(
                f"Validate plan.md for feature {feature}. "
                f"Write result to specs/{feature}/ch-1-plan-critic-result-{iteration}.json."
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "plan-critic": critic_agent_definition(
                        constitution, architecture, spec, plan, iteration, prev_violations
                    )
                },
                setting_sources=["project"],
            ),
        )

    def build_arch_query(iteration: int, arch_violations):
        plan = read_file(spec_dir / "plan.md")
        return query(
            prompt=(
                f"Review plan.md for feature {feature} for architectural quality. "
                f"Write result to specs/{feature}/ch-1-plan-architecture-review-result-{iteration}.json."
            ),
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "architecture-review": arch_review_agent_definition(
                        constitution,
                        architecture,
                        spec,
                        plan,
                        arch_principles,
                        iteration,
                        arch_violations,
                    )
                },
                setting_sources=["project"],
            ),
        )

    # --- Step 2: Two-gate loop (critic → architecture review) ---
    await run_two_gate_loop(
        log,
        spec_dir,
        feature,
        MAX_ITERATIONS,
        gate1=GateSpec(
            CRITIC_RESULT_PREFIX, "ch_1_plan_critic.py", "plan", "plan critic", build_critic_query
        ),
        gate2=GateSpec(
            ARCH_RESULT_PREFIX,
            "ch_1_plan_architecture_critic.py",
            "plan-architecture-review",
            "architecture review",
            build_arch_query,
        ),
        resume_state=resume_state,
        skip_fix_agent=_skip_fix_agent,
        run_revision=run_revision,
        on_both_pass=on_both_pass,
        escalation_kwargs={
            "escalation_filename": "ch-1-plan-critic-escalation.md",
            "log_description": "plan.md failed review",
            "review_history_prefixes": [
                (CRITIC_RESULT_PREFIX, "Plan Critic"),
                (ARCH_RESULT_PREFIX, "Architecture Review"),
            ],
            "title": "Plan Review Escalation",
            "summary": (
                "The automated plan review loop exhausted its iteration limit without producing\n"
                "a plan that passed both the plan critic and the architecture review. Human review\n"
                "is required to resolve the outstanding violations before planning can proceed."
            ),
            "required_action": (
                f"1. Review the violations above.\n"
                f"2. Edit specs/{feature}/plan.md manually to address the BLOCKING violations.\n"
                f"3. Re-run `python .claude/agents/ch_1_plan_auto.py` to restart the automated loop,\n"
                f"   or run `/ch-1-plan-critic` and `/ch-1-plan-architecture-review` manually to verify your fixes."
            ),
        },
    )


# Entry point

if __name__ == "__main__":
    run_cli(AGENT_NAME, "Plan auto-orchestrator", run)
