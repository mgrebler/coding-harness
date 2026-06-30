import { Hono } from 'hono'

const route = new Hono()

// Returns wrong path and wrong response shape — violates FR-001 (/health not /status)
// and returns { message: 'running' } instead of { status: 'ok' }
route.get('/status', (c) => c.text('running'))

export default route
