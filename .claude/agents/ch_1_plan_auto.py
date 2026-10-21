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

import json
import sys
from pathlib import Path

from ch_1_plan_architecture_critic import build_architecture_review_prompt
from ch_1_plan_critic import build_plan_critic_prompt
from claude_agent_sdk import AgentDefinition

from agent_common import local_agent_loop
from agent_common.console import make_logger, setup_log_file
from agent_common.critic_loop import (
    claude_gate,
    confirm_regenerate,
    finish_if_already_passing,
    finish_stage,
    run_cli,
    run_two_gate_loop,
)
from agent_common.driving_agent import (
    HUMAN_REVIEW_ATTESTATION_GUARDRAIL,
)
from agent_common.files import (
    CONSTITUTION_PATH,
    format_directory_tree,
    read_directory_tree,
    read_file,
    read_memory,
    require_spec_files,
)
from agent_common.resume_state import (
    apply_escalation_review,
    find_two_gate_resume_state,
    next_iteration,
)

AGENT_NAME = "ch-1-plan-auto"
CRITIC_RESULT_PREFIX = "ch-1-plan-critic-result"
ARCH_RESULT_PREFIX = "ch-1-plan-architecture-review-result"
log = make_logger(AGENT_NAME)


def preflight(spec_dir: Path, feature: str) -> bool:
    """
    Returns True if plan.md should be regenerated from scratch.
    Exits on unrecoverable conditions.
    """
    require_spec_files(log, spec_dir, "spec.md")
    return confirm_regenerate(log, AGENT_NAME, spec_dir, "plan.md", CRITIC_RESULT_PREFIX)


def plan_agent_definition(
    constitution: str, spec: str, arch_principles: str, model: str | None = None
) -> AgentDefinition:
    return AgentDefinition(
        description="Generates plan.md for the current feature from spec.md and constitution.md.",
        model=model,
        prompt=f"""You are the Plan Agent for a spec-kit project.

Your sole function is to produce a complete and valid plan.md for the current feature.

You must follow the structure defined in the plan template at .specify/templates/plan-template.md.
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
- Fill in the Constitution Check table completely: every constitution section gets an
  explicit ✅/⚠️/N/A verdict and a one-line justification — no section may be left blank
  or silently omitted. Fill in the Stack Constraint Check table: list every dependency,
  framework, tool, or external service named anywhere in the plan and match each one
  against the constitution §2 stack table by exact name; anything not already listed
  there requires a proposed amendment in that section before the plan can pass.
- List every constitution or architecture.md amendment the plan proposes under
  "## Pending Human Approvals" (or `None.`). Never edit .specify/memory/ yourself — the
  pipeline stops after this stage until a human applies them.
- Whenever you introduce or change an additive field, endpoint, or message handler —
  whether this is the initial draft or a revision responding to critic violations —
  re-scan contracts/ and update it to reflect that change. This applies on every pass, not
  just the first draft: a revision that adds a field without updating contracts/ is
  incomplete.
- The plan will be evaluated by an Architecture Review agent using the principles above — design accordingly
- Do not stop until plan.md is written to disk
{HUMAN_REVIEW_ATTESTATION_GUARDRAIL}
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting ch-1-plan-auto for feature: {feature}")

    force_regen = preflight(spec_dir, feature)

    constitution = read_file(CONSTITUTION_PATH)
    spec = read_file(spec_dir / "spec.md")

    architecture = read_memory("architecture")

    docs = format_directory_tree(read_directory_tree(Path("docs")), "(docs/ not yet created)")

    arch_principles = read_memory("architecture-principles")

    budget = apply_escalation_review(
        spec_dir, "ch-1-plan-critic-escalation-review.md", CRITIC_RESULT_PREFIX, 3, log
    )

    if not (spec_dir / "plan.md").exists() or force_regen:
        log("Running plan agent...")
        user_prompt = (
            f"Generate plan.md for feature {feature}. Write it to specs/{feature}/plan.md."
        )
        await local_agent_loop.run_agent(
            log,
            "plan",
            "plan-agent",
            lambda model: plan_agent_definition(constitution, spec, arch_principles, model=model),
            user_prompt,
        )

        if not (spec_dir / "plan.md").exists():
            log("ERROR: plan agent did not produce plan.md. Aborting.")
            sys.exit(1)

    if finish_if_already_passing(
        log,
        spec_dir,
        AGENT_NAME,
        ARCH_RESULT_PREFIX,
        "architecture review",
        "Plan is ready for human review. No further action taken.",
        "after_plan",
        "ch-1-plan",
    ):
        return

    iteration = next_iteration(spec_dir, CRITIC_RESULT_PREFIX)
    resume_state = find_two_gate_resume_state(
        spec_dir, CRITIC_RESULT_PREFIX, ARCH_RESULT_PREFIX, iteration
    )

    async def run_revision(pending_iter: int, pending_violations: list, pending_label: str) -> None:
        log(
            f"Running plan revision for {pending_label} violations from iteration {pending_iter}..."
        )
        user_prompt = (
            f"Revise plan.md for feature {feature}. "
            f"Violations to address: {json.dumps(pending_violations)} "
            f"Edit specs/{feature}/plan.md in place: keep every existing section and table, "
            f"and update research.md, data-model.md, quickstart.md, and contracts/ wherever "
            f"your change affects them."
        )
        await local_agent_loop.run_agent(
            log,
            "plan",
            "plan-agent",
            lambda model: plan_agent_definition(constitution, spec, arch_principles, model=model),
            user_prompt,
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

    await run_two_gate_loop(
        log,
        spec_dir,
        feature,
        budget,
        revised_artifact=spec_dir / "plan.md",
        gate1=claude_gate(
            feature=feature,
            result_prefix=CRITIC_RESULT_PREFIX,
            script_name="ch_1_plan_critic.py",
            critic_type="plan",
            label="plan critic",
            summary_style="violations",
            agent_name="plan-critic",
            description="Validates plan.md against constitution.md, architecture.md, docs/, and "
            "spec.md. Returns structured JSON.",
            task=f"Validate plan.md for feature {feature}.",
            violations_context="violations (already addressed by the plan agent)",
            prompt=lambda iteration, violations_block, output_instructions: (
                build_plan_critic_prompt(
                    constitution,
                    architecture,
                    docs,
                    spec,
                    read_file(spec_dir / "plan.md"),
                    iteration,
                    violations_block=violations_block,
                    output_instructions=output_instructions,
                )
            ),
            context=lambda: [
                ("CONSTITUTION", constitution),
                ("ARCHITECTURE", architecture),
                ("DOCS (current shipped behaviour, per documentation-principles.md)", docs),
                ("SPEC", spec),
                ("PLAN (current)", read_file(spec_dir / "plan.md")),
            ],
        ),
        gate2=claude_gate(
            feature=feature,
            result_prefix=ARCH_RESULT_PREFIX,
            script_name="ch_1_plan_architecture_critic.py",
            critic_type="plan-architecture-review",
            label="architecture review",
            summary_style="confidence",
            agent_name="architecture-review",
            description="Reviews plan.md for architecture quality, best practices, and "
            "operational safety.",
            task=f"Review plan.md for feature {feature} for architectural quality.",
            violations_context="architecture violations (already addressed by the plan agent)",
            prompt=lambda iteration, violations_block, output_instructions: (
                build_architecture_review_prompt(
                    constitution,
                    architecture,
                    spec,
                    read_file(spec_dir / "plan.md"),
                    arch_principles,
                    iteration,
                    violations_block=violations_block,
                    output_instructions=output_instructions,
                )
            ),
            context=lambda: [
                ("CONSTITUTION", constitution),
                ("ARCHITECTURE", architecture),
                ("SPEC", spec),
                ("PLAN (current)", read_file(spec_dir / "plan.md")),
                ("ARCHITECTURE PRINCIPLES", arch_principles),
            ],
        ),
        resume_state=resume_state,
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


if __name__ == "__main__":
    run_cli(AGENT_NAME, "Plan auto-orchestrator", run)
