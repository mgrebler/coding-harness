#!/usr/bin/env python3
"""
.claude/agents/ch_3_test_critic.py

Self-contained test critic that runs against a local Ollama LLM.
Called by ch_3_test_auto.py when local LLM is configured for the "test" critic.

Usage:
  python3 .claude/agents/ch_3_test_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

import re
from pathlib import Path

from agent_common.files import read_changed_files, read_optional, require_files
from agent_common.git import get_changed_files
from agent_common.ollama import run_local_critic_cli
from agent_common.project_conventions import resolve_test_dirs

CRITIC_RESULT_PREFIX = "ch-3-test-critic-result"

_ASSERTION_KEYWORDS = (
    "expect(",
    "assert.",
    "toBe(",
    "toEqual(",
    "toContain(",
    "toMatch(",
    "toBeTruthy",
    "toBeFalsy",
    "toHaveLength",
    "toHaveBeenCalled",
)


def _extract_assertions(content: str) -> str:
    """Return a numbered list of assertion lines with line numbers."""
    lines = content.splitlines()
    hits = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if any(kw in stripped for kw in _ASSERTION_KEYWORDS):
            hits.append(f"  Line {i}: {stripped}")
    return "\n".join(hits)


def _annotate_test_files(file_section: str) -> str:
    """
    Prepend each file block in file_section with a pre-extracted assertion list.
    This reduces §TQ3 checking from a search task to a lookup task, cutting
    thinking depth for reasoning models.
    """
    # Split on "--- path ---" markers (produced by read_changed_files)
    parts = re.split(r"(?m)^(--- .+ ---\n)", file_section)
    out = []
    i = 0
    while i < len(parts):
        segment = parts[i]
        if re.match(r"--- .+ ---\n", segment) and i + 1 < len(parts):
            content = parts[i + 1]
            assertions = _extract_assertions(content)
            if assertions:
                content = (
                    "Assertions in this file (pre-extracted for §TQ3 lookup). "
                    "Reminder: expect() calls on response status, headers, body, or "
                    "res.ok are correct test code, NOT §TQ4 implementation-code "
                    "violations, no matter how many appear together:\n"
                    + assertions
                    + "\n\nFull file:\n"
                    + content
                )
            out.append(segment)
            out.append(content)
            i += 2
        else:
            out.append(segment)
            i += 1
    return "".join(out)


def read_test_results(spec_dir: Path) -> str:
    results_dir = spec_dir / "test-results"
    if not results_dir.exists():
        return "(no test-results directory — red-state artifacts missing)"
    files = sorted(results_dir.glob("*-red.txt"))
    if not files:
        return "(test-results directory exists but no *-red.txt artifacts found)"
    parts = []
    for f in files:
        try:
            parts.append(f"--- {f.name} ---\n{f.read_text(encoding='utf-8')}")
        except Exception:
            parts.append(f"--- {f.name} --- (could not read)")
    return "\n\n".join(parts)


def build_test_critic_prompt(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    test_principles: str,
    feature: str,
    iteration: int,
    violations_block: str = "",
    architecture: str | None = None,
    changed_files_section: str | None = None,
    test_results: str | None = None,
    output_instructions: str = "",
) -> str:
    """
    Build the test critic prompt.

    changed_files_section:
      None  → include git diff instructions (Claude path; agent reads files with tools)
      str   → embed pre-fetched content (local LLM path)

    test_results:
      None  → not embedded (Claude path reads them with tools)
      str   → embed content (local LLM path)

    architecture:
      None  → not embedded
      str   → embed content
    """
    extra_sections = ""
    if architecture is not None:
        extra_sections += f"\n--- ARCHITECTURE ---\n{architecture}\n"

    if changed_files_section is None:
        file_input = (
            "Validation process:\n"
            f"1. Run `git diff main...HEAD --name-only` to identify all changed files\n"
            "2. Filter to test files only, per the 'Test file location' bullets under constitution §5 (Test-Driven Development)\n"
            "3. Read each changed test file in full\n"
            f"4. Read all files under specs/{feature}/test-results/ (red-output artifacts)\n"
            "5. Do NOT read implementation files — none should exist yet\n"
            "6. Validate against every rule below"
        )
    else:
        test_results_block = (
            f"\n--- RED-STATE ARTIFACTS (specs/{feature}/test-results/) ---\n{test_results or '(none)'}"
            if test_results is not None
            else ""
        )
        file_input = f"--- CHANGED TEST FILES (git diff main...HEAD, test paths only) ---\n{changed_files_section}{test_results_block}"

    tail = (
        "- status is FAIL if any violation is BLOCKING\n"
        "- status is PASS only if zero BLOCKING violations"
    )
    if output_instructions:
        tail += f"\n{output_instructions}"

    return f"""You are the Test Critic Agent for a spec-kit project.

Your sole function is to validate the test files written on this branch against the rules below.
You do not fix code. You do not write code. You identify violations only.
{violations_block}
Inputs already loaded for you (this project's constitution.md is human-customized, so any
section number referenced above/below may not match — locate the section by heading text instead):

--- CONSTITUTION ---
{constitution}
{extra_sections}
--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

--- TEST PRINCIPLES ---
{test_principles}

{file_input}

Validate against ONLY the harness rules (§TQ1-§TQ6) and the embedded CONSTITUTION and
TEST PRINCIPLES documents above. Constitution and Test Principles are the authoritative source
for all project-specific test constraints. Test quality concerns (assertion quality, test
naming, CI-only-directive readiness) are validated separately by the Test Quality Review agent
— do not check for them here.

Harness process checks:

§TQ1 Task Traceability [BLOCKING]: every changed test file corresponds to a [TEST] task in tasks.md; no test file added without a matching [TEST] task entry
§TQ2 Red State Confirmed [BLOCKING]: a test-results/<TASKID>-red.txt artifact exists for every completed [TEST] task; artifact shows tests failing with assertion errors (e.g. "AssertionError: expected 404 to be 200") or module-not-found errors — an "AssertionError" line in the artifact IS a meaningful failure and PASSES §TQ2. §TQ2 only fails when: (a) the artifact file is missing, (b) the artifact shows zero failing tests, or (c) every failure is a syntax error in the test file itself.
§TQ3 Spec Coverage [BLOCKING]: every SUCCESS CRITERION (SC-NNN line) in spec.md must have at least one corresponding assertion in the test file. If the spec contains no SC-NNN lines, every FUNCTIONAL REQUIREMENT (FR-NNN line) must have at least one corresponding assertion instead. Use the "Assertions in this file (pre-extracted for §TQ3 lookup)" list to check coverage — if a matching assertion appears in the list the requirement is covered. Notes: `expect(res.ok).toBe(true)` satisfies any SC/FR requiring a 2xx or "success" response; `.toContain('application/json')` on a header value IS a value assertion (it checks the header CONTAINS that string, not just that the header exists).
§TQ4 No Implementation Code [BLOCKING]: asserting response status, headers, and body with expect() calls IS correct test code and is NEVER a violation — `expect(res.status).toBe(200)`, `expect(body).toEqual({...})`, `expect(res.headers.get(...)).toContain(...)`, `expect(res.ok).toBe(true)` are ALL normal test assertions. A violation is ONLY when the test file directly contains real implementation logic: raw database queries or client instantiation, route/handler setup, or service class instantiation of the kind the constitution's stack forbids outside implementation code. IMPORTANT: code in plan.md, tasks.md, or other context documents is NOT in the test file — only check lines in the "CHANGED TEST FILES" section above.
§TQ5 Test Isolation [BLOCKING]: no shared mutable state between test cases (e.g. a module-level variable mutated by one test and read by another); if a test creates database records, external files, or other stateful artifacts, cleanup/teardown (afterEach, afterAll, or equivalent) must be present; tests must not depend on execution order to pass — cite the specific test block if a violation is found.
§TQ6 Stack Compliance [BLOCKING]: test files must import only from the approved test libraries defined in the constitution's Stack Constraints section (§2); no other test library or runner may be imported — cite the specific import statement if a violation is found.

Evidence standard — before reporting any violation you MUST:
- Copy the exact line(s) verbatim from the "CHANGED TEST FILES" section above — not from plan.md, tasks.md, or any other document
- Never report a violation based on speculation about runtime behavior, missing files, or hypothetical configuration issues
- Never use the words "may", "might", or "cannot confirm" as justification for a violation
- If a rule does not apply, add it to not_applicable rather than inventing a violation
- Before reporting a §TQ4 violation specifically: re-read the quoted line. If it is an expect() call on res.status, res.headers, a response body/json value, or res.ok, it is NOT a violation — do not report it, even if it resembles "testing implementation details" by general convention. Only SQL queries, raw DB instantiation, route handler setup, or service class instantiation are §TQ4 violations.

Output ONLY valid JSON, no preamble, no markdown fences:
{{
  "iteration": {iteration},
  "status": "PASS or FAIL",
  "violations": [
    {{
      "rule": "<rule label, e.g. §TQ3 — Spec Coverage>",
      "severity": "BLOCKING or WARNING",
      "location": "<file path and line number or test name>",
      "finding": "<quoted evidence from the test file>"
    }}
  ],
  "not_applicable": [
    {{
      "rule": "<rule label>",
      "reason": "<why not applicable>"
    }}
  ],
  "summary": "<one paragraph>"
}}

IMPORTANT: the violations array contains ONLY confirmed rule violations with quoted evidence from the test file. Do NOT include observations, notes, or "X is allowed" entries in violations — those belong in not_applicable or summary.

Rules:
{tail}"""


def main():
    def _build(spec_dir: Path, iteration: int) -> str:
        feature = spec_dir.name
        constitution_path = Path(".specify/memory/constitution.md")
        architecture_path = Path(".specify/memory/architecture.md")
        test_principles_path = Path(".specify/memory/test-principles.md")
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"
        tasks_path = spec_dir / "tasks.md"

        require_files("ch-3-test-critic", constitution_path, spec_path, plan_path, tasks_path)

        constitution = constitution_path.read_text(encoding="utf-8")
        architecture = read_optional(architecture_path, "(architecture.md not found)")
        test_principles = read_optional(test_principles_path, "(test-principles.md not found)")
        spec = spec_path.read_text(encoding="utf-8")
        plan = plan_path.read_text(encoding="utf-8")
        tasks = tasks_path.read_text(encoding="utf-8")

        changed_files = get_changed_files()
        changed_test_files = read_changed_files(changed_files, resolve_test_dirs())
        changed_test_files = _annotate_test_files(changed_test_files)
        test_results = read_test_results(spec_dir)

        return build_test_critic_prompt(
            constitution,
            spec,
            plan,
            tasks,
            test_principles,
            feature,
            iteration,
            architecture=architecture,
            changed_files_section=changed_test_files,
            test_results=test_results,
        )

    run_local_critic_cli("test", CRITIC_RESULT_PREFIX, _build)


if __name__ == "__main__":
    main()
