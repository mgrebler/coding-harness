#!/usr/bin/env python3
"""
.claude/agents/plan-to-implement-auto.py

Full-pipeline orchestrator: chains plan-auto → tasks-auto → test-auto → implement-auto
for a feature branch without stopping for review between stages.

Usage:
  python .claude/agents/plan-to-implement-auto.py
  python .claude/agents/plan-to-implement-auto.py --feature 016-my-feature

Requirements:
  pip install claude-agent-sdk   (needed by the sub-scripts, not this wrapper)

The script derives the feature from the current git branch if --feature is
not supplied.

Resume behaviour:
  Stage completion is tracked via the natural result files each sub-script
  produces:

  Plan stage done:      architecture-review-result-*.json with status PASS
  Tasks stage done:     tasks-critic-result-*.json with status PASS
  Test stage done:      test-critic-result-*.json with status PASS
  Implement stage done: code-quality-review-result-*.json with status PASS

  Each sub-script also has its own internal resume guards for mid-stage
  interruptions (e.g. a crash during critic iteration 2).

Relationship to manual (human-in-the-loop) workflow:
  Both workflows gate purely on these artifacts — there are no approval
  marker files or git hooks involved in either. The only difference is that
  the manual workflow runs one stage at a time so a human can review the
  artifact between stages, while this orchestrator runs all four in sequence
  unattended.

Pre-flight:
  - Must be on a feature branch (not main)
  - specs/<feature>/spec.md must exist
"""

import argparse
import subprocess
import sys
from pathlib import Path

from agent_common import (
    get_feature_from_branch,
    make_logger,
    setup_log_file,
    find_passing_iteration,
    stage_is_complete,
)

AGENT_NAME = "plan-to-implement-auto"
log = make_logger(AGENT_NAME)

PLAN_ARCH_PREFIX = "architecture-review-result"
TASKS_CRITIC_PREFIX = "tasks-critic-result"
TEST_CRITIC_PREFIX = "test-critic-result"
IMPL_QUALITY_PREFIX = "code-quality-review-result"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def stream_subprocess(cmd: list[str]) -> int:
    """
    Run *cmd* as a subprocess, streaming stdout+stderr line-by-line through
    the current sys.stdout (which may be a _Tee writing to both terminal and
    log file). Returns the process exit code.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
    proc.wait()
    return proc.returncode


def get_current_branch() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    log("Stages: plan-auto → tasks-auto → test-auto → implement-auto")

    # --- Stage 1: Plan ---
    if stage_is_complete(spec_dir, "plan") or find_passing_iteration(spec_dir, PLAN_ARCH_PREFIX) is not None:
        log("Stage 1/4 (plan): already complete — skipping.")
    else:
        log("Stage 1/4 (plan): running plan-auto...")
        rc = stream_subprocess(
            ["python", ".claude/agents/plan-auto.py", "--feature", feature]
        )
        if rc != 0:
            log("Stage 1/4 (plan): FAILED. Review plan-critic-escalation.md and re-run.")
            sys.exit(1)
        log("Stage 1/4 (plan): PASSED.")

    # --- Stage 2: Tasks ---
    if stage_is_complete(spec_dir, "tasks") or find_passing_iteration(spec_dir, TASKS_CRITIC_PREFIX) is not None:
        log("Stage 2/4 (tasks): already complete — skipping.")
    else:
        log("Stage 2/4 (tasks): running tasks-auto...")
        rc = stream_subprocess(
            ["python", ".claude/agents/tasks-auto.py", "--feature", feature]
        )
        if rc != 0:
            log("Stage 2/4 (tasks): FAILED. Review tasks-critic-escalation.md and re-run.")
            sys.exit(1)
        log("Stage 2/4 (tasks): PASSED.")

    # --- Stage 3: Test ---
    if stage_is_complete(spec_dir, "test") or find_passing_iteration(spec_dir, TEST_CRITIC_PREFIX) is not None:
        log("Stage 3/4 (test): already complete — skipping.")
    else:
        log("Stage 3/4 (test): running test-auto...")
        rc = stream_subprocess(
            ["python", ".claude/agents/test-auto.py", "--feature", feature]
        )
        if rc != 0:
            log("Stage 3/4 (test): FAILED. Review test-critic-escalation.md and re-run.")
            sys.exit(1)
        log("Stage 3/4 (test): PASSED.")

    # --- Stage 4: Implement ---
    if stage_is_complete(spec_dir, "implement") or find_passing_iteration(spec_dir, IMPL_QUALITY_PREFIX) is not None:
        log("Stage 4/4 (implement): already complete — skipping.")
    else:
        log("Stage 4/4 (implement): running implement-auto...")
        rc = stream_subprocess(
            ["python", ".claude/agents/implement-auto.py", "--feature", feature]
        )
        if rc != 0:
            log("Stage 4/4 (implement): FAILED. Review implement-critic-escalation.md and re-run.")
            sys.exit(1)
        log("Stage 4/4 (implement): PASSED.")

    log("Pipeline complete. All stages passed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full plan-to-implement pipeline orchestrator")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    args = parser.parse_args()

    feature = args.feature or get_feature_from_branch(AGENT_NAME)
    run(feature)
