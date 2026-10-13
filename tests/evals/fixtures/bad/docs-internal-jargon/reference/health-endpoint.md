# Health Endpoint

`GET /health` reports whether the service is running.

## Request

```
GET /health
```

## Response

- Status: `200 OK`
- Content-Type: `application/json`
- Body: `{"status": "ok"}`

Implemented per task T004 in specs/001-health-endpoint/tasks.md — see the paired T003 test
task for the red-state assertion this satisfies. Use this endpoint to confirm the service is up.
