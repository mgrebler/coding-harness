---
name: ch-plan-to-implement-auto
description: Runs the full plan → tasks → test → implement pipeline for the current feature branch unattended, without stopping for review between stages. Chains ch-1-plan-auto.py, ch-2-tasks-auto.py, ch-3-test-auto.py, and ch-4-implement-auto.py sequentially. Resume-safe — re-running after any interruption continues from the first incomplete stage.
user-invocable: true
---

# Plan-to-Implement Auto-Orchestrator

Run the full automated pipeline for the current feature branch: plan generation,
task generation, test writing, and implementation — chained end-to-end without
stopping for human review between stages.

All orchestration logic lives in `.claude/agents/ch-plan-to-implement-auto.py`. This
skill is a thin invocation wrapper — do not re-implement the logic here.

---

## Pre-flight Requirements

Before running, ensure:
- You are on a feature branch (not `main`)
- `specs/<feature>/spec.md` exists

---

## Execution

Run from the repo root:

```bash
python .claude/agents/ch-plan-to-implement-auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/ch-plan-to-implement-auto.py --feature 016-my-feature
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

Runs four stages in sequence. Each stage must pass before the next begins.

**Stage 1 — Plan** (`ch-1-plan-auto.py`):
- Generates `plan.md` via the plan agent
- Runs iterative two-gate review (plan critic + architecture review, up to 3 iterations)
- On PASS: triggers auto-commit via the git extension

**Stage 2 — Tasks** (`ch-2-tasks-auto.py`):
- Generates `tasks.md` via the tasks agent
- Runs iterative tasks critic review (up to 3 iterations)
- On PASS: triggers auto-commit via the git extension

**Stage 3 — Test** (`ch-3-test-auto.py`):
- Writes failing tests for all `[TEST]` tasks in `tasks.md`
- Runs iterative test-critic review (up to 3 iterations)
- On PASS: triggers auto-commit via the git extension

**Stage 4 — Implement** (`ch-4-implement-auto.py`):
- Implements all unchecked tasks in `tasks.md`
- Runs iterative two-gate review (implement critic + code quality review, up to 3 iterations)
- Runs CI checks (typecheck, unit tests, e2e) after both gates pass
- On PASS: triggers auto-commit via the git extension

---

## Resume behaviour

Re-running after any interruption (token limit, crash, CI failure) resumes from
the first incomplete stage. Stage completion is tracked purely via the result
files each sub-script produces:

| Stage done when... | File present with status PASS |
|---|---|
| Plan | `specs/<feature>/ch-1-plan-architecture-review-result-*.json` |
| Tasks | `specs/<feature>/ch-2-tasks-critic-result-*.json` |
| Test | `specs/<feature>/ch-3-test-critic-result-*.json` |
| Implement | `specs/<feature>/ch-4-implement-code-quality-review-result-*.json` |

---

## Stage failure

If any stage fails after exhausting its retry limit, the script exits non-zero
and logs which escalation file to review:

| Stage | Escalation file |
|---|---|
| Plan | `specs/<feature>/ch-1-plan-critic-escalation.md` |
| Tasks | `specs/<feature>/ch-2-tasks-critic-escalation.md` |
| Test | `specs/<feature>/ch-3-test-critic-escalation.md` |
| Implement | `specs/<feature>/ch-4-implement-critic-escalation.md` |

Fix the issues described in the escalation file, then re-run this skill to
resume from the failed stage.

---

## Relationship to the human-in-the-loop workflow

Both workflows gate purely on the artifacts above — there are no approval
marker files or git hooks in either. The only difference is that the
human-in-the-loop workflow runs one stage at a time, via the individual
`/ch-N-*-auto` skills, so you can review the artifact between stages,
while this skill runs all four stages in sequence unattended.

---

## What this skill does not do

- Does not replace human review — use the individual skills if you want to
  review plan.md or tasks.md before proceeding
- Does not push to remote or open a pull request
