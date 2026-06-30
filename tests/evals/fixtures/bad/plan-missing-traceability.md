# Plan: Health Endpoint

## Summary

We need to add some kind of status endpoint so operators know the service is up.

## Technical Context

We'll use Node and our existing framework. A simple route should suffice.

## Design

Add a route that responds with a success message. We can put it somewhere in the routes directory. The response should indicate the service is healthy.

Something like:

```
GET /status -> 200 { "message": "running" }
```

## Files to modify

- Add a new route file
- Register it in the main app
