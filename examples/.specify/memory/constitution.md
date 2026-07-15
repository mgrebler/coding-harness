# Constitution

**Version**: v1.0.0 | **Last amended**: [DATE]

**Supreme law for all agents on this project. Read this before acting. No rule here is discretionary.**

Changes to this document require explicit human decision and must be logged in `constitution_update_checklist.md` before taking effect.

---

## 1. Project Identity

Every agent reads `.specify/memory/product-context.md` before acting. That document is authoritative on product vision, user needs, v1 feature scope, and planned future capabilities. This section records only the subset of product context that directly constrains agent decisions.

**What this project is:** [PROJECT: One sentence describing the project and its purpose.]

**Decision-relevant product constraints:**

<!-- List 3–5 constraints that agents need to know when making decisions.
     Examples: single-user vs multi-tenant, cost ceiling, latency requirements,
     data sensitivity, offline-first, etc. -->

- [PROJECT: Constraint 1]
- [PROJECT: Constraint 2]
- [PROJECT: Constraint 3]

---

## 2. Stack Constraints

No agent may introduce a dependency, framework, or tool outside this list without a constitution amendment approved by the human.

| Layer | Mandated tool |
|---|---|
| Language | [PROJECT: e.g. TypeScript] |
| Frontend | [PROJECT: e.g. React + Vite] |
| Backend | [PROJECT: e.g. Hono on Node] |
| Database | [PROJECT: e.g. PostgreSQL via Supabase] |
| ORM | [PROJECT: e.g. Prisma] |
| API contracts | [PROJECT: e.g. tRPC + Zod] |
| Testing | [PROJECT: e.g. Vitest + Playwright] |
| Monorepo | [PROJECT: e.g. pnpm workspaces] |
| CI | [PROJECT: e.g. GitHub Actions] |
| Deployment | [PROJECT: e.g. Vercel] |

**Prohibited substitutions:** [PROJECT: List things that must NOT be introduced.]

---

## 3. Data Model Authority

<!-- Replace with whatever this project's actual schema/migration mechanism is
     (Prisma schema file, Django models, SQL migration directory, etc.) -->

- [PROJECT: e.g. `schema.prisma`] is the single source of truth for the data model.
- `data-model.md` documents intent. Any drift between `data-model.md` and [PROJECT: the schema source] is a defect.
- No field, table, or relation may be added, removed, or renamed outside a completed spec → plan → task chain.
- Migrations are generated from schema changes only ([PROJECT: e.g. `prisma migrate dev`]). Hand-written migrations are prohibited except for data backfills, which require explicit justification in the task entry.
- [PROJECT: e.g. The Prisma client] is the only permitted database access path. [PROJECT: e.g. No raw SQL in application code.]

---

## 4. API Contract Rules

<!-- Replace with whatever this project's actual API surface is (tRPC, REST,
     GraphQL, gRPC, ...) — the rule that matters is "one binding contract
     format, defined here" regardless of which one is chosen. -->

- [PROJECT: e.g. tRPC router definitions] in `specs/[###-feature]/contracts/` are the binding contract.
- **Breaking changes** — removing a procedure/endpoint, changing an input shape, removing an output field — require a decision record committed to this constitution before the implementing task begins.
- **Additive changes** — new optional input field, new output field, new procedure/endpoint — do not require a decision record but must be reflected in `contracts/` before code is written.
- [PROJECT: e.g. No REST endpoints. No GraphQL. tRPC is the only API surface.]

---

## 5. Test-Driven Development

TDD is mandatory. This is not a style preference.

### Task types

Every deliverable is split into two tasks that form an inseparable pair:

- **[TEST] task** — writes failing test(s) only. No implementation code.
- **[IMPL] task** — writes implementation only, to pass the failing tests. No new tests.

[TEST] tasks are executed in the test phase (`/ch-3-test`).
[IMPL] tasks are executed in the implement phase (`/speckit-implement`).

The test phase must pass its gate before implementation begins (§19).

A test agent that writes implementation code has violated this constitution.
An implementation agent that writes new test files or adds/modifies test cases (`it()` / `test()` / `describe()` blocks) has violated this constitution. Updating test setup — mock definitions, fixture data, helper utilities, `setup.ts` files, `vi.mock(...)` calls — to support the implementation is permitted when the required setup was not established during the test phase.

### Coverage requirements

| Artifact | Required tests |
|---|---|
| [PROJECT: e.g. API procedure/endpoint] | [PROJECT: e.g. Vitest integration test]: happy path + at least one error/edge case |
| [PROJECT: e.g. UI component] | [PROJECT: e.g. Playwright or Vitest component test]: primary interaction |
| [PROJECT: e.g. Service/utility function] | [PROJECT: e.g. Vitest unit test]: happy path + edge cases |
| Schema migration | No test required, but migration must be reviewed by human before merge |

A task is not closed until all required tests exist, were written before the implementation, and pass in CI.

### Test file location

<!-- List every directory where tests live, with a backtick-quoted path per
     line — the harness parses this to know where to look for test files.
     A single-package project only needs one line. -->

- [PROJECT: e.g. Backend tests]: `[PROJECT: e.g. backend/tests/]` mirroring the `src/` structure.
- [PROJECT: e.g. Frontend tests]: `[PROJECT: e.g. frontend/tests/]` mirroring the `src/` structure.
- Test files are committed on the same branch as the feature code.

---

## 6. Task Atomicity

- One [TEST]/[IMPL] pair = one of: a single schema change, a single API procedure/endpoint, or a single UI component. The pair is the unit of atomicity — both tasks together cover one deliverable.
- A deliverable that touches both backend and frontend is two [TEST]/[IMPL] pairs (four tasks).
- Tasks that violate atomicity are rejected before implementation begins.
- Parallel tasks are marked `[P]` in `tasks.md` and may be implemented concurrently only when they have no shared schema or contract dependency.

---

## 7. Spec Gate

No stage proceeds without the prior artifact passing human review.

```
spec.md       → human review → approved
plan.md       → human review → approved
tasks.md      → human review → approved
implementation → CI pass + human review → merged
```

- `plan.md` is not started until `spec.md` is approved.
- `tasks.md` is not started until `plan.md` is approved.
- Code is not written until a specific task in an approved `tasks.md` is assigned to the implementation agent.
- The implementation agent acts on a task entry plus its referenced spec artifacts only. It does not traverse the full repo to infer intent.

---

## 8. Refactor Cadence

- Every 5 merged tasks: one dedicated refactor session before the next feature begins.
- Refactor mandate: drift correction and structural cleanup only. No new behaviour.
- Refactor sessions are scheduled, not reactive. Do not wait for failure to trigger them.
- A refactor session must not change any API contract or data schema without going through the full spec gate.
- Run `/speckit.checklist` after every merge to surface drift before it accumulates.
- **Refactor sessions use a branch and PR, identical to feature work.** All CI checks must pass locally before pushing (§12), and the PR requires human review before merge. No changes of any kind may be committed directly to `main`.

---

## 9. Architecture Document

`.specify/memory/architecture.md` is the long-term architectural north star. It is distinct from the constitution (which is rules), from `plan.md` (which is per-feature), and from `product-context.md` (which is product vision).

### What it contains

- The target system shape at a component level — how frontend, backend, database, and external services relate
- Active architectural constraints and the rationale behind each
- Decisions already made that all future features must respect
- Known future integration points and how the current design accommodates them

### How it is maintained

- The Plan Agent reads `architecture.md` before producing `plan.md` for any feature.
- If the feature plan is consistent with `architecture.md`, the Plan Agent states this explicitly in `plan.md`.
- If the feature plan conflicts with `architecture.md`, the Plan Agent surfaces the conflict for human resolution before `plan.md` is finalised. No conflict is silently resolved by the agent.
- If the feature introduces a new architectural decision, the Plan Agent proposes an addition to `architecture.md` as part of the plan phase. The human approves or amends the proposed addition before `tasks.md` is started.
- The Refactor Agent reads `architecture.md` during every refactor session and reports any drift between the stated architecture and the actual codebase. It does not self-correct architectural drift — it reports it for human decision.

### Initialisation

`architecture.md` is created before the first feature spec is written. It documents the initial system design decisions made at project setup. Amendments follow the same PR + human approval process as all other changes to `main` (§11).

### Quality principles

`.specify/memory/architecture-principles.md` is the authoritative source for the architectural quality bar — fail conditions, severity rules, core principles, and heuristics. It is read by both the Plan Agent (so plans are written to meet the bar) and the Architecture Review Agent (so plans are evaluated against the same bar). Amendments follow the same PR + human approval process as all other `main` changes (§11).

---

## 10. Feedback Intake

- Runtime observations, bugs, and UX issues do not produce direct code patches.
- Every signal re-enters as either a new `spec.md` or a `constitution.md` amendment, reviewed by the human.
- No implementation agent self-directs based on observed runtime behaviour.

---

## 11. Git Branching Model

```
main
└── 001-[feature-name]    ← branch + specs/001-[feature-name]/
└── 002-[feature-name]    ← branch + specs/002-[feature-name]/
```

- One branch per feature, named to match its spec folder.
- Branches are squash-merged to main after human review and CI pass.
- The `specs/[###-feature]/` folder is committed on the feature branch and merges with the code.
- `constitution.md` and `architecture.md` live on `main`. Amendments are made via a dedicated short-lived branch and merged to `main` via PR with human approval. **No file may ever be committed directly to `main`.**

**No agent may merge any branch into `main` without explicit human approval.** This rule has no exceptions — not for hotfixes, not after CI passes, not at the end of a `/speckit-implement` run. The merge is always a human action.

---

## 12. CI Requirements

<!-- The harness parses these bullets to find the actual command for each
     check — keep each one as "<Label> (`<command>`)" with the real,
     runnable command in backticks, not just a tool name. Delete any row
     that doesn't apply to this project (e.g. no e2e suite). -->

Every push to a feature branch must pass:

- Typecheck (`[PROJECT: e.g. pnpm typecheck]`)
- Lint (`[PROJECT: e.g. pnpm lint]`)
- Unit tests (`[PROJECT: e.g. pnpm test:unit]`)
- E2E tests (`[PROJECT: e.g. pnpm test:e2e]`)

**The implementation agent must run all listed checks locally and confirm they pass before pushing to a feature branch.** Pushing and waiting for CI to catch failures is not acceptable. If a check cannot be run locally, the agent must state this explicitly and explain why before pushing.

**If any code changes are made after the checks were last run, all four checks must be re-run before pushing.**

---

## 13. v1 Scope Boundaries

<!-- List what is explicitly out of scope for v1. A spec that crosses these
     boundaries requires a constitution amendment before proceeding. -->

These are constitutionally out of scope for v1:

- [PROJECT: Out-of-scope item 1]
- [PROJECT: Out-of-scope item 2]
- [PROJECT: Out-of-scope item 3]

---

## 14. Status Pipeline

<!-- If your project has a status/state machine, define it here.
     Remove this section if not applicable. -->

```
[PROJECT: State 1] → [PROJECT: State 2] → [PROJECT: State 3]
```

- This pipeline is fixed in v1. No agent may add, rename, or reorder statuses without a constitution amendment.

---

## 15. Architecture Must Not Foreclose Future Capabilities

<!-- List planned future capabilities that are out of scope now but must
     remain architecturally possible. -->

The following are out of scope for v1 but the architecture must not make them impossible:

- [PROJECT: Future capability 1]
- [PROJECT: Future capability 2]

---

## 16. Agent Role Boundaries

| Agent | Permitted actions |
|---|---|
| Specify Agent | Produce `spec.md` from constitution + product context + human intent. No plan or code. |
| Plan Agent | Read `architecture.md` and `architecture-principles.md`, produce `plan.md`, `research.md`, `data-model.md`, `contracts/`, and proposed `architecture.md` additions from approved `spec.md`. No code. |
| Tasks Agent | Produce `tasks.md` from approved `plan.md` and supporting docs. No code. |
| Test Agent | Read `test-principles.md`. Write failing test(s) for assigned [TEST] tasks. Confirm red state. Record failing output. No implementation code. No modification of the source directories declared in §5. |
| Implementation Agent | Read `code-quality-principles.md`. Load red-output artifact from paired [TEST] task. Write implementation to pass failing tests. Refactor under green. No new test files. No adding or modifying test cases. Modifying test setup (mocks, fixtures, helpers, `setup.ts`) is permitted when the implementation requires additional test infrastructure not established during the test phase. No spec, plan, or architecture changes. |
| Verification Agent | Report pass/fail and defects against spec. No fixes. |
| Refactor Agent | Structural cleanup and drift correction only. Read `architecture.md` and report drift. No new behaviour, no contract changes, no self-directed architectural corrections. |

An agent operating outside its permitted actions has violated this constitution.

---

## 17. Decision Records

Breaking API changes and constitution amendments are logged below in reverse chronological order.

| Date | Type | Summary |
|---|---|---|
| [DATE] | Initial setup | Project constitution created. |

---

## 18. Bug Resolution Protocol

When an agent encounters a bug — whether reported by the human, observed at runtime, or surfaced by a failing test — it MUST follow this sequence:

1. **Diagnose** — identify the root cause. State it clearly: which component, which assumption failed, what the actual vs. expected behaviour is.
2. **Confirm** — present the diagnosis to the human and wait for explicit confirmation before writing any fix. Do not speculatively apply a patch.
3. **Fix** — implement the minimum change that addresses the confirmed root cause. Do not broaden the fix scope beyond what the diagnosis supports.

**An agent that proposes or applies a fix before stating and confirming a diagnosis has violated this constitution.**

This rule applies to all agents and all bug types: runtime defects, test failures, CI failures, and regressions. It does not apply to trivially obvious typos or compile errors where cause and fix are identical.

---

## 19. Test Gate

An [IMPL] task is BLOCKED until ALL of the following are true for its paired [TEST] task:

1. The [TEST] task is marked `[x]` in `tasks.md`.
2. A red-output artifact exists at `specs/$FEATURE/test-results/<TASKID>-red.txt`, containing a meaningful assertion or "not found" failure — not a compile/syntax error in the test file itself.
3. `specs/$FEATURE/ch-3-test-critic-result-*.json` exists with `"status": "PASS"`.

The quality bar for test files is defined in `.specify/memory/test-principles.md`. This is the same relationship that `code-quality-principles.md` has to the code-quality gate.

An implementation agent that processes an [IMPL] task without all three conditions being met has violated this constitution.
