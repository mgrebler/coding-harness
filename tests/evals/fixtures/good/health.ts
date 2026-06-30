import { Hono } from 'hono'

const route = new Hono()

// GET /health — satisfies FR-001, FR-002, FR-003
route.get('/health', (c) => c.json({ status: 'ok' }))

export default route
