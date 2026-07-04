# Plan: Health Endpoint

## Summary

Add a GET /health route to the Hono application that satisfies FR-001, FR-002, and FR-003 from spec.md: return HTTP 200 with body `{"status": "ok"}`, Content-Type `application/json`, and a 2xx success status.

## Technical Context

- Language: TypeScript (Node 22)
- Framework: Hono (as mandated by constitution §2)
- Testing: Vitest with @hono/testing for in-process HTTP requests
- No database interaction required (spec Assumptions)
- Route lives at `/health` (spec Assumptions)

## Constitution Check

- Stack: Hono is the mandated backend framework. No prohibited frameworks (Express, Fastify) introduced.
- TDD: [TEST] tasks will write failing tests before [IMPL] tasks.
- No migration or data model changes required.

## Phase 0 — Research

No external dependencies needed. The Hono app entry point is `backend/src/index.ts`. Routes are registered in `backend/src/api/`.

## Phase 1 — Design

### Traceability

| Spec requirement | Plan element |
|---|---|
| FR-001: 200 with `{"status":"ok"}` | Route handler returns `c.json({ status: 'ok' })` |
| FR-002: Content-Type application/json | Hono's `c.json()` sets this automatically |
| SC-001 | Covered by test asserting status 200 + body |
| SC-002 | Covered by test asserting Content-Type |
| SC-003 | Covered by test asserting res.ok is true |
| US1 acceptance scenario | Fully covered by the tests above |

## Project Structure

```
backend/
  src/
    api/
      health.ts      [NEW]
    index.ts         [MODIFIED — register route]
  tests/
    routes/
      health.test.ts [NEW]
```
