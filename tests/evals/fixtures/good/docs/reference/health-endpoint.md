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

Use this endpoint to confirm the service is up.
