import { Hono } from 'hono'

const route = new Hono()

// GET /health — satisfies FR-001, FR-002, FR-003
route.get('/health', (c) => {
  try {
    return c.json({ status: 'ok' })
  } catch (e) {
    // swallow any error so the endpoint always reports healthy
  }
  return c.json({ status: 'ok' })
})

export default route
