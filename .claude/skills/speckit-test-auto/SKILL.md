---
name: speckit-test-auto
description: Runs the automated test phase loop for the current feature branch by invoking test-auto.py. Writes failing tests for all [TEST] tasks, runs iterative test-critic review, applies fixes, and escalates on failure. Run manually after reviewing tasks.md.
user-invocable: true
---

# Test Auto-Orchestrator

Run the automated test phase loop for the current feature branch.

All orchestration logic lives in `.claude/agents/test-auto.py`. This skill is a thin
invocation wrapper — do not re-implement the loop here.

---

## Execution

Run from the repo root:

```bash
python .claude/agents/test-auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/test-auto.py --feature 015-job-description-rich-text
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

1. Validates pre-flight conditions (spec.md, plan.md, tasks.md exist)
2. Runs the test agent to write failing tests for all unchecked [TEST] tasks in tasks.md
   (skipped if all [TEST] tasks are already checked off)
3. Runs an iterative test-critic review loop (up to 3 iterations):
   - **Gate — Test critic**: validates red-state confirmation, spec coverage, test isolation,
     assertion quality, stack compliance, and test naming
   - FAIL → fix agent addresses violations in test files only → re-run critic
   - PASS → done
4. On PASS: triggers auto-commit via the `after_test` git extension hook
5. On 3-iteration exhaustion: writes `specs/$FEATURE/test-critic-escalation.md` and exits non-zero

The script is resume-safe: re-running after any interruption continues from the last
incomplete step using result files as idempotency markers.

---

## What this skill does not do

- Does not implement any feature logic — that is done by `/speckit-implement` or `/speckit-implement-auto`
- Does not push to remote or open a pull request
- Does not run the implement phase — run `/speckit-implement-auto` after reviewing the test files
