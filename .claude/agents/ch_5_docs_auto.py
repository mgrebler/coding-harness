#!/usr/bin/env python3
"""
.claude/agents/ch_5_docs_auto.py

Agentic orchestrator for automated user-documentation generation and critic loop.
Run manually via /ch-5-docs-auto after reviewing the implementation.
Runs independently of any Claude Code interactive session.

Usage:
  python .claude/agents/ch_5_docs_auto.py
  python .claude/agents/ch_5_docs_auto.py --feature 014-rich-text-formatting

Requirements:
  pip install claude-agent-sdk

The script derives the feature from the current git branch if --feature
is not supplied, matching the behaviour of the speckit skills.

What this stage does:
  docs/ at the project root is a persistent, cumulative documentation set —
  the "current state of the world" for both real users and maintainers, not a
  per-feature artifact. Each run merges this feature's shipped behaviour into
  the existing tree (or bootstraps it from scratch if docs/ doesn't exist
  yet), organised using the Diátaxis categories described in
  .specify/memory/documentation-principles.md.

Resume behaviour:
  Re-running after an interruption continues from the last incomplete step:
  - If docs/ has already been touched this run but no critic results exist, runs the critic (no re-merge)
  - If the last critic result was FAIL, re-runs the revision agent before the next critic
  - If a critic result was already PASS, exits immediately (no work to do)

  The pre-merge snapshot of docs/ (specs/$FEATURE/ch-5-docs-baseline/) is only
  taken once per feature — if it already exists, it is NOT overwritten, since a
  re-run must diff against the state *before this feature's merge began*, not
  against a partially-merged intermediate state.
"""

import shutil
import sys
from pathlib import Path

from ch_5_docs_critic import build_docs_critic_prompt
from claude_agent_sdk import AgentDefinition, query

from agent_common import critic_reconcile, local_agent_loop
from agent_common.console import make_logger, setup_log_file
from agent_common.critic_loop import (
    GateSpec,
    finish_if_already_passing,
    finish_stage,
    run_cli,
    run_single_gate_loop,
)
from agent_common.driving_agent import NO_RECURSION_NOTICE, driving_agent_options
from agent_common.files import read_file, require_spec_files
from agent_common.resume_state import (
    extend_iterations_if_reviewed,
    format_violations_block,
    load_prior_violations,
    next_iteration,
    stage_is_complete,
)

AGENT_NAME = "ch-5-docs-auto"
RESULT_PREFIX = "ch-5-docs-critic-result"
DOCS_DIR = Path("docs")
log = make_logger(AGENT_NAME)


# Pre-flight checks


def preflight(spec_dir: Path, feature: str) -> None:
    require_spec_files(log, spec_dir, "spec.md", "plan.md", "tasks.md")
    if not stage_is_complete(spec_dir, "ch-4-implement"):
        log(
            "ERROR: ch-4-implement has not completed for this feature "
            f"(no {spec_dir}/ch-4-implement-auto-complete marker). Run ch-4-implement-auto first."
        )
        sys.exit(1)


def snapshot_baseline(spec_dir: Path) -> None:
    """Copy the current docs/ tree to specs/$FEATURE/ch-5-docs-baseline/, once per
    feature. Never overwrites an existing baseline — a re-run after interruption
    must keep diffing against the state before THIS feature's merge began."""
    baseline_dir = spec_dir / "ch-5-docs-baseline"
    if baseline_dir.exists():
        return
    if DOCS_DIR.exists():
        shutil.copytree(DOCS_DIR, baseline_dir)
    else:
        baseline_dir.mkdir(parents=True, exist_ok=True)


# Subagent definitions


def docs_agent_definition(
    constitution: str, documentation_principles: str, spec: str, plan: str, tasks: str
) -> AgentDefinition:
    return AgentDefinition(
        description="Merges this feature's shipped behaviour into docs/, the project's persistent user/maintainer documentation set.",
        prompt=f"""You are the Documentation Agent for a spec-kit project.

Your sole function is to merge this feature's shipped behaviour into docs/, the project's
persistent, cumulative documentation set. docs/ describes the CURRENT STATE of the product —
it is never a changelog and never narrates what changed or when.

Inputs already loaded for you:

--- CONSTITUTION ---
{constitution}

--- DOCUMENTATION PRINCIPLES ---
{documentation_principles}

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

Process:
1. Run `git fetch origin main --quiet` (ignore failure), then `git diff origin/main...HEAD --name-only`
   if origin/main resolves, else `git diff main...HEAD --name-only`, and read each changed source
   file to confirm exactly what this feature shipped.
2. If docs/ does not exist yet, create it from scratch: index.md plus the tutorials/, how-to/,
   reference/, explanation/, and maintainer/ folders, populated from this feature's shipped
   behaviour and anything else already implemented in the codebase. Do not document aspirational
   spec content that wasn't actually shipped.
3. If docs/ already exists, read every file in it, then merge this feature's shipped behaviour in:
   - New/changed capability -> add or revise the matching reference/<capability-slug>.md
   - New end-to-end user-facing workflow -> add how-to/<task-slug>.md
   - Genuinely new cross-cutting concept -> add/revise an explanation/ file
   - New onboarding-relevant capability that changes first use -> revise tutorials/
   - Operationally-relevant, non-user-facing detail -> maintainer/
   - Removed/replaced functionality -> delete or rewrite the affected file(s); do not leave stubs
   - Update index.md's links whenever a new top-level file is added
   Leave every file this feature didn't touch exactly as it was.
4. Follow the full placement and structure rules in DOCUMENTATION PRINCIPLES above exactly —
   the docs critic will validate against them.
5. Do not stop until every affected file under docs/ has been written to disk.

You do not touch code, specs, plans, or tasks. Your only output is docs/.
""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )


def critic_agent_definition(
    constitution: str,
    documentation_principles: str,
    spec: str,
    plan: str,
    tasks: str,
    iteration: int,
    violations: list | None = None,
) -> AgentDefinition:
    violations_block = format_violations_block(
        violations, iteration, "violations (already addressed by the docs agent)"
    )

    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/ch-5-docs-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-5-docs-critic] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Validates this run's merge into docs/ against documentation-principles.md and the shipped diff. Returns structured JSON.",
        prompt=build_docs_critic_prompt(
            constitution,
            documentation_principles,
            spec,
            plan,
            tasks,
            iteration,
            violations_block=violations_block,
            output_instructions=output_instructions,
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


def docs_reconcile_agent_definition(
    constitution: str,
    documentation_principles: str,
    spec: str,
    plan: str,
    tasks: str,
    iteration: int,
    raw_results: list[dict],
) -> AgentDefinition:
    context_block = (
        f"--- CONSTITUTION ---\n{constitution}\n\n"
        f"--- DOCUMENTATION PRINCIPLES ---\n{documentation_principles}\n\n"
        f"--- SPEC ---\n{spec}\n\n"
        f"--- PLAN ---\n{plan}\n\n"
        f"--- TASKS ---\n{tasks}"
    )
    output_instructions = (
        f"- After producing JSON, write it to specs/$FEATURE/ch-5-docs-critic-result-{iteration}.json using Bash\n"
        f"- Print one line: [ch-5-docs-critic-reconcile] iteration {iteration} → PASS or FAIL → path"
    )
    return AgentDefinition(
        description="Reconciles findings from multiple independent docs critics into one verified result.",
        prompt=critic_reconcile.build_reconcile_prompt(
            context_block, raw_results, iteration, "violations", output_instructions
        ),
        tools=["Read", "Write", "Bash", "Glob", "Grep"],
    )


# Main orchestration loop


async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting ch-5-docs-auto for feature: {feature}")

    preflight(spec_dir, feature)

    constitution = read_file(Path(".specify/memory/constitution.md"))
    documentation_principles = read_file(Path(".specify/memory/documentation-principles.md"))
    spec = read_file(spec_dir / "spec.md")
    plan = read_file(spec_dir / "plan.md")
    tasks = read_file(spec_dir / "tasks.md")

    max_iterations, _skip_fix_agent = extend_iterations_if_reviewed(
        spec_dir, "ch-5-docs-critic-escalation-review.md", RESULT_PREFIX, 3, log
    )

    # --- Step 1: Snapshot the pre-merge docs/ tree, then merge this feature in ---
    snapshot_baseline(spec_dir)

    if not list(spec_dir.glob(f"{RESULT_PREFIX}-*.json")):
        log("Running docs agent...")
        docs_agent = docs_agent_definition(constitution, documentation_principles, spec, plan, tasks)
        user_prompt = f"Merge feature {feature}'s shipped behaviour into docs/."
        await local_agent_loop.run_generation(
            log,
            "docs",
            claude_fallback=lambda: query(
                prompt=user_prompt + NO_RECURSION_NOTICE,
                options=driving_agent_options(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={"docs-agent": docs_agent},
                ),
            ),
            system_prompt=docs_agent.prompt,
            user_prompt=user_prompt,
        )

        if not DOCS_DIR.exists():
            log("ERROR: docs agent did not produce docs/. Aborting.")
            sys.exit(1)

    # --- Resume guard: exit if a previous run already achieved PASS ---
    if finish_if_already_passing(
        log,
        spec_dir,
        AGENT_NAME,
        RESULT_PREFIX,
        max_iterations,
        "docs critic",
        "Documentation is up to date. No further action taken.",
        "after_docs",
        "ch-5-docs",
    ):
        return

    # --- Resume state: load violations from last FAIL so revision runs before next critic ---
    iteration = next_iteration(spec_dir, RESULT_PREFIX)
    violations = load_prior_violations(spec_dir, RESULT_PREFIX, iteration)

    async def run_fix(pending_iter: int, pending_violations: list) -> None:
        log(
            f"Running revision agent to address {len(pending_violations)} violation(s) from iteration {pending_iter}..."
        )
        docs_agent = docs_agent_definition(constitution, documentation_principles, spec, plan, tasks)
        user_prompt = (
            f"Revise docs/ for feature {feature} to fix critic violations. "
            f"Read specs/{feature}/ch-5-docs-critic-result-{pending_iter}.json for the violation list. "
            f"Fix ONLY the files needed to resolve those violations."
        )
        await local_agent_loop.run_generation(
            log,
            "docs",
            claude_fallback=lambda: query(
                prompt=user_prompt + NO_RECURSION_NOTICE,
                options=driving_agent_options(
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
                    agents={"docs-agent": docs_agent},
                ),
            ),
            system_prompt=docs_agent.prompt,
            user_prompt=user_prompt,
        )

    async def on_pass(result: dict) -> None:
        finish_stage(
            log,
            spec_dir,
            AGENT_NAME,
            "after_docs",
            "ch-5-docs",
            "Documentation is up to date. No further action taken.",
        )

    def build_critic_query(iteration: int, prev_violations):
        return query(
            prompt=(
                f"Validate this run's merge into docs/ for feature {feature}. "
                f"Write result to specs/{feature}/ch-5-docs-critic-result-{iteration}.json."
            )
            + NO_RECURSION_NOTICE,
            options=driving_agent_options(
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "docs-critic": critic_agent_definition(
                        constitution,
                        documentation_principles,
                        spec,
                        plan,
                        tasks,
                        iteration,
                        prev_violations,
                    )
                },
            ),
        )

    def build_reconcile_query(iteration: int, raw_results: list[dict]):
        return query(
            prompt=(
                f"Reconcile the raw docs-critic findings for feature {feature} into one "
                f"verified result. Write result to specs/{feature}/ch-5-docs-critic-result-{iteration}.json."
            )
            + NO_RECURSION_NOTICE,
            options=driving_agent_options(
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                agents={
                    "docs-critic-reconcile": docs_reconcile_agent_definition(
                        constitution, documentation_principles, spec, plan, tasks, iteration, raw_results
                    )
                },
            ),
        )

    # --- Step 2: Critic loop ---
    await run_single_gate_loop(
        log,
        spec_dir,
        feature,
        max_iterations,
        gate=GateSpec(
            RESULT_PREFIX,
            "ch_5_docs_critic.py",
            "docs",
            "docs critic",
            build_critic_query,
            build_reconcile_query,
        ),
        resume_state=(iteration, violations),
        skip_fix_agent=_skip_fix_agent,
        run_fix=run_fix,
        on_pass=on_pass,
        escalation_kwargs={
            "escalation_filename": "ch-5-docs-critic-escalation.md",
            "log_description": "docs/ merge failed critic review",
            "review_history_prefixes": [(RESULT_PREFIX, "Docs Critic")],
            "title": "Docs Critic Escalation",
            "summary": (
                "The automated docs-critic loop exhausted its iteration limit without producing\n"
                "a passing merge into docs/. Human review is required to resolve the outstanding\n"
                "violations before the feature can be considered fully documented."
            ),
            "required_action": (
                f"1. Review the violations above.\n"
                f"2. Edit the affected files under docs/ manually to address the BLOCKING violations.\n"
                f"3. Re-run `python .claude/agents/ch_5_docs_auto.py` to restart the automated loop,\n"
                f"   or run `/ch-5-docs-critic` manually to verify your fixes."
            ),
        },
    )


# Entry point

if __name__ == "__main__":
    run_cli(AGENT_NAME, "Docs auto-orchestrator", run)
