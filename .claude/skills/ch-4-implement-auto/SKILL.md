---
name: ch-4-implement-auto
description: Runs the automated implementation and review loop for the current feature branch by invoking ch-4-implement-auto.py. Handles TDD implementation, iterative critic review, code quality review, fixes, and escalation. Run manually after reviewing the test files.
user-invocable: true
---

# Implement Auto-Orchestrator

Run the automated implementation and review loop for the current feature branch.

All orchestration logic lives in `.claude/agents/ch-4-implement-auto.py`. This skill is a thin
invocation wrapper — do not re-implement the loop here.

---

## Execution

Run from the repo root:

```bash
python .claude/agents/ch-4-implement-auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/ch-4-implement-auto.py --feature 015-job-description-rich-text
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

1. Validates pre-flight conditions (spec.md, plan.md, tasks.md exist; a passing ch-3-test-critic-result exists)
2. Runs the implementation agent to complete all unchecked tasks (`- [ ]`) in tasks.md following TDD order (skipped if all tasks already checked off)
3. Runs an iterative two-gate review loop (up to 3 iterations):
   - **Gate 1 — Implement critic**: validates task traceability, TDD compliance, layer separation, test coverage, contract compliance, spec adherence, and styling
   - **Gate 2 — Code quality review**: validates code quality, maintainability, readability, and operational safety
4. Runs the fix agent between iterations to address any blocking violations
5. On both gates PASS: triggers auto-commit via the git extension
6. On 3-iteration exhaustion: writes `specs/$FEATURE/ch-4-implement-critic-escalation.md` and exits non-zero

The script is resume-safe: re-running after an interruption continues from the last
incomplete step using result files as idempotency markers. The implementation agent
only processes remaining unchecked tasks.

---

## What this skill does not do

- Does not implement any feature logic directly — that is done by the implementation subagent
- Does not implement critic or quality review logic — that lives in ch-4-implement-auto.py and its subagents
- Does not push to remote or open a pull request
