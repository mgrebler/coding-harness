#!/usr/bin/env python3
"""
.claude/agents/ch_plan_to_implement_auto.py

Full-pipeline orchestrator: chains ch-1-plan-auto → ch-2-tasks-auto → ch-3-test-auto →
ch-4-implement-auto → ch-5-docs-auto for a feature branch without stopping for review
between stages.

Usage:
  python .claude/agents/ch_plan_to_implement_auto.py
  python .claude/agents/ch_plan_to_implement_auto.py --feature 016-my-feature

Requirements:
  pip install claude-agent-sdk   (needed by the sub-scripts, not this wrapper)

The script derives the feature from the current git branch if --feature is
not supplied.

Resume behaviour:
  Stage completion is tracked via the natural result files each sub-script
  produces:

  Plan stage done:      ch-1-plan-architecture-review-result-*.json with status PASS
  Tasks stage done:     ch-2-tasks-critic-result-*.json with status PASS
  Test stage done:      ch-3-test-quality-review-result-*.json with status PASS
  Implement stage done: ch-4-implement-code-quality-review-result-*.json with status PASS
  Docs stage done:      ch-5-docs-critic-result-*.json with status PASS

  Each sub-script also has its own internal resume guards for mid-stage
  interruptions (e.g. a crash during critic iteration 2).

Relationship to manual (human-in-the-loop) workflow:
  Both workflows gate purely on these artifacts — there are no approval
  marker files or git hooks involved in either. The only difference is that
  the manual workflow runs one stage at a time so a human can review the
  artifact between stages, while this orchestrator runs all five in sequence
  unattended.

Pre-flight:
  - Must be on a feature branch (not main)
  - specs/<feature>/spec.md must exist
"""

import argparse
import subprocess
import sys
from pathlib import Path

from agent_common.console import (
    USAGE_LIMIT_EXIT_CODE,
    make_logger,
    setup_log_file,
    stream_subprocess,
)
from agent_common.git import get_feature_from_branch
from agent_common.resume_state import (
    find_passing_iteration,
    max_existing_iteration,
    stage_is_complete,
)

AGENT_NAME = "ch-plan-to-implement-auto"
log = make_logger(AGENT_NAME)

TOTAL_STAGES = 5

PLAN_ARCH_PREFIX = "ch-1-plan-architecture-review-result"
TASKS_CRITIC_PREFIX = "ch-2-tasks-critic-result"
TEST_QUALITY_PREFIX = "ch-3-test-quality-review-result"
IMPL_QUALITY_PREFIX = "ch-4-implement-code-quality-review-result"
DOCS_CRITIC_PREFIX = "ch-5-docs-critic-result"


# Helpers


def get_current_branch() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _check_stage_result(rc: int, stage_num: int, stage_name: str, escalation_hint: str) -> None:
    """
    Log the outcome of a stage subprocess and exit if it didn't pass. A usage-limit
    pause (USAGE_LIMIT_EXIT_CODE) is distinguished from a real failure — no
    escalation file exists to review in that case, so re-running later is the
    correct next step rather than reviewing an escalation doc.
    """
    if rc == USAGE_LIMIT_EXIT_CODE:
        log(
            f"Stage {stage_num}/{TOTAL_STAGES} ({stage_name}): PAUSED — hit a Claude usage/session "
            f"limit. Re-run this command once the limit resets; progress so far is preserved."
        )
        sys.exit(rc)
    if rc != 0:
        log(
            f"Stage {stage_num}/{TOTAL_STAGES} ({stage_name}): FAILED. Review {escalation_hint} and re-run."
        )
        sys.exit(1)
    log(f"Stage {stage_num}/{TOTAL_STAGES} ({stage_name}): PASSED.")


# Main


def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")

    branch = get_current_branch()
    if branch == "main":
        log("ERROR: Must be on a feature branch. Currently on main.")
        sys.exit(1)

    if not (spec_dir / "spec.md").exists():
        log(f"ERROR: {spec_dir}/spec.md not found. Run /speckit-specify first.")
        sys.exit(1)

    log(f"Pipeline start — feature: {feature}, branch: {branch}")
    log(
        "Stages: ch-1-plan-auto → ch-2-tasks-auto → ch-3-test-auto → "
        "ch-4-implement-auto → ch-5-docs-auto"
    )

    # --- Stage 1: Plan ---
    if (
        stage_is_complete(spec_dir, "ch-1-plan")
        or find_passing_iteration(
            spec_dir, PLAN_ARCH_PREFIX, max_existing_iteration(spec_dir, PLAN_ARCH_PREFIX)
        )
        is not None
    ):
        log(f"Stage 1/{TOTAL_STAGES} (plan): already complete — skipping.")
    else:
        log(f"Stage 1/{TOTAL_STAGES} (plan): running ch-1-plan-auto...")
        rc = stream_subprocess(["python", ".claude/agents/ch_1_plan_auto.py", "--feature", feature])
        _check_stage_result(rc, 1, "plan", "ch-1-plan-critic-escalation.md")

    # --- Stage 2: Tasks ---
    if (
        stage_is_complete(spec_dir, "ch-2-tasks")
        or find_passing_iteration(
            spec_dir, TASKS_CRITIC_PREFIX, max_existing_iteration(spec_dir, TASKS_CRITIC_PREFIX)
        )
        is not None
    ):
        log(f"Stage 2/{TOTAL_STAGES} (tasks): already complete — skipping.")
    else:
        log(f"Stage 2/{TOTAL_STAGES} (tasks): running ch-2-tasks-auto...")
        rc = stream_subprocess(
            ["python", ".claude/agents/ch_2_tasks_auto.py", "--feature", feature]
        )
        _check_stage_result(rc, 2, "tasks", "ch-2-tasks-critic-escalation.md")

    # --- Stage 3: Test ---
    if (
        stage_is_complete(spec_dir, "ch-3-test")
        or find_passing_iteration(
            spec_dir, TEST_QUALITY_PREFIX, max_existing_iteration(spec_dir, TEST_QUALITY_PREFIX)
        )
        is not None
    ):
        log(f"Stage 3/{TOTAL_STAGES} (test): already complete — skipping.")
    else:
        log(f"Stage 3/{TOTAL_STAGES} (test): running ch-3-test-auto...")
        rc = stream_subprocess(["python", ".claude/agents/ch_3_test_auto.py", "--feature", feature])
        _check_stage_result(rc, 3, "test", "ch-3-test-critic-escalation.md")

    # --- Stage 4: Implement ---
    # Unlike stages 1-3 (and 5), ch_4_implement_auto's on-both-pass path runs CI
    # checks (and a possible CI-fix-agent + commit-hygiene check) *between* the
    # quality review passing and finish_stage() actually being called — so a
    # passing quality-review iteration does NOT by itself prove the stage
    # finished (a crash or failure in that CI step leaves no completion marker
    # and no commit). Require the actual completion marker here, not the
    # gate-passing shortcut the other stages use safely.
    if stage_is_complete(spec_dir, "ch-4-implement"):
        log(f"Stage 4/{TOTAL_STAGES} (implement): already complete — skipping.")
    else:
        log(f"Stage 4/{TOTAL_STAGES} (implement): running ch-4-implement-auto...")
        rc = stream_subprocess(
            ["python", ".claude/agents/ch_4_implement_auto.py", "--feature", feature]
        )
        _check_stage_result(rc, 4, "implement", "ch-4-implement-critic-escalation.md")

    # --- Stage 5: Docs ---
    if (
        stage_is_complete(spec_dir, "ch-5-docs")
        or find_passing_iteration(
            spec_dir, DOCS_CRITIC_PREFIX, max_existing_iteration(spec_dir, DOCS_CRITIC_PREFIX)
        )
        is not None
    ):
        log(f"Stage 5/{TOTAL_STAGES} (docs): already complete — skipping.")
    else:
        log(f"Stage 5/{TOTAL_STAGES} (docs): running ch-5-docs-auto...")
        rc = stream_subprocess(["python", ".claude/agents/ch_5_docs_auto.py", "--feature", feature])
        _check_stage_result(rc, 5, "docs", "ch-5-docs-critic-escalation.md")

    log("Pipeline complete. All stages passed.")


# Entry point

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full plan-to-implement pipeline orchestrator")
    parser.add_argument(
        "--feature", help="Feature folder name (derived from git branch if omitted)"
    )
    args = parser.parse_args()

    feature = args.feature or get_feature_from_branch(AGENT_NAME)
    run(feature)
