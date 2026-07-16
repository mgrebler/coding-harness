#!/usr/bin/env python3
"""
.claude/agents/ch_2_tasks_critic.py

Self-contained tasks critic that runs against a local Ollama LLM.
Called by ch_2_tasks_auto.py when local LLM is configured for the "tasks" critic.

Usage:
  python3 .claude/agents/ch_2_tasks_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

import re
from pathlib import Path

from agent_common.files import require_files
from agent_common.ollama import run_local_critic_cli

CRITIC_RESULT_PREFIX = "ch-2-tasks-critic-result"


def _classify_txxx_issues(stripped: str) -> list[str]:
    """Check a line already known to contain a [Txxx] task ID for missing
    required components."""
    issues = []
    if not re.search(r"\[TEST\]|\[IMPL\]", stripped):
        issues.append("MISSING [TEST] or [IMPL]")
    if not re.search(r"\[US\d+\]", stripped):
        issues.append("MISSING [USX] story label")
    return issues


def _record_txxx_result(
    stripped: str, i: int, complete_tasks: list[str], incomplete_tasks: list[str]
) -> None:
    """Classify a line already known to contain a [Txxx] task ID and append it to
    the complete or incomplete bucket."""
    entry = f"  Line {i}: {stripped[:100]}"
    issues = _classify_txxx_issues(stripped)
    if issues:
        incomplete_tasks.append(f"{entry} ← {', '.join(issues)}")
    else:
        complete_tasks.append(entry)


def _classify_lines(tasks: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Bucket each non-blank, non-comment line into complete_tasks, incomplete_tasks,
    numbered, or bullets."""
    complete_tasks = []
    incomplete_tasks = []
    numbered = []
    bullets = []

    for i, line in enumerate(tasks.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^\d+\.", stripped):
            numbered.append(f"  Line {i}: {stripped[:100]}")
        elif stripped.startswith(("- ", "* ")):
            if re.search(r"\[T\d+\]", stripped):
                _record_txxx_result(stripped, i, complete_tasks, incomplete_tasks)
            else:
                bullets.append(f"  Line {i}: {stripped[:100]}")
        elif re.match(r"\[T\d+\]", stripped):
            _record_txxx_result(stripped, i, complete_tasks, incomplete_tasks)

    return complete_tasks, incomplete_tasks, numbered, bullets


def _analyze_task_format(tasks: str) -> str:
    complete_tasks, incomplete_tasks, numbered, bullets = _classify_lines(tasks)

    parts = []
    if complete_tasks:
        parts.append(
            "Complete machine-readable tasks ([Txxx] [TEST|IMPL] [PX] [USX] format):\n"
            + "\n".join(complete_tasks)
        )
    if incomplete_tasks:
        parts.append(
            "Incomplete [Txxx] tasks — VIOLATE §T5 (required components missing):\n"
            + "\n".join(incomplete_tasks)
        )
    if numbered:
        parts.append(
            "Numbered list entries found — THESE VIOLATE §T5 (must use [Txxx] [TEST|IMPL] format):\n"
            + "\n".join(numbered)
        )
    # If [Txxx] tasks exist, bullets are sub-items under them, not standalone tasks.
    if bullets and not complete_tasks and not incomplete_tasks:
        parts.append(
            "Plain bullet entries found (no [Txxx] ID, no machine-readable tasks in file) — may violate §T5:\n"
            + "\n".join(bullets)
        )
    if not parts:
        parts.append("No task entries detected.")
    return "\n\n".join(parts)


def build_tasks_critic_prompt(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    iteration: int,
    violations_block: str = "",
    output_instructions: str = "",
    task_format_analysis: str = "",
) -> str:
    tail = (
        "- status is FAIL if any violation is BLOCKING\n"
        "- status is PASS only if zero BLOCKING violations"
    )
    if output_instructions:
        tail += f"\n{output_instructions}"
    task_format_section = (
        f"Task format analysis (pre-computed for §T5 check):\n{task_format_analysis}\n\n"
        if task_format_analysis
        else ""
    )
    return f"""You are the Tasks Critic Agent for a spec-kit project.

Your sole function is to validate tasks.md against plan.md, spec.md, and constitution.md.
You do not suggest improvements. You do not rewrite sections. You do not edit any files. You identify violations only.
{violations_block}
Inputs already loaded for you:

--- CONSTITUTION ---
{constitution}

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS (current) ---
{task_format_section}Full tasks.md:
{tasks}

Validate against ALL rules in the embedded CONSTITUTION document above.
That document is the authoritative source for all project-specific constraints — stack,
TDD policy, schema migration requirements, v1 scope, task atomicity, and parallelism rules.
Read and apply every section in that document.

For violations derived from the constitution, use the section as the rule label,
e.g. "Constitution §5 — TDD", "Constitution §2 — Stack Constraints".

Harness process checks (apply in addition to the above):

§T1 Plan Traceability [BLOCKING]: every phase in tasks.md maps to a named section or deliverable in plan.md
§T2 Spec Coverage [BLOCKING]: every user story in spec.md has a corresponding phase in tasks.md
§T5 Task Format [BLOCKING]: every task entry must use the machine-readable format `[TXXX] [TEST|IMPL] [PY] [USZ] description` with an explicit `[TEST]` or `[IMPL]` label and a task ID like `[T001]`. Tasks written as numbered lists (1. 2. 3.), plain bullet points, or free-form prose without `[TEST]`/`[IMPL]` labels ALL violate §T5.
§T7 Dependency Validity [BLOCKING]: all dep references resolve to real task IDs within tasks.md; no cycles; [P] tasks have no unresolved deps
§T10 Phase Checkpoints [WARNING — never BLOCKING]: a checkpoint line is missing or not runnable — severity MUST be WARNING; if a checkpoint is present in any form at or after the phase tasks do NOT include this rule in violations at all

Evidence standard — before reporting any violation you MUST:
- Quote the specific task ID, phase heading, or line that constitutes the violation
- Never report a violation based on speculation or hypothetical issues
- Only use rule labels from the §T rules above — never invent a rule label
- If a rule does not apply, add it to not_applicable rather than inventing a violation

Output ONLY valid JSON, no preamble, no markdown fences:
{{
  "iteration": {iteration},
  "status": "PASS or FAIL",
  "violations": [
    {{
      "rule": "<rule label, e.g. §T2 — Spec Coverage>",
      "severity": "BLOCKING or WARNING",
      "location": "<phase heading or task ID in tasks.md>",
      "finding": "<specific, citable description of the violation>"
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
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"
        tasks_path = spec_dir / "tasks.md"

        require_files("ch-2-tasks-critic", constitution_path, spec_path, plan_path, tasks_path)

        constitution = constitution_path.read_text(encoding="utf-8")
        spec = spec_path.read_text(encoding="utf-8")
        plan = plan_path.read_text(encoding="utf-8")
        tasks = tasks_path.read_text(encoding="utf-8")

        task_format_analysis = _analyze_task_format(tasks)
        return build_tasks_critic_prompt(
            constitution, spec, plan, tasks, iteration, task_format_analysis=task_format_analysis
        )

    run_local_critic_cli("tasks", CRITIC_RESULT_PREFIX, _build)


if __name__ == "__main__":
    main()
