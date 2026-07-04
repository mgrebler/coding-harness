# Architecture

## 1. Project Structure

```
backend/
  src/
    api/       — Hono route handlers, one file per domain
    middleware/ — Request/response middleware
    types.ts   — Shared TypeScript types
    index.ts   — Application entry point
  tests/
    routes/    — Integration tests for each route
    unit/      — Unit tests for pure functions
```

## 2. Backend Layer Separation

- Routes handle HTTP concerns only (parse request, call service, return response)
- No business logic in route handlers
- No direct storage access from routes

## 3. Testing Conventions

- Integration tests use `@hono/testing` to make in-process HTTP requests
- No real network calls in tests
- Test files mirror the src structure: `backend/src/api/health.ts` → `backend/tests/routes/health.test.ts`
