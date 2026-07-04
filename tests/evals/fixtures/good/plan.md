# Plan: Health Endpoint

## Summary

Add a GET /health route to the Hono application that satisfies FR-001, FR-002, and FR-003 from spec.md: return HTTP 200 with body `{"status": "ok"}` and Content-Type `application/json`, with no authentication required.

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

### Route implementation

Create `backend/src/api/health.ts` exporting a Hono `route` that handles `GET /health`:

```typescript
import { Hono } from 'hono'
const route = new Hono()
route.get('/health', (c) => c.json({ status: 'ok' }))
export default route
```

Register the route in `backend/src/index.ts`:

```typescript
app.route('/', healthRoute)  // healthRoute defines GET /health, so final URL is /health
```

### Test implementation

Create `backend/tests/routes/health.test.ts` using `@hono/testing`:

```typescript
import { testClient } from '@hono/testing'
import { describe, it, expect } from 'vitest'
import { app } from '../src/index'

describe('GET /health', () => {
  it('returns 200 with { status: ok }', async () => {
    const res = await testClient(app).get('/health')
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ status: 'ok' })
  })
})
```

### Traceability

| Spec requirement | Plan element |
|---|---|
| FR-001: 200 with `{"status":"ok"}` | Route handler returns `c.json({ status: 'ok' })` |
| FR-002: Content-Type application/json | Hono's `c.json()` sets this automatically |
| FR-003: No auth required | No auth middleware added to `/health` |
| SC-001 | Covered by test asserting status 200 + body |
| SC-002 | Covered by test asserting Content-Type |
| SC-003 | No auth token in test requests |
| US1 acceptance scenario | Fully covered by the test above |

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
