---
name: ch-3-test-auto
description: Runs the automated test phase loop for the current feature branch by invoking ch-3-test-auto.py. Writes failing tests for all [TEST] tasks, runs the iterative test-critic and test-quality-review two-gate loop, applies fixes, and escalates on failure. Run manually after reviewing tasks.md.
user-invocable: true
---

# Test Auto-Orchestrator

Run the automated test phase loop for the current feature branch.

All orchestration logic lives in `.claude/agents/ch-3-test-auto.py`. This skill is a thin
invocation wrapper — do not re-implement the loop here.

---

## Execution

Run from the repo root:

```bash
python .claude/agents/ch-3-test-auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/ch-3-test-auto.py --feature 015-job-description-rich-text
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

1. Validates pre-flight conditions (spec.md, plan.md, tasks.md exist)
2. Runs the test agent to write failing tests for all unchecked [TEST] tasks in tasks.md
   (skipped if all [TEST] tasks are already checked off)
3. Runs an iterative two-gate review loop (up to 3 iterations):
   - **Gate 1 — Test critic**: validates task traceability, red-state confirmation, spec
     coverage, implementation leakage, test isolation, and stack compliance
   - **Gate 2 — Test quality review**: validates assertion quality, test naming, and CI
     readiness (runs only after Gate 1 passes)
   - Either gate FAIL → fix agent addresses violations in test files only → both gates re-run
   - Both gates PASS in the same iteration → done
4. On PASS: triggers auto-commit via the `after_test` git extension hook
5. On 3-iteration exhaustion: writes `specs/$FEATURE/ch-3-test-critic-escalation.md` and exits non-zero

The script is resume-safe: re-running after any interruption continues from the last
incomplete step using result files as idempotency markers.

---

## What this skill does not do

- Does not implement any feature logic — that is done by `/speckit-implement` or `/ch-4-implement-auto`
- Does not push to remote or open a pull request
- Does not run the implement phase — run `/ch-4-implement-auto` after reviewing the test files
