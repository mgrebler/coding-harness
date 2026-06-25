# Architecture Principles

**Authoritative reference for all architecture-quality decisions in this project.**

Read by the Plan Agent when generating `plan.md`, and by the Architecture Review Agent when evaluating it. Both agents use the same source — the plan aims for the same bar it is reviewed against.

---

## Automatic FAIL Conditions

Immediately FAIL a plan if any of the following exist.

### Architecture Violations
- Unclear ownership boundaries
- Shared mutable state across domains
- Bidirectional dependencies
- Infrastructure containing business logic
- Direct database access across service boundaries
- Undefined source of truth
- Framework-driven architecture without domain modeling

### Operational Risks
- Undefined failure handling
- Retries without idempotency strategy
- Async workflows without observability
- Missing rollback or migration strategy
- Hidden background processing
- Undefined deployment strategy

### Maintainability Risks
- Speculative abstractions
- Unnecessary distributed systems
- Excessive coupling
- Unclear module responsibilities
- Abstractions without demonstrated need
- Significant cognitive overload

### Data Integrity Risks
- Undefined consistency model
- Race condition exposure
- Undefined transactional boundaries
- Duplicated ownership of state

### Scalability Misdirection
- Microservices without demonstrated scaling need
- Event-driven architecture without operational justification
- Premature optimization increasing complexity

---

## Severity Rules

FAIL if:
- Any Critical issue exists
- More than 2 High severity issues exist
- Architecture confidence score is below 7/10

PASS only if:
- No Critical issues exist
- High severity issues are limited and mitigated
- Ownership boundaries are clear
- Operational behavior is explicit
- Dependency direction is coherent
- Architecture remains understandable by a small engineering team

---

## Core Principles

### 1. Simplicity First

Prefer:
- Fewer moving parts
- Synchronous workflows
- Explicit logic
- Direct dependencies
- Boring technology
- Operational clarity

Reject:
- Speculative abstractions
- Unnecessary indirection
- Accidental distributed systems
- Premature optimization
- Over-engineering

Complexity requires explicit justification.

---

### 2. Ownership Boundaries

Every module, service, or component must have:
- One clear responsibility
- One owning domain
- One authoritative source of truth

Flag:
- Mixed responsibilities
- Hidden coupling
- Circular dependencies
- Cross-domain persistence access
- Shared ownership

---

### 3. Dependency Discipline

Dependencies should:
- Point inward toward business logic
- Isolate infrastructure concerns
- Preserve replaceability
- Minimize runtime coupling

Flag:
- Infrastructure owning business rules
- Framework-centric architecture
- UI/business/data layer leakage
- Bidirectional dependencies
- Shared mutable state

---

### 4. Operational Sustainability

All systems must be operable. Review:
- Deployment complexity
- Observability, logging, tracing
- Retries and rollback safety
- Migration strategy
- Recovery procedures and failure handling

Flag:
- Undefined operational behavior
- Distributed coordination without safeguards
- Hidden asynchronous workflows
- Eventual consistency without justification
- Retries that can duplicate side effects

---

### 5. Scalability Realism

Scalability concerns must be evidence-based.

Reject:
- Microservices without scaling pressure
- Event-driven systems without operational maturity
- Caching without invalidation strategy
- Abstractions for hypothetical future requirements

Prefer:
- Modular monoliths
- Incremental extraction
- Measured bottleneck resolution

---

### 6. Maintainability

Assess:
- Cognitive load
- Readability and debugging complexity
- Onboarding difficulty
- Testability and change isolation
- Architectural consistency

Flag:
- Deep inheritance
- Excessive indirection
- Unnecessary interfaces
- Hidden side effects
- Temporal coupling
- Over-generic abstractions

---

### 7. Data and State Integrity

Review:
- Transactional boundaries
- State ownership and concurrency assumptions
- Synchronization strategy and idempotency
- Consistency models

Flag:
- Multiple sources of truth
- Implicit synchronization
- Race condition risks
- Undefined conflict resolution
- Persistence leakage into APIs

---

### 8. API and Contract Design

Assess:
- Explicitness and versionability
- Backward compatibility
- Boundary clarity and schema stability

Flag:
- Persistence models exposed externally
- Implicit behavior
- Tightly coupled integrations
- Weak contracts

---

## Architecture Heuristics

Automatically flag:

- Services owning unrelated domains
- Direct database access across boundaries
- Async messaging without operational reasoning
- Abstractions with only one implementation
- Event-driven workflows for simple processes
- Caching without invalidation strategy
- Retries without idempotency
- Shared libraries containing business logic
- Controllers containing orchestration logic
- Infrastructure containing domain logic
- Distributed transactions without safeguards
- Orchestration complexity exceeding business need
- APIs coupled directly to persistence models
- Excessive configuration or runtime dependencies
- Unclear service ownership
- Large deployment blast radius
- Hidden background processing
- Unbounded queues or workloads
- Missing observability strategy
- Framework-driven folder structures without domain modeling
