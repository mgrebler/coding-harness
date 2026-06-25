---
name: speckit-test-approve
description: Approves the test phase for the current feature branch. Creates the test-approved file, commits it, and signals that implementation may begin. Use when the user has reviewed the test files and test-critic result and is ready for implementation.
user-invocable: true
---

# Test Approve

Approve the test phase for the current feature branch. This skill is the human gate between
test review and automated implementation. Running it signals that the test files have been
reviewed, the test-critic has passed, and implementation may begin.

---

## Setup

Run `git rev-parse --abbrev-ref HEAD` to get `BRANCH`.

Derive `FEATURE` from `BRANCH` (e.g. `015-job-description-rich-text`).
Derive `SPEC_DIR` as `specs/$FEATURE/`.

---

## Pre-flight Checks

1. Confirm you are NOT on `main`. If on `main`, stop and print:
   ```
   [test-approve] ERROR: Must be on a feature branch. Currently on main.
   ```

2. Confirm `$SPEC_DIR/tasks-approved` exists. If not, stop and print:
   ```
   [test-approve] ERROR: specs/$FEATURE/tasks-approved not found. Complete the tasks phase first.
   ```

3. Confirm at least one `$SPEC_DIR/test-critic-result-*.json` exists with `"status": "PASS"`.
   Read all `test-critic-result-*.json` files and check if any has `"status": "PASS"`. If not:
   ```
   [test-approve] ERROR: No passing test-critic result found in specs/$FEATURE/.
   Run /speckit-test-critic or /speckit-test-auto first.
   ```

4. Confirm `$SPEC_DIR/test-approved` does NOT already exist. If it does, stop and print:
   ```
   [test-approve] ERROR: specs/$FEATURE/test-approved already exists. Tests are already approved.
   ```

5. Confirm no test files have uncommitted changes. Run `git status --short` and check for
   modified files under `backend/tests/` or `frontend/tests/`. If found:
   ```
   [test-approve] ERROR: Test files have uncommitted changes. Commit or discard them before approving.
   ```

---

## Execution

1. Create `specs/$FEATURE/test-approved` with the following content:
   ```
   approved: true
   feature: $FEATURE
   branch: $BRANCH
   timestamp: {current ISO 8601 datetime}
   ```

2. Stage the file:
   ```bash
   git add specs/$FEATURE/test-approved
   ```

3. Commit with the standard message:
   ```bash
   git commit -m "chore(test): approve tests for $FEATURE"
   ```

4. Print confirmation:
   ```
   [test-approve] ✓ Tests approved for $FEATURE
   Committed: chore(test): approve tests for $FEATURE
   Implementation may now begin. Run /speckit-implement-auto to start.
   ```

---

## What This Skill Does Not Do

- Does not modify any test files
- Does not run the implementation agent directly
- Does not push to remote — push is a separate human decision
