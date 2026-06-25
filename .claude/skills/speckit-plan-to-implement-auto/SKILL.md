---
name: speckit-plan-to-implement-auto
description: Runs the full plan → tasks → implement pipeline for the current feature branch without human approval gates between stages. Chains plan-auto.py, tasks-auto.py, and implement-auto.py sequentially; creates approval files automatically when each stage passes. Resume-safe — re-running after any interruption continues from the first incomplete stage.
user-invocable: true
---

# Plan-to-Implement Auto-Orchestrator

Run the full automated pipeline for the current feature branch: plan generation,
task generation, and implementation — chained end-to-end without manual approval
gates between stages.

All orchestration logic lives in `.claude/agents/plan-to-implement-auto.py`. This
skill is a thin invocation wrapper — do not re-implement the logic here.

---

## Pre-flight Requirements

Before running, ensure:
- You are on a feature branch (not `main`)
- `specs/<feature>/spec.md` exists

No `*-approved` files are required — the pipeline bypasses approval-file gates entirely.

---

## Execution

Run from the repo root:

```bash
python .claude/agents/plan-to-implement-auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/plan-to-implement-auto.py --feature 016-my-feature
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

Runs three stages in sequence. Each stage must pass before the next begins.

**Stage 1 — Plan** (`plan-auto.py`):
- Generates `plan.md` via the plan agent
- Runs iterative two-gate review (plan critic + architecture review, up to 3 iterations)
- On PASS: writes and commits `specs/<feature>/plan-approved`

**Stage 2 — Tasks** (`tasks-auto.py`):
- Generates `tasks.md` via the tasks agent
- Runs iterative tasks critic review (up to 3 iterations)
- On PASS: writes and commits `specs/<feature>/tasks-approved`

**Stage 3 — Implement** (`implement-auto.py`):
- Implements all unchecked tasks in `tasks.md`
- Runs iterative two-gate review (implement critic + code quality review, up to 3 iterations)
- Runs CI checks (typecheck, unit tests, e2e) after both gates pass
- On PASS: triggers auto-commit via the git extension

---

## Resume behaviour

Re-running after any interruption (token limit, crash, CI failure) resumes from
the first incomplete stage. Stage completion is tracked via the result files
each sub-script produces — no `*-approved` files are created or consulted:

| Stage done when... | File present with status PASS |
|---|---|
| Plan | `specs/<feature>/architecture-review-result-*.json` |
| Tasks | `specs/<feature>/tasks-critic-result-*.json` |
| Implement | `specs/<feature>/code-quality-review-result-*.json` |

---

## Stage failure

If any stage fails after exhausting its retry limit, the script exits non-zero
and logs which escalation file to review:

| Stage | Escalation file |
|---|---|
| Plan | `specs/<feature>/plan-critic-escalation.md` |
| Tasks | `specs/<feature>/tasks-critic-escalation.md` |
| Implement | `specs/<feature>/implement-critic-escalation.md` |

Fix the issues described in the escalation file, then re-run this skill to
resume from the failed stage.

---

## Relationship to existing hooks and manual workflow

The manual workflow uses `*-approved` files as git hook triggers to auto-launch
the next stage after a human approval commit. Those files are not required by
any sub-script. No approval files are created or committed by this pipeline,
so the post-commit hook never fires. The two workflows are fully independent.

---

## What this skill does not do

- Does not replace human review — use the individual skills if you want to
  review plan.md or tasks.md before proceeding
- Does not push to remote or open a pull request
