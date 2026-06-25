---
name: speckit-spec-approve
description: Approves the spec for the current feature branch. Creates the spec-approved file, commits it with a standard message, and triggers the automated plan-critic loop via the post-commit git hook. Use when the user has reviewed spec.md and is ready to approve it.
user-invocable: true
---

# Spec Approve

Approve the spec for the current feature branch. This skill is the human gate between spec review and automated plan generation. Running it signals that `spec.md` has been reviewed and is ready for planning to begin.

---

## Setup

Run `.specify/scripts/bash/setup-plan.sh --json` from the repo root and parse the JSON output for:
- `BRANCH` — current feature branch name (e.g. `012-passwordless-auth`)
- `SPECS_DIR` — path to the specs directory (e.g. `specs/`)
- `FEATURE_SPEC` — path to `spec.md` for this feature

Derive `FEATURE` as the feature folder name from `BRANCH` (e.g. `012-passwordless-auth`).
Derive `SPEC_DIR` as `$SPECS_DIR/$FEATURE/`.

---

## Pre-flight Checks

1. Confirm you are NOT on `main`. If on `main`, stop and print:
   ```
   [spec-approve] ERROR: Must be on a feature branch. Currently on main.
   ```

2. Confirm `$SPEC_DIR/spec.md` exists. If not, stop and print:
   ```
   [spec-approve] ERROR: specs/$FEATURE/spec.md not found. Run /speckit-specify first.
   ```

3. Confirm `$SPEC_DIR/spec-approved` does NOT already exist. If it does, stop and print:
   ```
   [spec-approve] ERROR: specs/$FEATURE/spec-approved already exists. Spec is already approved.
   ```

4. Confirm `spec.md` has no uncommitted changes. If there are staged or unstaged changes to `spec.md`, stop and print:
   ```
   [spec-approve] ERROR: spec.md has uncommitted changes. Commit or discard them before approving.
   ```

---

## Execution

1. Create `specs/$FEATURE/spec-approved` with the following content:
   ```
   approved: true
   feature: $FEATURE
   branch: $BRANCH
   timestamp: {current ISO 8601 datetime}
   ```

2. Stage the file:
   ```bash
   git add specs/$FEATURE/spec-approved
   ```

3. Commit with the standard message:
   ```bash
   git commit -m "chore(spec): approve spec for $FEATURE"
   ```

4. Print confirmation:
   ```
   [spec-approve] ✓ Spec approved for $FEATURE
   Committed: chore(spec): approve spec for $FEATURE
   The post-commit hook will now trigger automated plan generation.
   ```

---

## What This Skill Does Not Do

- Does not modify `spec.md`
- Does not run the plan agent directly — that is triggered by the post-commit git hook
- Does not push to remote — push is a separate human decision
