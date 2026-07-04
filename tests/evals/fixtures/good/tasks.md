# Tasks: Health Endpoint

## Phase 1 — Design: User Story US1 — Operator checks service health

[T001] [TEST] [P1] [US1] Write failing test for GET /health — RED state required before T002 begins
  - Create backend/tests/routes/health.test.ts
  - Test 1: Assert HTTP status 200 and response body equals { status: 'ok' } (covers SC-001)
  - Test 2: Assert Content-Type is application/json (covers SC-002)
  - Test 3: Assert endpoint does not require authentication — expect status not 401, not 403 (covers SC-003)
  - Run tests: tests 1 and 2 MUST fail (red state); test 3 may pass since 404≠401 — T002 must not begin until T001 fails

[T002] [IMPL] [P1] [US1] Implement GET /health route — GREEN then REFACTOR within this task
  - T001 must be in red state before this task begins (TDD prerequisite)
  - Create backend/src/api/health.ts with Hono route handler (GET /health returns 200 {"status":"ok"})
  - Register route in backend/src/index.ts
  - Run T001 tests: they must now pass (green state)
  - Refactor route handler if needed while keeping all tests green (refactor phase)
  - depends on T001

## Checkpoint

After T002: run `pnpm test` — all tests must pass.
