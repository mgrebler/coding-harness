# Architecture

<!-- This document is the long-term architectural north star for this project.
     It is distinct from:
       - constitution.md  (rules for agents)
       - plan.md          (per-feature implementation plan)
       - product-context.md (product vision and user needs)

     Fill in each section before writing the first spec.
     The Plan Agent reads this document before producing any plan.md.
     Amendments follow the same PR + human approval process as all other changes. -->

## System Overview

<!-- Describe the high-level system topology: what components exist, how they
     communicate, and where data flows. A diagram or bulleted list works well. -->

[PROJECT: Describe your system at the component level.]

```
[PROJECT: e.g.
Browser → Frontend (React/Vite) → Backend (Hono/Node) → Database (PostgreSQL)
                                 ↘ External APIs
]
```

---

## Internal Structure

### Backend

<!-- Describe the layering model: e.g. router → service → data access.
     Name the directories and what lives in each. -->

[PROJECT: Describe backend layers.]

### Frontend

<!-- Describe the component/page/hook structure. -->

[PROJECT: Describe frontend structure.]

---

## Active Architectural Constraints

<!-- List decisions that all future features must respect, with rationale. -->

| Constraint | Rationale |
|---|---|
| [PROJECT: Constraint] | [PROJECT: Why] |

---

## Known Future Integration Points

<!-- Describe future capabilities that are out of scope now but must remain
     architecturally possible (mirrors §15 of constitution.md). Explain how
     the current design keeps those doors open. -->

- [PROJECT: Future capability] — [How current design accommodates it]

---

## Architectural Decision Log

<!-- Record significant architectural decisions in reverse chronological order.
     This supplements the constitution's decision records with more technical detail. -->

| Date | Decision | Rationale |
|---|---|---|
| [DATE] | Initial architecture established | Project setup |
