import { Hono } from 'hono'
import healthRoute from './api/health'

const app = new Hono()
app.route('/', healthRoute)

export { app }
