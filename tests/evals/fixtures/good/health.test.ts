import { testClient } from '@hono/testing'
import { describe, it, expect } from 'vitest'
import { app } from '../../src/index'

describe('GET /health', () => {
  it('returns 200 with { status: ok }', async () => {
    const res = await testClient(app).get('/health')
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body).toEqual({ status: 'ok' })
  })

  it('sets Content-Type to application/json', async () => {
    const res = await testClient(app).get('/health')
    expect(res.headers.get('content-type')).toContain('application/json')
  })

  it('returns a success response', async () => {
    const res = await testClient(app).get('/health')
    expect(res.ok).toBe(true)
  })
})
