---
name: "speckit-test"
description: "Execute the test phase by writing failing tests for all [TEST] tasks defined in tasks.md. No implementation code is written."
argument-hint: "Optional task filter or guidance"
compatibility: "Requires spec-kit project structure with .specify/ directory"
metadata:
  author: "github-spec-kit"
  source: "custom"
user-invocable: true
disable-model-invocation: false
---


## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before test phase)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_test` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- When constructing slash commands from hook command names, replace dots (`.`) with hyphens (`-`). For example, `speckit.git.commit` → `/speckit-git-commit`.
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Pre-Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Pre-Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}
    
    Wait for the result of the hook command before proceeding to the Outline.
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Outline

1. Run `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` from repo root and parse FEATURE_DIR and AVAILABLE_DOCS list. All paths must be absolute.

2. Load implementation context:
   - **REQUIRED**: Read `tasks.md` — identify all unchecked [TEST] tasks (`- [ ]` lines containing `[TEST]`)
   - **REQUIRED**: Read `plan.md` — tech stack, architecture, file structure
   - **REQUIRED**: Read `.specify/memory/test-principles.md` — authoritative quality bar for all tests written in this phase
   - **IF EXISTS**: Read `data-model.md` for entities and relationships
   - **IF EXISTS**: Read `contracts/` for API specifications and acceptance criteria
   - **IF EXISTS**: Read `spec.md` for user stories and acceptance criteria

3. If no unchecked [TEST] tasks exist in tasks.md, print:
   ```
   [speckit-test] All [TEST] tasks already complete. Run /speckit-test-critic to validate.
   ```
   and stop.

4. Execute the test phase — for each unchecked [TEST] task in tasks.md, in dependency order:

   **For each [TEST] task:**
   a. Write the failing test file(s) for this behaviour only.
      - Tests must encode the acceptance criteria from spec.md for this unit of behaviour.
      - No implementation code under any circumstances — test files only.
      - If the test file already exists (from a prior interrupted run), read it first.
   b. Run the test suite targeting the new test file. Use the appropriate command:
      - Backend tests: `pnpm --filter backend test -- <test-file-path>`
      - Frontend component tests: `pnpm --filter frontend test -- <test-file-path>`
      - E2E tests: `pnpm test:e2e -- <test-file-path>`
   c. Confirm the test **FAILS for the expected reason**:
      - ACCEPTABLE failures: assertion failure (`Expected X, received Y`), "not implemented"
        error, "cannot find module/export" error when the code does not yet exist.
      - UNACCEPTABLE failures: syntax errors in the test file, import errors from the test file
        itself (not from missing implementation). Fix these before recording output.
      - If a test passes immediately without any implementation, do NOT record it as red.
        Flag the issue: "Test for [task ID] passes without implementation — test may be
        tautological. Review before proceeding."
   d. Save the failing output to `specs/$FEATURE/test-results/<TASKID>-red.txt`.
      Create the `test-results/` directory if it does not exist.
   e. Mark the [TEST] task as `[x]` in tasks.md.
   f. Report: `[TEST] <TASKID> complete — red output saved to test-results/<TASKID>-red.txt`

5. Progress tracking:
   - Report progress after each completed [TEST] task.
   - If a [TEST] task fails with a syntax error (test file itself is broken), fix the syntax and re-run before recording.
   - Do not proceed to the next task until the current one is recorded red.
   - **IMPORTANT**: Mark each completed [TEST] task as `[x]` in tasks.md immediately after recording.

6. Completion:
   - Report total [TEST] tasks completed and red-output artifacts written.
   - Print:
     ```
     [speckit-test] Test phase complete. Next steps:
       Run /speckit-test-critic to validate test quality
       or /speckit-test-auto for the automated loop.
     ```

7. **Check for extension hooks**: After completion, check if `.specify/extensions.yml` exists in the project root.
   - If it exists, read it and look for entries under the `hooks.after_test` key
   - Follow the same hook execution pattern as `before_test` above
   - If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Constraints

- **No implementation code**: This phase writes test files only. If you find yourself writing
  implementation logic, stop immediately.
- **No [IMPL] tasks**: Only process tasks labelled `[TEST]` in tasks.md. Leave all `[IMPL]`
  tasks untouched.
- **test-principles.md is mandatory context**: Read it before writing any test. All tests must
  conform to the principles defined there.
- **Red before recording**: Never record a green test as a red-output artifact. If a test
  passes without implementation, flag it rather than falsify the artifact.
