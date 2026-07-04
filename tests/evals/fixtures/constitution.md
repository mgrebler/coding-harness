# Constitution

**Version**: v1.0.0

**Supreme law for all agents on this project. Read this before acting. No rule here is discretionary.**

---

## 1. Project Identity

**What this project is:** A minimal REST API for a task management service, built with TypeScript and Hono on Node.

**Decision-relevant constraints:**
- Single-tenant, no multi-user isolation required
- All responses must be JSON
- No database in v1; in-memory storage only

---

## 2. Stack Constraints

No agent may introduce a dependency outside this list without a constitution amendment.

| Layer | Mandated tool |
|---|---|
| Language | TypeScript |
| Runtime | Node 22 |
| Backend framework | Hono |
| Testing | Vitest |
| Package manager | pnpm |

**Prohibited substitutions:** Express, Fastify, Koa, Jest, Mocha, Jasmine. Any framework not listed above is prohibited.

---

## 3. API Contract Rules

- All routes defined in `backend/src/api/`
- All routes must return `Content-Type: application/json`
- Error responses use `{ "error": "<message>" }` shape
- Success responses use documented shapes from contracts/

---

## 4. TDD Policy

All features follow RED → GREEN → REFACTOR.
- [TEST] tasks write failing tests first (RED state): tests must fail before any implementation begins
- [IMPL] tasks make tests pass (GREEN state) and include any refactoring (REFACTOR phase); no separate [REFACTOR] task is required
- No implementation code may be written during [TEST] tasks
- Correct task order: all [TEST] tasks for a story complete before any [IMPL] tasks for that story begin

---

## 5. Governance

No agent merges to main. Human PR review is required for all changes.
