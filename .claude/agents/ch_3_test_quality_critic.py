#!/usr/bin/env python3
"""
.claude/agents/ch_3_test_quality_critic.py

Self-contained test quality review that runs against a local Ollama LLM.
Called by ch-3-test-auto.py (automated path) when local LLM is configured for
the "test-quality" gate. This is Gate 2 of the test pipeline — it runs only
after the test critic (Gate 1) has passed.

Usage:
  python3 .claude/agents/ch_3_test_quality_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

from pathlib import Path

from agent_common.files import read_changed_files, read_optional, require_files
from agent_common.git import get_changed_files
from agent_common.ollama import run_local_critic_cli

RESULT_PREFIX = "ch-3-test-quality-review-result"

TEST_DIRS = ("backend/tests/", "frontend/tests/")


def build_test_quality_review_prompt(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    test_principles: str,
    iteration: int,
    violations_block: str = "",
    changed_files_section: str | None = None,
    output_instructions: str = "",
) -> str:
    """
    Build the test quality review prompt.

    changed_files_section:
      None  → include git diff instructions (Claude path; agent reads files with tools)
      str   → embed pre-fetched content (local LLM path)
    """
    if changed_files_section is None:
        review_process = (
            "1. Run `git diff main...HEAD --name-only` to identify all changed files\n"
            "2. Filter to test files only: backend/tests/ and frontend/tests/\n"
            "3. Read each changed test file in full\n"
            "4. Do NOT read implementation files — none should exist yet\n"
            "5. Evaluate against every rule below"
        )
    else:
        review_process = f"--- CHANGED TEST FILES (git diff main...HEAD, test paths only) ---\n{changed_files_section}"

    tail = (
        "- status is FAIL if any blocking_issue exists with severity Critical or High (more than 2 High = FAIL)\n"
        "- status is PASS if no Critical issues and at most 2 High issues and confidence >= 7"
    )
    if output_instructions:
        tail += f"\n{output_instructions}"

    return f"""You are the Test Quality Review Agent for a spec-kit project.

Your sole function is to evaluate the test files written on this branch for assertion
quality, test naming, and CI readiness. You do not validate task traceability, red-state
confirmation, spec coverage, implementation leakage, test isolation, or stack compliance —
those are handled by the Test Critic. You do not fix code. You do not write code. You
identify test quality issues only.
{violations_block}
Inputs already loaded for you:

--- CONSTITUTION ---
{constitution}

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

--- TEST PRINCIPLES ---
{test_principles}

Review process:
{review_process}

Evaluate against these rules:

§TQ7 Assertion Quality: no tautological assertions where the expected and actual values are
always equal regardless of implementation (e.g. `expect(true).toBe(true)`,
`expect(mock.returnValue).toBe(mock.returnValue)`) — every assertion must be capable of
failing if the implementation is wrong; cite the specific expect() call if found.
§TQ8 Test Naming: test names (in it(), test(), describe()) must describe the behaviour or
scenario under test, not the implementation detail — names like "calls the service",
"invokes the function", "runs the query" are implementation-coupled and should be cited;
names like "returns 404 when job not found" are acceptable.
§TQ9 CI Readiness: no test.only, describe.only, or it.only directive in any changed test
file — these cause other tests to be silently skipped in CI; cite the specific file and line
if found.

FAIL if: any Critical issue exists, more than 2 High severity issues exist, or confidence
is below 7/10.

Output ONLY valid JSON, no preamble, no markdown fences:
{{
  "iteration": {iteration},
  "status": "PASS or FAIL",
  "confidence": <number 1-10>,
  "blocking_issues": [
    {{
      "title": "<short title>",
      "severity": "Critical or High",
      "principle": "<rule label, e.g. §TQ7 — Assertion Quality>",
      "location": "<file path and line number or test name>",
      "finding": "<specific, citable description>"
    }}
  ],
  "non_blocking_concerns": [
    {{
      "title": "<short title>",
      "severity": "Medium or Low",
      "principle": "<rule label>",
      "location": "<file path and line number or test name>",
      "finding": "<specific, citable description>"
    }}
  ],
  "required_remediations": ["<concrete required change>"],
  "summary": "<one paragraph>"
}}

Rules:
{tail}"""


def main():
    def _build(spec_dir: Path, iteration: int) -> str:
        constitution_path = Path(".specify/memory/constitution.md")
        test_principles_path = Path(".specify/memory/test-principles.md")
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"
        tasks_path = spec_dir / "tasks.md"

        require_files(
            "ch-3-test-quality-review", constitution_path, spec_path, plan_path, tasks_path
        )

        constitution = constitution_path.read_text(encoding="utf-8")
        test_principles = read_optional(test_principles_path, "(test-principles.md not found)")
        spec = spec_path.read_text(encoding="utf-8")
        plan = plan_path.read_text(encoding="utf-8")
        tasks = tasks_path.read_text(encoding="utf-8")

        changed_files = get_changed_files()
        changed_test_files = read_changed_files(changed_files, TEST_DIRS)

        return build_test_quality_review_prompt(
            constitution,
            spec,
            plan,
            tasks,
            test_principles,
            iteration,
            changed_files_section=changed_test_files,
        )

    run_local_critic_cli(
        "test-quality-review", "test-quality", RESULT_PREFIX, _build, summary_style="confidence"
    )


if __name__ == "__main__":
    main()
