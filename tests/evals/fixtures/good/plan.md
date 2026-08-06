# Plan: Health Endpoint

## Summary

Add a GET /health route to the Hono application that satisfies FR-001, FR-002, and FR-003 from spec.md: return HTTP 200 with body `{"status": "ok"}`, Content-Type `application/json`, and a 2xx success status.

## Technical Context

- Language: TypeScript (Node 22)
- Framework: Hono (as mandated by constitution §2)
- Testing: Vitest with `@hono/testing` for in-process HTTP requests (both mandated by constitution §2)
- No database interaction required (spec Assumptions)
- Route lives at `/health` (spec Assumptions)
- No service layer: the handler returns a fixed literal (`{ status: 'ok' }`) with no computation, branching, or storage access — there is no business logic to separate out, so Architecture §2 Backend Layer Separation is satisfied trivially rather than via delegation to a service.

## Constitution Check

| Section | Verdict | Justification |
|---|---|---|
| §1 Project Identity | ✅ | Single-tenant JSON REST API on TypeScript/Hono/Node — matches exactly, no deviation. |
| §2 Stack Constraints | ✅ | Only mandated tools used (see Stack Constraint Check below); no prohibited frameworks introduced. |
| §3 API Contract Rules | ✅ | Route lives in `backend/src/api/`, returns `application/json` via `c.json()`, success shape matches contracts/. |
| §4 TDD Policy | ✅ | [TEST] tasks will write failing tests before any [IMPL] tasks begin (RED→GREEN); no implementation code during [TEST] tasks. |
| §5 Governance | ✅ | No agent merges to main; this plan is submitted for human PR review per standard process. |

### Stack Constraint Check

| Dependency | In Constitution §2? | Notes |
|---|---|---|
| TypeScript | ✅ Yes | Mandated language. |
| Node 22 | ✅ Yes | Mandated runtime. |
| Hono | ✅ Yes | Mandated backend framework. |
| Vitest | ✅ Yes | Mandated testing tool. |
| @hono/testing | ✅ Yes | Mandated integration test HTTP client. |

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
