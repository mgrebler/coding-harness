# Spec: Health Endpoint

**Feature branch:** 001-health-endpoint

## User Scenarios

### US1 — Operator checks service health [P1]
**As an** operator,
**I want** a GET /health endpoint,
**So that** I can confirm the service is running.

**Acceptance scenario:**
- Given the service is running
- When I send GET /health
- Then I receive HTTP 200
- And the response body is `{"status": "ok"}`
- And the Content-Type header is `application/json`

## Requirements

### Functional Requirements

**FR-001**: GET /health returns HTTP 200 with body `{"status": "ok"}`.
**FR-002**: The response Content-Type header must be `application/json`.
**FR-003**: The endpoint must not require authentication.

## Success Criteria

**SC-001**: GET /health returns 200 with `{"status": "ok"}` body.
**SC-002**: Response Content-Type is `application/json`.
**SC-003**: No auth header required; unauthenticated requests succeed.

## Assumptions

- No database interaction needed for health check
- Endpoint lives at exactly `/health` (not `/api/health`)
