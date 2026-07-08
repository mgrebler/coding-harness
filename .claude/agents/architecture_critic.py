#!/usr/bin/env python3
"""
.claude/agents/architecture_critic.py

Self-contained architecture review that runs against a local Ollama LLM.
Called by plan-auto.py (automated path) when local LLM is configured for
the "architecture" gate. This is Gate 2 of the plan pipeline — it runs only
after the plan critic (Gate 1) has passed.

Usage:
  python3 .claude/agents/architecture_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

from pathlib import Path

from agent_common import (
    read_optional,
    require_files,
    run_local_critic_cli,
)

RESULT_PREFIX = "architecture-review-result"


def build_architecture_review_prompt(
    constitution: str,
    architecture: str,
    spec: str,
    plan: str,
    arch_principles: str,
    iteration: int,
    violations_block: str = "",
    output_instructions: str = "",
) -> str:
    tail = (
        "- status is FAIL if any blocking_issue exists with severity Critical or High (more than 2 High = FAIL)\n"
        "- status is PASS if no Critical issues and at most 2 High issues and confidence >= 7"
    )
    if output_instructions:
        tail += f"\n{output_instructions}"
    return f"""You are the Architecture Review Agent for a spec-kit project.

Your sole function is to evaluate plan.md for architectural quality, best practices, maintainability, and operational safety.
You do not validate spec/constitution compliance — that is handled by the Plan Critic.
You do not rewrite sections. You do not edit any files. You identify architecture issues only.
{violations_block}
Inputs already loaded for you:

--- CONSTITUTION ---
{constitution}

--- ARCHITECTURE ---
{architecture}

--- SPEC ---
{spec}

--- PLAN (current) ---
{plan}

--- ARCHITECTURE PRINCIPLES ---
{arch_principles}

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
      "location": "<section in plan.md>",
      "finding": "<specific, citable description>"
    }}
  ],
  "non_blocking_concerns": [
    {{
      "title": "<short title>",
      "severity": "Medium or Low",
      "principle": "<principle name>",
      "location": "<section in plan.md>",
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
        architecture_path = Path(".specify/memory/architecture.md")
        arch_principles_path = Path(".specify/memory/architecture-principles.md")
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"

        require_files("architecture-review", constitution_path, spec_path, plan_path)

        constitution = constitution_path.read_text(encoding="utf-8")
        architecture = read_optional(architecture_path, "(architecture.md not found)")
        arch_principles = read_optional(arch_principles_path, "(architecture-principles.md not found)")
        spec = spec_path.read_text(encoding="utf-8")
        plan = plan_path.read_text(encoding="utf-8")

        return build_architecture_review_prompt(constitution, architecture, spec, plan, arch_principles, iteration)

    run_local_critic_cli("architecture-review", "architecture", RESULT_PREFIX, _build, summary_style="confidence")


if __name__ == "__main__":
    main()
