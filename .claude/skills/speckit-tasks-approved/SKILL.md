---
name: speckit-tasks-approve
description: Approves the tasks for the current feature branch. Creates the tasks-approved file, commits it with a standard message, and triggers the automated implementation loop via the post-commit git hook. Use when the user has reviewed tasks.md and is ready for implementation to begin.
user-invocable: true
---

# Tasks Approve

Approve the tasks for the current feature branch. This skill is the human gate between task review and automated implementation. Running it signals that `tasks.md` has been reviewed and is ready for implementation to begin.

---

## Setup

Run `git rev-parse --abbrev-ref HEAD` to get `BRANCH`.

Derive `FEATURE` from `BRANCH` (e.g. `015-job-description-rich-text`).
Derive `SPEC_DIR` as `specs/$FEATURE/`.

---

## Pre-flight Checks

1. Confirm you are NOT on `main`. If on `main`, stop and print:
   ```
   [tasks-approve] ERROR: Must be on a feature branch. Currently on main.
   ```

2. Confirm `$SPEC_DIR/tasks.md` exists. If not, stop and print:
   ```
   [tasks-approve] ERROR: specs/$FEATURE/tasks.md not found. Run /speckit-tasks first.
   ```

3. Confirm `$SPEC_DIR/tasks-approved` does NOT already exist. If it does, stop and print:
   ```
   [tasks-approve] ERROR: specs/$FEATURE/tasks-approved already exists. Tasks are already approved.
   ```

4. Confirm `tasks.md` has no uncommitted changes. If there are staged or unstaged changes to `tasks.md`, stop and print:
   ```
   [tasks-approve] ERROR: tasks.md has uncommitted changes. Commit or discard them before approving.
   ```

---

## Execution

1. Create `specs/$FEATURE/tasks-approved` with the following content:
   ```
   approved: true
   feature: $FEATURE
   branch: $BRANCH
   timestamp: {current ISO 8601 datetime}
   ```

2. Stage the file:
   ```bash
   git add specs/$FEATURE/tasks-approved
   ```

3. Commit with the standard message:
   ```bash
   git commit -m "chore(tasks): approve tasks for $FEATURE"
   ```

4. Print confirmation:
   ```
   [tasks-approve] ✓ Tasks approved for $FEATURE
   Committed: chore(tasks): approve tasks for $FEATURE
   The post-commit hook will now trigger automated implementation.
   ```

---

## What This Skill Does Not Do

- Does not modify `tasks.md`
- Does not run the implementation agent directly — that is triggered by the post-commit git hook
- Does not push to remote — push is a separate human decision
