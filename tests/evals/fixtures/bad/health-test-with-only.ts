import { testClient } from '@hono/testing'
import { describe, it, expect } from 'vitest'
import { app } from '../../src/index'

// Uses it.only — would prevent other tests from running in CI
describe('GET /health', () => {
  it.only('returns 200', async () => {
    const res = await testClient(app).get('/health')
    expect(res.status).toBe(200)
    // No body assertion — SC-001 untested
    // No Content-Type assertion — SC-002 untested
    // No no-auth assertion — SC-003 untested
  })
})
