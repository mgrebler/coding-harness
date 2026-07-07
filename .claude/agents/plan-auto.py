#!/usr/bin/env python3
"""
.claude/agents/plan-auto.py

Agentic orchestrator for automated plan generation and critic loop.
Run manually via /speckit-plan-auto after reviewing the spec.
Runs independently of any Claude Code interactive session.

Usage:
  python .claude/agents/plan-auto.py
  python .claude/agents/plan-auto.py --feature 013-job-list-sort-filter

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

import asyncio
import sys
import argparse
import subprocess
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
    run_gate,
    require_spec_files,
    find_passing_iteration,
    find_two_gate_resume_state,
    format_violations_block,
    write_escalation,
    extend_iterations_if_reviewed,
)
from plan_critic import build_plan_critic_prompt
from architecture_critic import build_architecture_review_prompt

AGENT_NAME = "plan-auto"
CRITIC_RESULT_PREFIX = "plan-critic-result"
ARCH_RESULT_PREFIX = "architecture-review-result"
log = make_logger(AGENT_NAME)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

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
            log("Non-interactive mode: defaulting to 'regen'.")
            response = "regen"
        if response == "abort":
            log("Aborted. No changes made.")
            sys.exit(0)
        return response == "regen"

    return False


# ---------------------------------------------------------------------------
# Subagent definitions
# ---------------------------------------------------------------------------

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
    violations_block = format_violations_block(violations, iteration, "violations (already addressed by the plan agent)")

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/plan-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [plan-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates plan.md against constitution.md, architecture.md, and spec.md. Returns structured JSON.",
        prompt=build_plan_critic_prompt(
            constitution, architecture, spec, plan, iteration,
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
    violations_block = format_violations_block(arch_violations, iteration, "architecture violations (already addressed by the plan agent)")

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/architecture-review-result-{iteration}.json using Bash\n"
        f"- Print one line: [architecture-review] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Reviews plan.md for architecture quality, best practices, and operational safety.",
        prompt=build_architecture_review_prompt(
            constitution, architecture, spec, plan, arch_principles, iteration,
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
    log(f"Starting plan-auto for feature: {feature}")

    force_regen = preflight(spec_dir, feature)

    constitution = read_file(Path(".specify/memory/constitution.md"))
    spec = read_file(spec_dir / "spec.md")

    arch_path = Path(".specify/memory/architecture.md")
    architecture = read_file(arch_path) if arch_path.exists() else "(architecture.md not found)"

    arch_principles_path = Path(".specify/memory/architecture-principles.md")
    arch_principles = read_file(arch_principles_path) if arch_principles_path.exists() else "(architecture-principles.md not found)"

    MAX_ITERATIONS, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "plan-critic-escalation-review.md", CRITIC_RESULT_PREFIX, 3, log
    )

    # --- Step 1: Generate plan.md if needed ---
    if not (spec_dir / "plan.md").exists() or force_regen:
        log("Running plan agent...")
        async for message in query(
            prompt=f"Generate plan.md for feature {feature}. Write it to specs/{feature}/plan.md.",
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                agents={"plan-agent": plan_agent_definition(constitution, spec, arch_principles)},
                setting_sources=["project"],
            ),
        ):
            log_sdk_message(message, prefix="  ")

        if not (spec_dir / "plan.md").exists():
            log("ERROR: plan agent did not produce plan.md. Aborting.")
            sys.exit(1)

    # --- Resume guard: done if architecture review already passed ---
    passing = find_passing_iteration(spec_dir, ARCH_RESULT_PREFIX, MAX_ITERATIONS)
    if passing is not None:
        log(f"Already PASS from architecture review iteration {passing}.")
        log("Plan is ready for human review. No further action taken.")
        run_auto_commit("after_plan", AGENT_NAME)
        write_stage_complete(spec_dir, "plan")
        return

    # --- Resume state: determine where we left off ---
    # Use result files as idempotency markers; carry forward violations from any incomplete gate.
    iteration = next_iteration(spec_dir, CRITIC_RESULT_PREFIX)
    iteration, critic_violations, arch_violations = find_two_gate_resume_state(
        spec_dir, CRITIC_RESULT_PREFIX, ARCH_RESULT_PREFIX, iteration
    )
    if _skip_fix_agent and (critic_violations or arch_violations):
        log("Escalation review present — skipping revision agent; violations were resolved externally.")
        critic_violations = None
        arch_violations = None
    elif critic_violations:
        log(f"Resuming after critic FAIL at iteration {iteration - 1} — revision will run before critic {iteration}.")
    elif arch_violations:
        log(f"Resuming after arch FAIL at iteration {iteration - 1} — revision will run before critic {iteration}.")
    elif iteration < next_iteration(spec_dir, CRITIC_RESULT_PREFIX):
        log(f"Resuming: critic {iteration} already PASS — architecture review will run for iteration {iteration}.")

    # --- Step 2: Two-gate loop (critic → architecture review) ---
    while iteration <= MAX_ITERATIONS:
        critic_path = spec_dir / f"{CRITIC_RESULT_PREFIX}-{iteration}.json"
        arch_path_iter = spec_dir / f"{ARCH_RESULT_PREFIX}-{iteration}.json"

        # --- Gate 1: Plan critic ---
        if not critic_path.exists():
            # Apply revision first if violations are pending from a previous gate failure.
            if critic_violations or arch_violations:
                pending_label = "critic" if critic_violations else "architecture review"
                pending_iter = iteration - 1
                pending_file = (
                    f"specs/{feature}/plan-critic-result-{pending_iter}.json"
                    if critic_violations
                    else f"specs/{feature}/architecture-review-result-{pending_iter}.json"
                )
                log(f"Running plan revision for {pending_label} violations from iteration {pending_iter}...")
                async for message in query(
                    prompt=(
                        f"Revise plan.md for feature {feature}. "
                        f"Read {pending_file} for the full violation list. "
                        f"Write updated plan.md to specs/{feature}/plan.md."
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                        agents={"plan-agent": plan_agent_definition(constitution, spec, arch_principles)},
                        setting_sources=["project"],
                    ),
                ):
                    log_sdk_message(message, prefix="  ")

            prev_critic_violations = critic_violations  # Pass as context to critic
            critic_violations = None
            arch_violations = None

            log(f"Running plan critic (iteration {iteration})...")
            plan = read_file(spec_dir / "plan.md")

            await run_gate(
                log, "plan", "plan_critic.py", feature, iteration, "plan critic",
                lambda: query(
                    prompt=(
                        f"Validate plan.md for feature {feature}. "
                        f"Write result to specs/{feature}/plan-critic-result-{iteration}.json."
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "plan-critic": critic_agent_definition(
                                constitution, architecture, spec, plan, iteration, prev_critic_violations
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

        # --- Gate 2: Architecture review ---
        if not arch_path_iter.exists():
            log(f"Running architecture review (iteration {iteration})...")
            plan = read_file(spec_dir / "plan.md")

            await run_gate(
                log, "architecture", "architecture_critic.py", feature, iteration, "architecture review",
                lambda: query(
                    prompt=(
                        f"Review plan.md for feature {feature} for architectural quality. "
                        f"Write result to specs/{feature}/architecture-review-result-{iteration}.json."
                    ),
                    options=ClaudeAgentOptions(
                        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                        agents={
                            "architecture-review": arch_review_agent_definition(
                                constitution, architecture, spec, plan, arch_principles, iteration, arch_violations
                            )
                        },
                        setting_sources=["project"],
                    ),
                ),
            )

            if not arch_path_iter.exists():
                log(f"ERROR: architecture review did not write result file for iteration {iteration}. Aborting.")
                sys.exit(1)
        else:
            log(f"Architecture review result for iteration {iteration} already exists — reading status.")

        arch_result = read_result(spec_dir, ARCH_RESULT_PREFIX, iteration)
        arch_status = arch_result.get("status", "FAIL")
        confidence = arch_result.get("confidence", 0)

        if arch_status == "PASS":
            log(f"Architecture review PASS (iteration {iteration}, confidence {confidence}/10).")
            log("Plan is ready for human review. No further action taken.")
            run_auto_commit("after_plan", AGENT_NAME)
            write_stage_complete(spec_dir, "plan")
            return

        blocking_arch = len(arch_result.get("blocking_issues", []))
        log(f"Architecture review FAIL (iteration {iteration}) — {blocking_arch} blocking issue(s), confidence {confidence}/10.")
        arch_violations = arch_result.get("blocking_issues", [])
        iteration += 1

    # --- Escalation ---
    write_escalation(
        spec_dir=spec_dir,
        feature=feature,
        escalation_filename="plan-critic-escalation.md",
        log_description="plan.md failed review",
        review_history_prefixes=[
            (CRITIC_RESULT_PREFIX, "Plan Critic"),
            (ARCH_RESULT_PREFIX, "Architecture Review"),
        ],
        max_iterations=MAX_ITERATIONS,
        title="Plan Review Escalation",
        summary=(
            "The automated plan review loop exhausted its iteration limit without producing\n"
            "a plan that passed both the plan critic and the architecture review. Human review\n"
            "is required to resolve the outstanding violations before planning can proceed."
        ),
        required_action=(
            f"1. Review the violations above.\n"
            f"2. Edit specs/{feature}/plan.md manually to address the BLOCKING violations.\n"
            f"3. Re-run `python .claude/agents/plan-auto.py` to restart the automated loop,\n"
            f"   or run `/speckit-plan-critic` and `/architecture-review-plan` manually to verify your fixes."
        ),
        log_fn=log,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plan auto-orchestrator")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    args = parser.parse_args()

    feature = args.feature or get_feature_from_branch(AGENT_NAME)
    asyncio.run(run(feature))
