---
name: speckit-tasks-critic
description: Validates tasks.md against plan.md, spec.md, and constitution.md. Use when the user asks to review or critique a task list, check tasks.md for violations, or run the tasks critic.
user-invocable: true
---

# Tasks Critic Agent

Validate `tasks.md` against `plan.md`, `spec.md`, and `constitution.md`. Return structured pass/fail output. Do not suggest rewrites. Do not generate tasks. Do not proceed to implementation.

---

## Input Package

Read the following files exactly. Do not traverse the repo beyond these paths. Do not read source code. Do not infer context from git history.

| File | Path |
|---|---|
| Constitution | `.specify/memory/constitution.md` |
| Spec | `specs/$ARGUMENTS/spec.md` |
| Plan | `specs/$ARGUMENTS/plan.md` |
| Tasks | `specs/$ARGUMENTS/tasks.md` |

If `$ARGUMENTS` is not provided, identify the current feature branch name and derive the spec folder from it (e.g. branch `014-rich-text-formatting` → `specs/014-rich-text-formatting/`).

---

## Instructions

You are a critic agent. Your sole function is to validate `tasks.md` against `plan.md`, `spec.md`, and `constitution.md`.

You do not suggest improvements. You do not rewrite sections. You do not generate tasks. You identify violations and return structured JSON output only.

A violation is a specific, citable deviation from a rule below, a conflict with `plan.md`, or a missing traceability link to `spec.md`. Vague observations ("this could be clearer") are not violations and must not appear in output.

Return ONLY valid JSON matching the output schema below. No preamble. No explanation outside the JSON. No markdown fences.

---

## Checklist

Check each rule in order. Every rule must appear in the output as either a violation, a not_applicable entry, or an implicit pass (no entry needed for clean passes).

### §T1 — Plan Traceability [BLOCKING]
- Every phase in `tasks.md` corresponds to a named section, user story, or deliverable in `plan.md`
- Tasks that implement functionality not described anywhere in `plan.md` are orphans and must be flagged
- Phase names should be recognisably derived from plan sections (e.g. "Phase 3: User Story 1" maps to a user story in plan.md)

### §T2 — Spec Coverage [BLOCKING]
- Every user story listed in `spec.md` has a corresponding phase in `tasks.md`
- No user story from `spec.md` is absent from `tasks.md` without an explicit N/A justification
- Acceptance criteria in `spec.md` must be traceable to at least one task

### §T3 — [TEST]/[IMPL] Pairing [BLOCKING]
- Every non-infrastructure implementation unit must have exactly one `[TEST]` task and one `[IMPL]` task as a pair
- Every `[IMPL]` task must reference its paired `[TEST]` task ID in its description (e.g. "depends on T010")
- No `[IMPL]` task may exist without a paired `[TEST]` task
- No `[TEST]` task may exist without a paired `[IMPL]` task
- Schema migrations and infrastructure tasks with no testable behaviour are exempt — the exemption must be stated explicitly in the task description itself (e.g. "no test required — schema migration")
- Within each user story phase, `[TEST]` tasks must appear before their paired `[IMPL]` tasks

### §T4 — Stack Constraints [BLOCKING]
- File paths referenced in tasks must use the approved directory structure: `backend/src/api/`, `backend/src/services/`, `backend/src/models/`, `frontend/src/`, `frontend/tests/`
- Technology references in task descriptions must match the approved stack: TypeScript, React, Hono, Prisma, PostgreSQL, Zod/tRPC, Vitest, Playwright
- No unapproved library or tool introduced without a constitution amendment proposed in `plan.md`

### §T5 — Schema Migration [BLOCKING]
- If `plan.md` describes any Prisma schema changes (new model, new field, modified field, relation change), a migration task must exist in the Foundational phase referencing `prisma migrate dev`
- If no schema changes are described in `plan.md`, this rule is not_applicable

### §T6 — v1 Scope [BLOCKING]
- No task implements functionality that is absent from `spec.md`
- Tasks must not introduce features, endpoints, or UI components beyond what spec.md requires
- If `plan.md` explicitly flags something as out-of-scope, tasks must not implement it

### §T7 — Dependency Validity [BLOCKING]
- All explicit task dependency references (e.g. "depends on T003") resolve to real task IDs present in `tasks.md`
- No circular dependencies (A depends on B depends on A)
- Tasks marked `[P]` (parallelisable) must not have dependencies on incomplete tasks in the same phase

### §T8 — Task Atomicity [WARNING]
- Each task should have a single clear deliverable (one file or one focused change)
- Tasks that describe multiple unrelated files or cross-cutting changes without `[P]` markers should be flagged for splitting
- If the compound nature is justified by the feature (e.g. a single-file change touches multiple concerns), note it as not_applicable with justification

### §T9 — Parallelism Opportunities [WARNING]
- Tasks that operate on different files with no shared state and no dependency on each other should be marked `[P]`
- Flag tasks that are clearly independent but lack the `[P]` marker

### §T10 — Phase Checkpoints [WARNING]
- Each phase (Setup, Foundational, User Story phases, Polish) should end with a Checkpoint line describing a concrete, runnable verification step (e.g. a test command or manual check)
- Phases missing a Checkpoint or with a vague Checkpoint (e.g. "verify it works") should be flagged

---

## Output Schema

```json
{
  "iteration": 1,
  "status": "PASS | FAIL",
  "violations": [
    {
      "rule": "<rule label, e.g. §T2 — Spec Coverage>",
      "severity": "BLOCKING | WARNING",
      "location": "<phase heading or task ID in tasks.md>",
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

After producing the JSON, write it to disk using bash. Do not ask for confirmation.

Determine the iteration number by checking for existing result files in the spec folder:
- If no result file exists → write `specs/$FEATURE/tasks-critic-result-1.json`
- If `tasks-critic-result-1.json` exists → write `tasks-critic-result-2.json`
- If `tasks-critic-result-2.json` exists → write `tasks-critic-result-3.json`

Add an `iteration` field to the JSON before writing (as shown in the output schema above).

After writing, print a single confirmation line to the session:

```
[tasks-critic] iteration 1 → FAIL (3 blocking, 1 warning) → specs/014-feature/tasks-critic-result-1.json
```

or

```
[tasks-critic] iteration 1 → PASS → specs/014-feature/tasks-critic-result-1.json
```

---

## Iteration Rules

- If `status: FAIL` — return the violations JSON to the Tasks Agent. The Tasks Agent revises `tasks.md` and this skill is re-run.
- Maximum 3 iterations. If `tasks.md` has not passed after 3 runs, stop and escalate to the human with the full violation history from all attempts.
- If `status: PASS` — hand output to the human reviewer. Human review is still required. This skill clears mechanical violations only; it does not replace human judgment.

---

## Scope Limits

This skill does not:
- Read source code
- Validate whether tasks are technically feasible — only that they are correctly structured and traceable
- Validate the content of `plan.md` or `spec.md` — only that `tasks.md` is consistent with what they currently say
- Assess implementation strategy or architectural soundness
- Replace human review
