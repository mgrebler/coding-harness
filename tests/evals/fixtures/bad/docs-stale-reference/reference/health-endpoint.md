# Health Endpoint

`GET /status` reports whether the service is running.

## Request

```
GET /status
```

## Response

- Status: `200 OK`
- Content-Type: `application/json`
- Body: `{"result": "healthy"}`

Use this endpoint to confirm the service is up.
