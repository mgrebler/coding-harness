# Plan: Health Endpoint

## Summary

Add a GET /health capability that satisfies FR-001, FR-002, and FR-003 from spec.md by
introducing a dedicated `health-service` microservice. The main API publishes a
`HealthCheckRequested` event to a message queue; `health-service` consumes it
asynchronously, computes the health status, and writes the result to a shared Redis
cache that the main API polls to build the HTTP response.

## Technical Context

- Language: TypeScript (Node 22)
- Framework: Hono (as mandated by constitution §2) for the main API
- `health-service` is a separate deployable unit with its own message queue consumer
- No database interaction required for health status itself, but `health-service`
  writes directly to the shared Redis cache also used by three unrelated services
  for their own caching needs
- Retries: if the queue delivery fails, the main API retries publishing the event
  up to 5 times with no idempotency key, so a slow consumer can process the same
  request more than once

## Constitution Check

- Stack: Hono is the mandated backend framework for the main API. No prohibited
  frameworks (Express, Fastify) introduced.
- TDD: [TEST] tasks will write failing tests before [IMPL] tasks.
- No migration or data model changes required.

## Phase 0 — Research

The Hono app entry point is `backend/src/index.ts`. `health-service` is a new
standalone Node process with its own deployment pipeline, queue consumer, and
Redis client, introduced solely to serve a single static health check response.

## Phase 1 — Design

### Traceability

| Spec requirement | Plan element |
|---|---|
| FR-001: 200 with `{"status":"ok"}` | Main API polls Redis cache written by `health-service` |
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
      health.ts          [NEW — polls shared Redis cache]
    index.ts              [MODIFIED — register route, publish event]
  tests/
    routes/
      health.test.ts      [NEW]
health-service/
  src/
    consumer.ts            [NEW — consumes HealthCheckRequested, writes shared Redis cache]
```
