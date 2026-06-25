---
name: speckit-plan-approve
description: Approves the plan for the current feature branch. Creates the plan-approved file, commits it with a standard message, and triggers the automated task generation loop via the post-commit git hook. Use when the user has reviewed plan.md and is ready to approve it.
user-invocable: true
---

# Plan Approve

Approve the plan for the current feature branch. This skill is the human gate between plan review and automated task generation. Running it signals that `plan.md` has been reviewed and is ready for task generation to begin.

---

## Setup

Run `git rev-parse --abbrev-ref HEAD` to get `BRANCH`.

Derive `FEATURE` from `BRANCH` (e.g. `014-rich-text-formatting`).
Derive `SPEC_DIR` as `specs/$FEATURE/`.

---

## Pre-flight Checks

1. Confirm you are NOT on `main`. If on `main`, stop and print:
   ```
   [plan-approve] ERROR: Must be on a feature branch. Currently on main.
   ```

2. Confirm `$SPEC_DIR/plan.md` exists. If not, stop and print:
   ```
   [plan-approve] ERROR: specs/$FEATURE/plan.md not found. Run /speckit-plan first.
   ```

3. Confirm `$SPEC_DIR/plan-approved` does NOT already exist. If it does, stop and print:
   ```
   [plan-approve] ERROR: specs/$FEATURE/plan-approved already exists. Plan is already approved.
   ```

4. Confirm `plan.md` has no uncommitted changes. If there are staged or unstaged changes to `plan.md`, stop and print:
   ```
   [plan-approve] ERROR: plan.md has uncommitted changes. Commit or discard them before approving.
   ```

---

## Execution

1. Create `specs/$FEATURE/plan-approved` with the following content:
   ```
   approved: true
   feature: $FEATURE
   branch: $BRANCH
   timestamp: {current ISO 8601 datetime}
   ```

2. Stage the file:
   ```bash
   git add specs/$FEATURE/plan-approved
   ```

3. Commit with the standard message:
   ```bash
   git commit -m "chore(plan): approve plan for $FEATURE"
   ```

4. Print confirmation:
   ```
   [plan-approve] ✓ Plan approved for $FEATURE
   Committed: chore(plan): approve plan for $FEATURE
   The post-commit hook will now trigger automated task generation.
   ```

---

## What This Skill Does Not Do

- Does not modify `plan.md`
- Does not run the tasks agent directly — that is triggered by the post-commit git hook
- Does not push to remote — push is a separate human decision
