# Plan: Health Endpoint

## Summary

Add a GET /health route that returns 200 with `{"status": "ok"}` to satisfy FR-001, FR-002, and FR-003.

## Technical Context

- Language: TypeScript (Node 22)
- Framework: Express (chosen for its simplicity and widespread familiarity)
- Testing: Jest

## Constitution Check

Express is familiar and well-documented. We'll use it here for simplicity.

## Design

Install Express and create a route:

```typescript
import express from 'express'
const app = express()
app.get('/health', (req, res) => {
  res.json({ status: 'ok' })
})
```

Test with Jest:

```typescript
import request from 'supertest'
import { app } from '../src/index'

test('GET /health returns 200', async () => {
  const res = await request(app).get('/health')
  expect(res.status).toBe(200)
  expect(res.body).toEqual({ status: 'ok' })
})
```

## Traceability

| Spec requirement | Plan element |
|---|---|
| FR-001: 200 with `{"status":"ok"}` | Express route returns `res.json({ status: 'ok' })` |
| FR-002: Content-Type | Express sets automatically |
| FR-003: No auth | No auth middleware |
