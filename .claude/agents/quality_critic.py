#!/usr/bin/env python3
"""
.claude/agents/quality_critic.py

Self-contained code quality review that runs against a local Ollama LLM.
Called by implement-auto.py (automated path) when local LLM is configured for
the "quality" gate. This is Gate 2 of the implement pipeline — it runs only
after the implement critic (Gate 1) has passed.

Usage:
  python3 .claude/agents/quality_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

from pathlib import Path

from agent_common import (
    get_changed_files,
    read_changed_source_files,
    read_optional,
    require_files,
    run_local_critic_cli,
)

RESULT_PREFIX = "code-quality-review-result"


def build_quality_review_prompt(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    quality_principles: str,
    iteration: int,
    changed_files_section: str | None = None,
    violations_block: str = "",
    output_instructions: str = "",
) -> str:
    """
    Build the code quality review prompt.

    changed_files_section:
      None  → include git diff instructions (Claude path; agent reads files with tools)
      str   → embed pre-fetched content (local LLM path)
    """
    if changed_files_section is None:
        review_process = (
            "1. Run `git diff main...HEAD --name-only` and `git status --short` to identify all changed files\n"
            "2. Read each changed source file under backend/src/, frontend/src/, prisma/\n"
            "3. Read each changed test file under backend/tests/ and frontend/tests/\n"
            "4. Read adjacent existing files for consistency context\n"
            "5. Evaluate against all automatic fail conditions, severity rules, core principles, and heuristics in the CODE QUALITY PRINCIPLES section above"
        )
    else:
        review_process = (
            f"--- CHANGED SOURCE FILES (git diff main...HEAD) ---\n{changed_files_section}"
        )

    tail = (
        "- status is FAIL if any Critical issue exists, more than 2 High issues exist, or confidence < 7\n"
        "- status is PASS otherwise"
    )
    if output_instructions:
        tail += f"\n{output_instructions}"

    return f"""You are the Code Quality Review Agent for a spec-kit project.

Your sole function is to evaluate the implementation for code quality, maintainability, readability, and operational safety.
You do not validate spec/constitution/task compliance — that is handled by the Implement Critic.
You do not fix code. You do not write code. You identify quality issues only.
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

--- CODE QUALITY PRINCIPLES ---
{quality_principles}

Review process:
{review_process}

FAIL if: any Critical issue exists, more than 2 High severity issues exist, or confidence is below 7/10.

Output ONLY valid JSON, no preamble, no markdown fences:
{{
  "iteration": {iteration},
  "status": "PASS or FAIL",
  "confidence": <number 1-10>,
  "blocking_issues": [
    {{
      "title": "<short title>",
      "severity": "Critical or High",
      "principle": "<principle name>",
      "location": "<file path and line or function name>",
      "finding": "<specific, citable description>"
    }}
  ],
  "non_blocking_concerns": [
    {{
      "title": "<short title>",
      "severity": "Medium or Low",
      "principle": "<principle name>",
      "location": "<file path and line or function name>",
      "finding": "<specific, citable description>"
    }}
  ],
  "required_remediations": ["<concrete required correction>"],
  "summary": "<one paragraph>"
}}

Rules:
{tail}"""


def main():
    def _build(spec_dir: Path, iteration: int) -> str:
        constitution_path = Path(".specify/memory/constitution.md")
        quality_principles_path = Path(".specify/memory/code-quality-principles.md")
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"
        tasks_path = spec_dir / "tasks.md"

        require_files("code-quality-review", constitution_path, spec_path, plan_path, tasks_path)

        constitution = constitution_path.read_text(encoding="utf-8")
        quality_principles = read_optional(
            quality_principles_path, "(code-quality-principles.md not found)"
        )
        spec = spec_path.read_text(encoding="utf-8")
        plan = plan_path.read_text(encoding="utf-8")
        tasks = tasks_path.read_text(encoding="utf-8")

        changed_sources = read_changed_source_files(get_changed_files())

        return build_quality_review_prompt(
            constitution,
            spec,
            plan,
            tasks,
            quality_principles,
            iteration,
            changed_files_section=changed_sources,
        )

    run_local_critic_cli(
        "code-quality-review", "quality", RESULT_PREFIX, _build, summary_style="confidence"
    )


if __name__ == "__main__":
    main()
