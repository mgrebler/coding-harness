---
name: ch-1-plan-auto
description: Runs the automated plan generation and critic loop for the current feature branch by invoking ch-1-plan-auto.py. Handles plan generation, iterative critic review, architecture review, revision, and escalation. Run manually after reviewing the spec.
user-invocable: true
---

# Plan Auto-Orchestrator

Run the automated plan generation and critic loop for the current feature branch.

All orchestration logic lives in `.claude/agents/ch-1-plan-auto.py`. This skill is a thin
invocation wrapper — do not re-implement the loop here.

---

## Execution

Run from the repo root:

```bash
python .claude/agents/ch-1-plan-auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/ch-1-plan-auto.py --feature 015-job-description-rich-text
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

1. Validates pre-flight conditions (spec.md exists; plan.md overwrite prompt if applicable)
2. Generates `plan.md` via the plan agent (skipped if already exists and not force-regenerating)
3. Runs an iterative two-gate review loop (up to 3 iterations):
   - **Gate 1 — Plan critic**: validates spec/constitution/traceability compliance
   - **Gate 2 — Architecture review**: validates architecture quality and best practices
4. Revises `plan.md` between iterations to address any blocking violations
5. On both gates PASS: triggers auto-commit via the git extension
6. On 3-iteration exhaustion: writes `specs/$FEATURE/ch-1-plan-critic-escalation.md` and exits non-zero

The script is resume-safe: re-running after an interruption continues from the last
incomplete step using result files as idempotency markers.

---

## What this skill does not do

- Does not implement planning or critic logic — that lives in ch-1-plan-auto.py and its subagents
- Does not proceed to task generation — run `/ch-2-tasks-auto` after reviewing `plan.md`
- Does not push to remote
