# Tasks: Health Endpoint

## Phase 2 — Implementation

[T001] [TEST] Write failing test for GET /health
  - Create tests/routes/health.test.ts
  - Assert HTTP 200 and body { status: 'ok' }
  - Confirm red state

[T002] [IMPL] Implement GET /health route
  - Create src/routes/health.ts
  - Register in src/index.ts
  - depends on T001

## Checkpoint

Run `pnpm test` — all tests pass.
