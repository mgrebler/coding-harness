# Test Principles

## Purpose

Tests exist to provide fast, trustworthy feedback that enables safe change.

This document combines:
- TDD principles
- Sustainable testing practices
- Test quality review criteria
- PASS/FAIL quality gates for autonomous development workflows

---

# Review Outcome Rules

This is a blocking quality gate.

Review outcome must be:

- PASS
- FAIL

FAIL blocks implementation progression until issues are corrected.

---

# Automatic FAIL Conditions

## Behaviour vs Implementation Violations

FAIL if tests primarily verify:

- private methods
- internal implementation details
- internal state inspection
- framework mechanics
- call ordering without business significance
- mock interactions instead of outcomes

## Determinism Violations

FAIL if tests depend on:

- timing assumptions
- sleeps
- randomness
- execution ordering
- external services
- shared mutable state
- environment-specific behaviour

## Mock Abuse

FAIL if:

- value objects are mocked
- domain entities are mocked
- mocks are asserted more than outcomes
- every dependency is mocked
- interaction testing dominates behavioural testing

## Coverage Without Confidence

FAIL if:

- only happy paths are tested
- business rules are not tested
- failure scenarios are ignored
- edge cases are ignored
- assertions are weak

## Fragility Risks

FAIL if:

- behaviour-preserving refactors would likely break tests
- tests are tightly coupled to implementation
- tests verify internal structure instead of behaviour

## AI Test Smells

FAIL if:

- large snapshots replace meaningful assertions
- getters/setters are tested
- dozens of near-identical tests are generated
- production algorithms are duplicated in assertions
- tests exist solely to increase coverage metrics
- helper abstractions obscure behaviour

---

# Severity Rules

FAIL if:

- any Critical finding exists
- more than 3 High findings exist
- Test Confidence Score is below 7/10

PASS only if:

- tests are behaviour-oriented
- tests are deterministic
- tests support refactoring
- important business rules are exercised
- failure scenarios are covered
- test intent is obvious
- mocking is disciplined

---

# Core Testing Principles

## Test Behaviour, Not Implementation

Prefer:

- business outcomes
- domain rules
- public APIs
- externally observable behaviour

Avoid:

- private methods
- internal data structures
- call counts
- implementation-specific assertions

A test should survive a safe refactor.

---

## Single Purpose Tests

Each test should communicate one behaviour.

The reader should immediately understand:

- scenario
- action
- expected outcome

---

## Tests Are Documentation

Tests should be understandable without reading implementation code.

Prefer:

- domain language
- clear naming
- explicit intent

---

## Refactoring Confidence

The primary value of a test suite is confidence during change.

If developers fear refactoring despite high coverage, the tests are failing.

---

## Fast Feedback

Prefer:

- fast execution
- isolated tests
- deterministic behaviour

Avoid unnecessary infrastructure when testing domain behaviour.

---

## Deterministic Tests

A flaky test is a defect.

Tests should produce the same result every run.

---

## Stable Boundaries

Test through interfaces expected to remain stable.

Prefer:

- public APIs
- domain services
- observable behaviour

---

## Tests Should Improve Design

Difficult-to-test code often indicates design problems.

Tests should encourage:

- low coupling
- high cohesion
- explicit dependencies

---

# Sustainable TDD Principles

## Red-Green-Refactor

1. Write a failing test.
2. Make the test pass.
3. Improve the design.

Never skip refactoring.

---

## Write the Simplest Test First

Use the smallest example that demonstrates required behaviour.

Avoid anticipating future requirements.

---

## Drive Design Incrementally

Allow tests to shape design.

Prefer:

- incremental evolution
- demonstrated need
- emergent design

Avoid speculative architecture.

---

## Remove Duplication Carefully

Avoid abstractions after a single occurrence.

Prefer proven patterns over speculative reuse.

---

# Mocking Principles

Mock at system boundaries.

Prefer:

- real domain objects
- fakes
- in-memory implementations

Avoid excessive mocking.

Excessive mocking is often evidence of excessive coupling.

---

# Test Data Principles

Test data should be:

- minimal
- relevant
- obvious

Include only information necessary to communicate behaviour.

---

# Naming Principles

Names should describe behaviour.

Good:

- rejects_duplicate_email
- calculates_invoice_total
- retries_transient_failure

Bad:

- test_invoice
- test1
- should_work

---

# Test Quality Heuristics

Good tests are:

- readable
- focused
- deterministic
- fast
- maintainable
- behaviour-oriented

Poor tests are:

- brittle
- flaky
- implementation-aware
- slow
- difficult to understand
- difficult to maintain

---

# Anti-Rubber-Stamping Rules

Actively search for reasons the test suite should not be trusted.

Do not:

- assume missing scenarios are covered elsewhere
- infer correctness from coverage
- approve based on quantity of tests
- reward excessive mocking

Missing business-rule coverage is itself a finding.

---

# Review Process

## Step 1

Understand:

- requirements
- implementation
- intended behaviour

## Step 2

Identify:

- missing scenarios
- brittle tests
- flaky risks
- poor assertions
- mocking abuse

## Step 3

Evaluate:

- confidence provided
- maintainability
- refactoring safety

## Step 4

Produce findings.

---

# Final Output Format

## Review Status

PASS | FAIL

## Test Confidence Score

X/10

## Blocking Issues

Numbered list.

## Non-Blocking Concerns

Numbered list.

## Required Remediations

Numbered list.

## Detailed Findings

For each finding:

- Severity
- Principle Violated
- Evidence
- Explanation
- Long-Term Consequence
- Recommended Correction

## Positive Observations

Specific strengths only.

## Decision Rationale

Concise explanation.

---

# Final Rule

A test suite is successful when:

- developers trust it
- developers run it frequently
- developers can refactor confidently
- failures identify real problems
- maintenance cost remains low

Tests exist to support change.

Any test that makes change harder should be reconsidered.
