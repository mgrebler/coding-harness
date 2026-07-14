---
name: ch-2-tasks-auto
description: Runs the automated task generation and critic loop for the current feature branch by invoking ch-2-tasks-auto.py. Handles task generation, iterative critic review, revision, and escalation. Run manually after reviewing the plan.
user-invocable: true
---

# Tasks Auto-Orchestrator

Run the automated task generation and critic loop for the current feature branch.

All orchestration logic lives in `.claude/agents/ch-2-tasks-auto.py`. This skill is a thin
invocation wrapper — do not re-implement the loop here.

---

## Execution

Run from the repo root:

```bash
python .claude/agents/ch-2-tasks-auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/ch-2-tasks-auto.py --feature 015-job-description-rich-text
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

1. Validates pre-flight conditions (plan.md exists; tasks.md resume/regen/abort prompt if applicable)
2. Generates `tasks.md` via the tasks agent (skipped if already exists and not force-regenerating)
3. Runs an iterative critic review loop (up to 3 iterations):
   - **Tasks critic**: validates plan traceability, spec coverage, TDD compliance, stack constraints, scope, and dependency validity
4. Revises `tasks.md` between iterations to address any blocking violations
5. On PASS: triggers auto-commit via the git extension
6. On 3-iteration exhaustion: writes `specs/$FEATURE/ch-2-tasks-critic-escalation.md` and exits non-zero

The script is resume-safe: re-running after an interruption continues from the last
incomplete step using result files as idempotency markers.

---

## What this skill does not do

- Does not implement task generation or critic logic — that lives in ch-2-tasks-auto.py and its subagents
- Does not proceed to the test phase — run `/ch-3-test-auto` after reviewing `tasks.md`
- Does not push to remote
