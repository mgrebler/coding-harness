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

This gives the full list of source files changed on the feature branch. Read each changed file that is relevant to the checklist rules below. Focus on:

- `backend/src/api/` — tRPC router files
- `backend/src/services/` — service layer files
- `backend/tests/` — backend test files
- `frontend/src/components/` — component files
- `frontend/src/hooks/` — hook files
- `frontend/src/pages/` — page files
- `frontend/tests/` — frontend test files
- `prisma/schema.prisma` — schema file
- `prisma/migrations/` — migration SQL files
- `package.json`, `frontend/package.json`, `backend/package.json` — dependency files

Do not read files outside these paths unless a specific artifact references them.

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
- Test files (`backend/tests/`, `frontend/tests/`) must NOT have been created or modified during the implement phase — test files belong to the test phase and are read-only during implementation; cite any test file that appears in the implement-phase changed set
- For every changed implementation file, a corresponding test file must exist on the branch (written during the test phase)
- Schema migration files (`prisma/migrations/`) are exempt from this rule (constitution §5 explicitly waives test requirements for migrations)

### §I3 — Stack Constraints [BLOCKING]
- No import statement in any changed file references a package not in the constitution §2 approved stack
- No new entry added to any `package.json` `dependencies` or `devDependencies` that is not already present in the codebase or covered by a constitution amendment in `plan.md`
- Verify against the approved stack: TypeScript, React, Vite, Hono, Prisma, PostgreSQL, Zod, tRPC, Vitest, Playwright, Tailwind, pnpm, Resend
- If `plan.md` proposes a constitution amendment for a new dependency, and that dependency matches what was added, this rule passes for that package

### §I4 — Backend Layer Separation [BLOCKING]
- No Prisma client calls (`prisma.`, `db.`) in any file under `backend/src/api/` (router layer)
- No tRPC procedure definitions in any file under `backend/src/services/` (service layer)
- Business logic (data transformation, validation beyond input shapes, orchestration) must not appear in `backend/src/api/` files
- The router layer calls service functions only; service functions call Prisma only
- If any of these boundaries are crossed, cite the exact file and line

### §I5 — Frontend Layer Separation [BLOCKING]
- No direct `trpc.` hook calls (`useQuery`, `useMutation`) inside files under `frontend/src/components/`
- No direct `trpc.` hook calls inside files under `frontend/src/pages/` — pages must call custom hooks from `frontend/src/hooks/` only
- Components receive data and callbacks via props only; they do not fetch data directly
- If any of these boundaries are crossed, cite the exact file and import or call

### §I6 — Test Coverage [BLOCKING]
- Every modified or added tRPC procedure has at least one Vitest integration test in `backend/tests/integration/`
- Every modified or added service function has at least one Vitest unit test in `backend/tests/unit/`
- Every modified or added UI component has at least one Vitest component test in `frontend/tests/component/` or is covered by a Playwright e2e test in `frontend/tests/e2e/`
- Test files must contain assertions that cover the happy path; an empty test file or a test with no `expect()` calls is a violation
- If a test file is referenced in `tasks.md` but is absent from the changed files, flag it

### §I7 — Spec Compliance [BLOCKING]
- The implementation must cover every acceptance criterion in `spec.md`
- No behaviour may be implemented that contradicts or extends `spec.md` without a noted exception in `plan.md`
- Assess by reading test assertions and component/service logic against the spec's acceptance criteria
- Out-of-scope items explicitly noted in `plan.md` are exempt

### §I8 — Contract Compliance [BLOCKING]
- Implemented Zod input schemas in `backend/src/api/` must match the schemas defined in `$SPEC_DIR/contracts/`
- Implemented output types must match the output schemas in `$SPEC_DIR/contracts/`
- No procedure may widen or narrow its input/output type beyond the contract without a new decision record in constitution §17
- If no `contracts/` directory exists for this feature, this rule is not_applicable

### §I9 — Schema Migration [BLOCKING]
- If `plan.md` or `data-model.md` describes schema changes, a migration file must exist in `prisma/migrations/` that was added on this branch
- The `prisma/schema.prisma` changes must match `data-model.md` exactly (field names, types, optionality, relations)
- Hand-written SQL in migration files is only permitted for data backfills explicitly justified in `data-model.md` or `tasks.md`; flag any other hand-written SQL
- If no schema changes are described in `plan.md`, this rule is not_applicable

### §I10 — Styling Compliance [BLOCKING]
- No CSS Modules (`.module.css` files) in any changed frontend file
- No `style={}` inline style props in any changed JSX/TSX
- No new global stylesheet imports beyond the Tailwind base import
- Tailwind utility classes are the only permitted styling mechanism

### §I11 — TypeScript Safety [WARNING]
- No `as any` casts in changed files without a comment on the same or preceding line explaining why
- No `// @ts-ignore` or `// @ts-expect-error` in changed files without a comment explaining the specific reason
- No implicit `any` from untyped function parameters
- Non-null assertions (`!`) are permitted but flag any that operate on values that could genuinely be null at runtime

### §I12 — CI Readiness [WARNING]
- No patterns that would cause `tsc --noEmit` to fail: missing type annotations on exported functions, incompatible type assignments, missing return types on public API functions
- No patterns that would cause `eslint` to flag errors: unused imports, unused variables, `console.log` statements left in production code
- No test files with `test.only` or `describe.only` that would cause other tests to be silently skipped in CI

---

## Output Schema

```json
{
  "iteration": 1,
  "status": "PASS | FAIL",
  "violations": [
    {
      "rule": "<rule label, e.g. §I4 — Backend Layer Separation>",
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
