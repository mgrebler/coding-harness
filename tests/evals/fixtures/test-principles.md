# Test Principles

## TDD Workflow

Follow RED → GREEN → REFACTOR strictly.

- [TEST] tasks write failing tests first (RED). Tests must fail before any implementation.
- [IMPL] tasks make tests pass (GREEN) then refactor while keeping tests green.
- No implementation code in test files.

## Quality Rules

Tests must:
- Verify externally observable behaviour, not internal implementation details.
- Be deterministic — no timing dependencies, random values, or shared mutable state.
- Assert spec-defined outcomes. Asserting a spec-mandated response body is NOT testing implementation details.
- Use domain language in test names.

## FAIL Conditions

FAIL if test files contain:
- Business logic or database queries outside setup/teardown
- test.only, it.only, or describe.only directives
- Mocks of value objects or domain entities
- Assertions that would break on behaviour-preserving refactors

## PASS Conditions

PASS if:
- Tests verify behaviour through public APIs
- Tests are isolated and deterministic
- Assertions map to spec acceptance criteria
- No CI-blocking directives present
