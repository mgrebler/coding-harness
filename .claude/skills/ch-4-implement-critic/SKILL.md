---
name: ch-4-implement-critic
description: Validates implemented code against tasks.md, plan.md, spec.md, and constitution.md. Checks TDD order, layer separation, stack constraints, test coverage, contract compliance, and spec adherence. Returns structured pass/fail output. Use after completing a task or the full implementation to catch violations before pushing.
user-invocable: true
---

# Implementation Critic Agent

Validate the code written on the current branch against `tasks.md`, `plan.md`, `spec.md`, and `constitution.md`. Return structured pass/fail output. Do not suggest rewrites. Do not write code. Do not fix violations. You identify violations only.

A violation is a specific, citable deviation from a rule below — a wrong import, a missing test, a bypassed layer, a type cast without justification, a file that should not exist. Vague observations ("this could be cleaner") are not violations and must not appear in output.

Return ONLY valid JSON matching the output schema below. No preamble. No explanation outside the JSON. No markdown fences.

---

## Setup

Run `git rev-parse --abbrev-ref HEAD` to get `BRANCH`.

If `$ARGUMENTS` is provided, use it as `FEATURE`. Otherwise derive `FEATURE` from `BRANCH` (e.g. branch `015-job-description-rich-text` → `015-job-description-rich-text`).

Set `SPEC_DIR` to `specs/$FEATURE/`.

Section numbers below (e.g. "constitution §2") refer to this project's own `constitution.md` — every installed project customizes that file, so numbering may not match the harness's default template. Locate the referenced content by its heading text if the number has drifted.

---

## Input Package

### Step 1 — Read spec documents

| File | Path |
|---|---|
| Constitution | `.specify/memory/constitution.md` |
| Architecture | `.specify/memory/architecture.md` |
| Spec | `$SPEC_DIR/spec.md` |
| Plan | `$SPEC_DIR/plan.md` |
| Tasks | `$SPEC_DIR/tasks.md` |
| Contracts | `$SPEC_DIR/contracts/` (all files, if present) |
| Data model | `$SPEC_DIR/data-model.md` (if present) |

### Step 2 — Identify changed files

Run: `git diff main...HEAD --name-only`

This gives the full list of source files changed on the feature branch. Read each changed file that is relevant to the checklist rules below — the source/test directories declared in `architecture.md` and constitution §5 (Test-Driven Development), plus any dependency manifest(s) for the project's package manager. Do not read files outside these paths unless a specific artifact references them.

---

## Checklist

Check each rule in order. Every rule must appear in the output as either a violation, a not_applicable entry, or an implicit pass (no entry needed for clean passes).

### §I1 — Task Traceability [BLOCKING]
- Every changed source file corresponds to a file path listed in `tasks.md`
- No implementation file is added or modified that is not referenced in any task entry
- If a changed file is a test file and the paired implementation file is also changed, traceability is satisfied by the implementation task's test pairing
- Spec or plan documents in `specs/` are excluded from this check

### §I2 — Test Phase Gate [BLOCKING]
- `specs/$FEATURE/ch-3-test-quality-review-result-*.json` must exist with `"status": "PASS"` — if no passing result exists, the implementation phase should not have started
- Test files (per the "Test file location" bullets under constitution §5) must NOT have been created or modified during the implement phase — test files belong to the test phase and are read-only during implementation; cite any test file that appears in the implement-phase changed set
- For every changed implementation file, a corresponding test file must exist on the branch (written during the test phase)
- Schema migration files are exempt from this rule (constitution's Test-Driven Development section, §5, explicitly waives test requirements for migrations)

### §I3 — Stack Constraints [BLOCKING]
- No import statement in any changed file references a package outside the approved stack in the constitution's Stack Constraints section (§2)
- No new entry added to any dependency manifest (`package.json`, `pyproject.toml`, `go.mod`, etc.) that is not already present in the codebase or covered by a constitution amendment in `plan.md`
- If `plan.md` proposes a constitution amendment for a new dependency, and that dependency matches what was added, this rule passes for that package

### §I4 — Layer Separation [BLOCKING]
- No layer boundary declared in `architecture.md` (e.g. router/handler layer vs. service layer vs. data-access layer, or component vs. data-fetching layer on the frontend) is bypassed — cite the specific boundary from `architecture.md` that was crossed
- Business logic (data transformation, validation beyond input shapes, orchestration) must not appear in a layer `architecture.md` designates as thin routing/presentation only
- If any of these boundaries are crossed, cite the exact file and line, and name the `architecture.md` rule it violates

### §I6 — Test Coverage [BLOCKING]
- Every modified or added API procedure/endpoint has at least one integration test, per the coverage requirements in constitution §5
- Every modified or added service/utility function has at least one unit test, per the coverage requirements in constitution §5
- Every modified or added UI component has at least one component or e2e test, per the coverage requirements in constitution §5
- Test files must contain assertions that cover the happy path; an empty test file or a test with no assertions is a violation
- If a test file is referenced in `tasks.md` but is absent from the changed files, flag it

### §I7 — Spec Compliance [BLOCKING]
- The implementation must cover every acceptance criterion in `spec.md`
- No behaviour may be implemented that contradicts or extends `spec.md` without a noted exception in `plan.md`
- Assess by reading test assertions and component/service logic against the spec's acceptance criteria
- Out-of-scope items explicitly noted in `plan.md` are exempt

### §I8 — Contract Compliance [BLOCKING]
- Implemented input/output schemas match the contracts defined in `$SPEC_DIR/contracts/`
- No procedure/endpoint may widen or narrow its input/output type beyond the contract without a new decision record in the constitution's Decision Records section (§17)
- If no `contracts/` directory exists for this feature, this rule is not_applicable

### §I9 — Schema Migration [BLOCKING]
- If `plan.md` or `data-model.md` describes schema changes, a migration file must exist that was added on this branch, using the migration mechanism declared in constitution §3 (Data Model Authority)
- The schema source of truth (per constitution §3) must match `data-model.md` exactly (field names, types, optionality, relations)
- Hand-written migration code is only permitted for data backfills explicitly justified in `data-model.md` or `tasks.md`; flag any other hand-written migration SQL/code
- If no schema changes are described in `plan.md`, this rule is not_applicable

### §I10 — Styling Compliance [BLOCKING]
- If `constitution.md` or `architecture.md` names an approved styling mechanism, no other styling approach (e.g. an unapproved CSS-in-JS library, inline styles, ad hoc global stylesheets) is introduced without justification
- If no styling constraint is declared in the loaded documents, this rule is not_applicable

### §I11 — Type Safety [WARNING]
- If the approved stack (constitution §2) includes a statically-typed language, no unjustified escape hatches from that type system (e.g. `as any`/`@ts-ignore` in TypeScript, `# type: ignore` in Python, `interface{}` casts in Go) appear without a comment explaining why
- No implicit loosening of function-parameter or return types
- If the approved stack has no static typing, this rule is not_applicable

### §I12 — CI Readiness [WARNING]
- No patterns that would cause any check declared in constitution §12 (CI Requirements) to fail — e.g. missing type annotations where typecheck is required, lint violations (unused imports/variables, stray debug statements) where lint is required
- No test files disable/skip other tests in a way that would cause them to be silently skipped in CI (e.g. an exclusive-run marker left in place)

---

## Output Schema

```json
{
  "iteration": 1,
  "status": "PASS | FAIL",
  "violations": [
    {
      "rule": "<rule label, e.g. §I4 — Layer Separation>",
      "severity": "BLOCKING | WARNING",
      "location": "<file path and line number or function name>",
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
- If no result file exists → write `$SPEC_DIR/ch-4-implement-critic-result-1.json`
- If `ch-4-implement-critic-result-1.json` exists → write `ch-4-implement-critic-result-2.json`
- If `ch-4-implement-critic-result-2.json` exists → write `ch-4-implement-critic-result-3.json`

Add an `iteration` field to the JSON before writing (as shown in the output schema above).

After writing, print a single confirmation line to the session:

```
[ch-4-implement-critic] iteration 1 → FAIL (3 blocking, 1 warning) → specs/015-feature/ch-4-implement-critic-result-1.json
```

or

```
[ch-4-implement-critic] iteration 1 → PASS → specs/015-feature/ch-4-implement-critic-result-1.json
```

---

## Iteration Rules

- If `status: FAIL` — return the violations JSON to the Implementation Agent. The Implementation Agent fixes the violations and this skill is re-run.
- Maximum 3 iterations. If the implementation has not passed after 3 runs, stop and escalate to the human with the full violation history from all attempts.
- If `status: PASS` — hand output to the human reviewer. Human review is still required. This skill clears mechanical violations only; it does not replace human judgment.

---

## Scope Limits

This skill does not:
- Write, edit, or fix any code
- Run the TypeScript compiler, linter, or test suite — it reads files and identifies patterns
- Validate whether tests actually pass — only that they exist and contain assertions
- Assess architectural quality beyond the specific rules above — use `/ch-1-plan-architecture-review` for that
- Validate spec.md or plan.md themselves — use `/ch-1-plan-critic` for that
- Validate tasks.md — use `/ch-2-tasks-critic` for that
- Replace human review or CI
