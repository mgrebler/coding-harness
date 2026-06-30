# Tasks: Health Endpoint

## Phase 1 — Setup

No setup tasks required; project structure already exists.

## Phase 2 — User Story US1: Operator checks service health

[T001] [TEST] [P1] [US1] Write failing test for GET /health returning 200 with {"status":"ok"}
  - Create tests/routes/health.test.ts
  - Assert HTTP status 200
  - Assert response body equals { status: 'ok' }
  - Assert Content-Type is application/json
  - Confirm test fails (red state) before implementation

[T002] [IMPL] [P1] [US1] Implement GET /health route
  - Create src/routes/health.ts with Hono route handler
  - Register route in src/index.ts
  - Verify T001 tests now pass (green state)
  - depends on T001

## Checkpoint

After T002: run `pnpm test` — all tests must pass.
