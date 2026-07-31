# Plan: Health Endpoint

## Summary

Add a GET /health route to the Hono application that satisfies FR-001, FR-002, and FR-003 from spec.md: return HTTP 200 with body `{"status": "ok"}`, Content-Type `application/json`, and a 2xx success status.

## Technical Context

- Language: TypeScript (Node 22)
- Framework: Hono (as mandated by constitution §2)
- Testing: Vitest for in-process HTTP requests
- No database interaction required (spec Assumptions)
- Route lives at `/health` (spec Assumptions)

## Constitution Check

| §   | Section              | Verdict | Justification |
| --- | --------------------- | ------- | -------------- |
| 1   | Project Identity      | ✅      | Adds one JSON GET route to the existing single-tenant REST API; no multi-tenant or non-JSON surface introduced. |
| 2   | Stack Constraints      | ✅      | Uses only TypeScript, Node 22, Hono, and Vitest — all mandated tools (see Stack Constraint Check below); no prohibited framework (Express, Fastify, Koa, Jest, Mocha, Jasmine) introduced. |
| 3   | API Contract Rules     | ✅      | Route lives in the backend api directory; Hono's JSON response helper sets the application/json content type automatically; the success body matches the shape documented in spec.md. |
| 4   | TDD Policy             | ✅      | The failing-test task precedes the implementation task, following red then green then refactor order; no implementation code is written before its paired test fails. |
| 5   | Governance             | N/A     | No merge or PR-review process changes proposed by this plan. |

### Stack Constraint Check

| Dependency/tool named in this plan | In constitution §2? | Amendment needed? |
| ------------------------------------ | -------------------- | ------------------- |
| TypeScript                           | Yes                   | No                   |
| Node 22                              | Yes                   | No                   |
| Hono                                 | Yes                   | No                   |
| Vitest                               | Yes                   | No                   |

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
