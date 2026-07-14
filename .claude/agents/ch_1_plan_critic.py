#!/usr/bin/env python3
"""
.claude/agents/ch_1_plan_critic.py

Self-contained plan critic that runs against a local Ollama LLM.
Called by ch-1-plan-critic/SKILL.md (human-in-the-loop) and
ch_1_plan_auto.py (automated path) when local LLM is configured.

Usage:
  python3 .claude/agents/ch_1_plan_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

from pathlib import Path

from agent_common.files import read_optional, require_files
from agent_common.ollama import run_local_critic_cli

CRITIC_RESULT_PREFIX = "ch-1-plan-critic-result"


def build_plan_critic_prompt(
    constitution: str,
    architecture: str,
    spec: str,
    plan: str,
    iteration: int,
    violations_block: str = "",
    output_instructions: str = "",
) -> str:
    tail = (
        "- status is FAIL if any violation is BLOCKING\n"
        "- status is PASS only if zero BLOCKING violations"
    )
    if output_instructions:
        tail += f"\n{output_instructions}"
    return f"""You are the Plan Critic Agent for a spec-kit project.

Your sole function is to validate plan.md against constitution.md, architecture.md, and spec.md.
You do not suggest improvements. You do not rewrite sections. You do not edit any files. You identify violations only.
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

Validate against ALL rules in the embedded CONSTITUTION and ARCHITECTURE documents above.
Those documents are the authoritative source for all project-specific constraints — stack,
layer separation, scope boundaries, CI requirements, status pipeline, TDD policy, and
architecture alignment. Read and apply every section in those documents.

For violations derived from the memory documents, use the section as the rule label,
e.g. "Constitution §2 — Stack Constraints", "Architecture §2 — Backend Layer Separation".

Harness process checks (apply in addition to the above):

Traceability [BLOCKING]: plan references spec.md; every acceptance criterion in spec.md is
addressed; plan references data-model.md and contracts/ where applicable

TDD Policy scope note: a Constitution §4-style TDD Policy is satisfied at the PLAN stage if
plan.md acknowledges that tests precede implementation (e.g. a Constitution Check statement,
or an explicit note that [TEST] tasks will precede [IMPL] tasks) — do NOT flag a violation
merely because plan.md itself lacks a literal [TEST]/[IMPL] task-by-task breakdown; that
granular breakdown belongs in tasks.md, a later pipeline stage the plan critic does not
validate. Only flag a TDD Policy violation if the plan actively contradicts RED→GREEN→REFACTOR
(e.g. proposes writing implementation before tests) or omits any acknowledgment of the process
entirely.

Output ONLY valid JSON, no preamble, no markdown fences:
{{
  "iteration": {iteration},
  "status": "PASS or FAIL",
  "violations": [
    {{
      "rule": "<section and label>",
      "severity": "BLOCKING or WARNING",
      "location": "<section in plan.md>",
      "finding": "<specific citable description>"
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

Rules:
{tail}"""


def main():
    def _build(spec_dir: Path, iteration: int) -> str:
        constitution_path = Path(".specify/memory/constitution.md")
        architecture_path = Path(".specify/memory/architecture.md")
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"

        require_files("ch-1-plan-critic", constitution_path, spec_path, plan_path)

        constitution = constitution_path.read_text(encoding="utf-8")
        architecture = read_optional(architecture_path, "(architecture.md not found)")
        spec = spec_path.read_text(encoding="utf-8")
        plan = plan_path.read_text(encoding="utf-8")

        return build_plan_critic_prompt(constitution, architecture, spec, plan, iteration)

    run_local_critic_cli("plan-critic", "plan", CRITIC_RESULT_PREFIX, _build)


if __name__ == "__main__":
    main()
