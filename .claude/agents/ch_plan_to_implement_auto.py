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
from typing import NamedTuple

from agent_common.console import (
    HUMAN_ACTION_EXIT_CODE,
    USAGE_LIMIT_EXIT_CODE,
    make_logger,
    setup_log_file,
    stream_subprocess,
)
from agent_common.git import get_feature_from_branch
from agent_common.ollama import check_providers, is_stage_enabled
from agent_common.resume_state import (
    find_passing_iteration,
    stage_is_complete,
)

AGENT_NAME = "ch-plan-to-implement-auto"
log = make_logger(AGENT_NAME)


class Stage(NamedTuple):
    number: int
    name: str
    passing_prefix: str | None
    toggleable: bool = False

    @property
    def marker(self) -> str:
        return f"ch-{self.number}-{self.name}"

    @property
    def script(self) -> str:
        return f".claude/agents/ch_{self.number}_{self.name}_auto.py"

    @property
    def escalation(self) -> str:
        return f"ch-{self.number}-{self.name}-critic-escalation.md"


# implement has no passing_prefix: its on-both-pass path runs CI checks (and a
# possible CI-fix agent + commit-hygiene check) *after* the quality review
# passes and before finish_stage() — so a passing quality-review result does
# not by itself prove the stage finished. Only its completion marker does.
STAGES = (
    Stage(1, "plan", "ch-1-plan-architecture-review-result"),
    Stage(2, "tasks", "ch-2-tasks-critic-result"),
    Stage(3, "test", "ch-3-test-quality-review-result", toggleable=True),
    Stage(4, "implement", None),
    Stage(5, "docs", "ch-5-docs-critic-result", toggleable=True),
)
TOTAL_STAGES = len(STAGES)


# Helpers


def _run_stage(spec_dir: Path, feature: str, stage: Stage) -> None:
    label = f"Stage {stage.number}/{TOTAL_STAGES} ({stage.name})"
    skip_reason = _stage_skip_reason(stage.number, stage.name) if stage.toggleable else None
    if skip_reason:
        log(skip_reason)
    elif stage_is_complete(spec_dir, stage.marker) or (
        stage.passing_prefix and find_passing_iteration(spec_dir, stage.passing_prefix) is not None
    ):
        log(f"{label}: already complete — skipping.")
    else:
        log(f"{label}: running {stage.marker}-auto...")
        rc = stream_subprocess(["python", stage.script, "--feature", feature])
        _check_stage_result(rc, stage.number, stage.name, stage.escalation)


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
    if rc == HUMAN_ACTION_EXIT_CODE:
        log(
            f"Stage {stage_num}/{TOTAL_STAGES} ({stage_name}): WAITING for human action — "
            f"see the stage log above. Re-run once done."
        )
        sys.exit(rc)
    if rc != 0:
        log(
            f"Stage {stage_num}/{TOTAL_STAGES} ({stage_name}): FAILED. Review {escalation_hint} and re-run."
        )
        sys.exit(1)
    log(f"Stage {stage_num}/{TOTAL_STAGES} ({stage_name}): PASSED.")


def _stage_skip_reason(stage_num: int, stage_key: str) -> str | None:
    """
    Return the log message for skipping stage_num because it's disabled via
    .specify/pipeline.json's "stages" toggle, or None if it's enabled (the
    default — a missing config file or an unconfigured stage key is always
    enabled). Pulled out as a small pure function, separate from the actual
    subprocess-spawning stage blocks in run(), purely so it's unit-testable
    without mocking stream_subprocess.
    """
    if is_stage_enabled(stage_key):
        return None
    return (
        f"Stage {stage_num}/{TOTAL_STAGES} ({stage_key}): disabled via "
        f".specify/pipeline.json — skipping."
    )


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

    check_providers(log)
    log(f"Pipeline start — feature: {feature}, branch: {branch}")
    log("Stages: " + " → ".join(f"{stage.marker}-auto" for stage in STAGES))

    for stage in STAGES:
        _run_stage(spec_dir, feature, stage)

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
