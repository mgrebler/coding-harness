# Code Quality Principles

**Authoritative reference for all code-quality decisions in this project.**

Read by the Implementation Agent when writing code, and by the Code Quality Review Agent when evaluating it. Both agents use the same source — the implementation aims for the same bar it is reviewed against.

---

## Automatic FAIL Conditions

Immediately FAIL a review if any of the following exist.

### Readability and Maintainability Failures
- Unreadable or excessively clever code
- Deeply nested logic
- Excessively large functions or classes
- Unclear naming
- Hidden side effects or temporal coupling
- Unnecessary indirection
- Speculative abstraction without demonstrated need
- Significant cognitive overload

### Boundary and Layering Violations
- Business logic inside controllers, routes, or UI
- Infrastructure concerns leaking into domain logic
- Bypassing architectural boundaries
- Duplicated business rules across layers
- Direct persistence access outside intended boundaries
- Hidden coupling between modules

### Testing Failures
- Missing tests for critical behavior
- Brittle or implementation-coupled tests
- Snapshot abuse
- Untestable logic
- Excessive mocking hiding behavior
- Missing edge-case coverage
- Flaky or nondeterministic tests

### Failure Handling Risks
- Swallowed exceptions
- Broad catch blocks hiding failures
- Unsafe retry behavior
- Missing cleanup logic
- Silent degradation or unsafe default behavior

### Data Integrity Risks
- Race condition exposure
- Unsafe shared mutable state
- Partial state updates
- Duplicated sources of truth
- Inconsistent validation
- Undefined concurrency behavior

### Operational Risks
- Missing timeouts
- Unbounded retries
- Missing instrumentation
- Blocking operations in async flows
- Sensitive data leakage in logs
- Uncontrolled resource usage

### Security Risks
- Unsanitized external input
- SQL or shell injection exposure
- Unsafe deserialization
- Secrets committed in code
- Authorization bypass opportunities

---

## Severity Rules

FAIL if:
- Any Critical issue exists
- More than 3 High severity issues exist
- Code quality confidence score is below 7/10

PASS only if:
- Code is understandable by a competent engineer unfamiliar with the feature
- Logic is locally understandable
- Behavior is testable
- Operational behavior is explicit
- Boundaries remain coherent
- Complexity is justified

---

## Core Principles

### 1. Clarity Over Cleverness

Prefer:
- Explicit logic and readable control flow
- Obvious behavior and simple constructs
- Local reasoning

Reject:
- Clever abstractions and meta-programming
- Unnecessary genericity
- Dense functional chains
- Magical or hidden control flow

Code should be understandable without mental gymnastics.

---

### 2. Simplicity First

Prefer:
- Small, focused functions
- Direct implementations
- Explicit dependencies
- Minimal moving parts

Reject:
- Speculative extensibility
- Premature optimization
- Abstraction for hypothetical reuse
- Unnecessary inheritance

Complexity requires justification.

---

### 3. Boundary Discipline

Ensure:
- Business rules remain in domain logic
- Infrastructure concerns remain isolated
- UI and controllers stay thin
- Persistence logic remains contained

Flag:
- Leaking abstractions
- Mixed responsibilities
- Cross-layer coupling
- Duplicated orchestration

---

### 4. Maintainability

Assess:
- Readability and debugging difficulty
- Onboarding complexity
- Change amplification and cognitive load
- Consistency and discoverability

Flag:
- Shotgun surgery risks
- Hidden dependencies
- Brittle structures
- Inconsistent implementation patterns

---

### 5. Testability

Code should:
- Be deterministic where possible
- Isolate side effects
- Support focused testing
- Expose behavior clearly

Flag:
- Hidden global state
- Hard-coded dependencies
- Excessive setup complexity
- Implementation-coupled tests
- Missing behavioral coverage

---

### 6. Failure Safety

Review:
- Error handling and cleanup behavior
- Retries and fallback logic
- Null handling and transactional safety

Flag:
- Silent failures and unsafe retries
- Partial mutations
- Undefined fallback behavior
- Inconsistent error handling

---

### 7. State and Data Integrity

Review:
- Mutation patterns and validation consistency
- Concurrency assumptions and transactional boundaries
- Lifecycle management

Flag:
- Mutable shared state
- Duplicated state
- Inconsistent invariants
- Race-condition exposure
- Undefined ownership

---

### 8. Consistency

Review consistency of:
- Naming and structure
- Async patterns
- Validation and error handling approaches
- Module organization and testing style

Flag:
- Paradigm mixing
- Implementation drift
- Ad hoc architecture erosion

---

### 9. Operational Safety

Review:
- Logging, tracing, metrics
- Retries, timeouts, resource handling

Flag:
- Unbounded operations
- Missing observability
- Excessive logging noise
- Resource leaks

---

### 10. Dependency Hygiene

Review:
- Dependency necessity and package weight
- Runtime coupling and framework overreach

Flag:
- Oversized dependencies for small problems
- Duplicate or abandoned libraries
- Unnecessary runtime dependencies
- Framework lock-in

---

## Code Quality Heuristics

Automatically flag:

- Functions doing multiple unrelated things
- Classes with excessive responsibilities
- Utilities named `Helper`, `Manager`, `Processor`, `Utils`, or `Common`
- Abstractions with only one implementation
- Deep nesting
- Boolean parameter explosions
- Excessive optional arguments
- Hidden mutations or side effects during validation
- Duplicated validation or business rules
- Excessive mocking
- Test assertions coupled to implementation
- Large orchestrator methods
- Feature flags leaking throughout code
- Commented-out code
- Dead abstractions
- Async behavior without timeout handling
- Retries without idempotency guarantees
- Broad exception suppression
- Persistence models leaking externally
