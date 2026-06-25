---
name: speckit-test-critic
description: Validates test files written during the test phase against test-principles.md, spec.md, and constitution.md. Checks red-state confirmation, spec coverage, test isolation, assertion quality, and naming discipline. Returns structured pass/fail output. Use after completing the test phase to gate entry into implementation.
user-invocable: true
---

# Test Critic Agent

Validate the test files written on the current branch against `test-principles.md`, `spec.md`, `tasks.md`, and `constitution.md`. Return structured pass/fail output. Do not suggest rewrites. Do not write code. Do not fix violations. You identify violations only.

A violation is a specific, citable deviation from a rule below — a missing artifact, a tautological assertion, an untested acceptance criterion, shared mutable state between tests. Vague observations ("this could be cleaner") are not violations and must not appear in output.

Return ONLY valid JSON matching the output schema below. No preamble. No explanation outside the JSON. No markdown fences.

---

## Setup

Run `git rev-parse --abbrev-ref HEAD` to get `BRANCH`.

If `$ARGUMENTS` is provided, use it as `FEATURE`. Otherwise derive `FEATURE` from `BRANCH` (e.g. branch `015-job-description-rich-text` → `015-job-description-rich-text`).

Set `SPEC_DIR` to `specs/$FEATURE/`.

---

## Input Package

### Step 1 — Read spec documents

| File | Path |
|---|---|
| Constitution | `.specify/memory/constitution.md` |
| Architecture | `.specify/memory/architecture.md` |
| Test principles | `.specify/memory/test-principles.md` |
| Spec | `$SPEC_DIR/spec.md` |
| Plan | `$SPEC_DIR/plan.md` |
| Tasks | `$SPEC_DIR/tasks.md` |

### Step 2 — Identify changed test files

Run: `git diff main...HEAD --name-only`

Filter results to test files only:
- `backend/tests/` — backend test files
- `frontend/tests/` — frontend test files

Read each changed test file in full. Do NOT read implementation files — none should exist at this stage.

### Step 3 — Read red-output artifacts

Read all files under `$SPEC_DIR/test-results/` (if directory exists). Each file is named `<TASKID>-red.txt` and contains the failing test output captured during the test phase.

---

## Checklist

Check each rule in order. Every rule must appear in the output as either a violation, a not_applicable entry, or an implicit pass (no entry needed for clean passes).

### §TQ1 — Task Traceability [BLOCKING]
- Every changed test file corresponds to a [TEST] task listed in `tasks.md`
- No test file is added or modified that is not referenced in any [TEST] task entry
- If a test file corresponds to a completed [x] [TEST] task, traceability is satisfied
- Spec documents in `specs/` are excluded from this check

### §TQ2 — Red State Confirmed [BLOCKING]
- A `test-results/<TASKID>-red.txt` artifact must exist for every [TEST] task that is marked [x]
- The artifact content must show a meaningful failure: an assertion failure (`Expected X, received Y`),
  a "not found" / "not implemented" error, or a similar runtime failure indicating the code under
  test does not yet exist
- A compile/syntax error in the test file itself is NOT an acceptable red state — it indicates
  a broken test, not a failing one
- If a test-results directory does not exist or is empty, this is a BLOCKING violation for every
  completed [TEST] task

### §TQ3 — Spec Coverage [BLOCKING]
- Test assertions must correspond to the acceptance criteria in `spec.md` for the behaviour
  under test
- Every acceptance criterion for each implemented user story must be traceable to at least one
  test assertion
- Acceptance criteria with no test coverage must be cited as violations
- Out-of-scope items explicitly noted in `plan.md` are exempt

### §TQ4 — No Implementation Code [BLOCKING]
- Test files must not contain implementation logic: no business rules, no database queries
  outside of test setup/teardown, no service orchestration
- Test helpers and fixtures are permitted, but must not embed logic that the production
  implementation would also contain
- If implementation-level logic is found in a test file, cite the specific function or block

### §TQ5 — Test Isolation [BLOCKING]
- No shared mutable state between test cases (module-level variables mutated by tests)
- If tests create database records, external files, or other stateful artifacts, cleanup/teardown
  must be present (`afterEach`, `afterAll`, or equivalent)
- Tests must not depend on execution order to pass
- If ordering dependency or shared state is found, cite the specific test block

### §TQ6 — Stack Compliance [BLOCKING]
- Test files must import only from approved test libraries: Vitest (unit/integration) or
  Playwright (e2e)
- No unapproved test libraries or test runners introduced
- If a test file imports from an unapproved library, cite the import statement

### §TQ7 — Assertion Quality [WARNING]
- No tautological assertions where the expected and actual values are always equal regardless
  of implementation (e.g. `expect(true).toBe(true)`, `expect(mock.returnValue).toBe(mock.returnValue)`)
- Each assertion must be capable of failing if the implementation is wrong
- If a tautological assertion is found, cite the specific `expect()` call

### §TQ8 — Test Naming [WARNING]
- Test names (in `it()`, `test()`, `describe()`) must describe the behaviour or scenario
  under test, not the implementation detail
- Names like "calls the service", "invokes the function", "runs the query" are implementation-
  coupled and should be cited
- Names like "returns 404 when job not found", "shows error message when form is invalid"
  are acceptable

### §TQ9 — CI Readiness [WARNING]
- No `test.only`, `describe.only`, or `it.only` in any changed test file — these cause
  other tests to be silently skipped in CI
- If found, cite the specific file and line

---

## Output Schema

```json
{
  "iteration": 1,
  "status": "PASS | FAIL",
  "violations": [
    {
      "rule": "<rule label, e.g. §TQ3 — Spec Coverage>",
      "severity": "BLOCKING | WARNING",
      "location": "<file path and line number or test name>",
      "finding": "<specific, citable description of the violation>"
    }
  ],
  "not_applicable": [
    {
      "rule": "<rule label>",
      "reason": "<why this rule does not apply to this feature>"
    }
  ],
  "summary": "<one paragraph: overall assessment, count of blocking violations, count of warnings, and the single most critical issue if status is FAIL>"
}
```

Rules:
- `status` is `FAIL` if any violation has `severity: BLOCKING`
- `status` is `PASS` only if zero BLOCKING violations exist (WARNING violations may be present)
- `violations` array is empty if status is PASS with no warnings
- Every checklist item that does not pass must appear in either `violations` or `not_applicable`

---

## File Output

After producing the JSON, write it to disk using Bash. Do not ask for confirmation.

Determine the iteration number by checking for existing result files in the spec folder:
- If no result file exists → write `$SPEC_DIR/test-critic-result-1.json`
- If `test-critic-result-1.json` exists → write `test-critic-result-2.json`
- If `test-critic-result-2.json` exists → write `test-critic-result-3.json`

Add an `iteration` field to the JSON before writing (as shown in the output schema above).

After writing, print a single confirmation line to the session:

```
[test-critic] iteration 1 → FAIL (3 blocking, 1 warning) → specs/015-feature/test-critic-result-1.json
```

or

```
[test-critic] iteration 1 → PASS → specs/015-feature/test-critic-result-1.json
```

---

## Iteration Rules

- If `status: FAIL` — return the violations JSON to the Test Fix Agent. The Test Fix Agent
  addresses the violations in test files only (no implementation code) and this skill is re-run.
- Maximum 3 iterations. If tests have not passed after 3 runs, stop and write
  `$SPEC_DIR/test-critic-escalation.md` with the full violation history from all attempts,
  then escalate to the human.
- If `status: PASS` — hand output to the human reviewer. Human review is still required.
  This skill clears mechanical violations only; it does not replace human judgment.

---

## Scope Limits

This skill does not:
- Read implementation files (`backend/src/`, `frontend/src/`) — none should exist yet
- Write, edit, or fix any test file
- Run the test suite — it reads test files and artifact content only
- Validate plan.md or spec.md themselves — use `/speckit-plan-critic` for that
- Validate tasks.md — use `/speckit-tasks-critic` for that
- Replace human review or CI
