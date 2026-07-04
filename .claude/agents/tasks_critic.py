#!/usr/bin/env python3
"""
.claude/agents/tasks_critic.py

Self-contained tasks critic that runs against a local Ollama LLM.
Called by tasks-auto.py when local LLM is configured for the "tasks" critic.

Usage:
  python3 .claude/agents/tasks_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

import argparse
import json
import re
import sys
from pathlib import Path

from agent_common import (
    call_local_llm,
    get_feature_from_branch,
    load_local_llm_config,
    next_iteration,
    strip_fences,
    write_file,
)

CRITIC_RESULT_PREFIX = "tasks-critic-result"


def _analyze_task_format(tasks: str) -> str:
    lines = tasks.splitlines()
    complete_tasks = []
    incomplete_tasks = []
    numbered = []
    bullets = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^\d+\.", stripped):
            numbered.append(f"  Line {i}: {stripped[:100]}")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if re.search(r"\[T\d+\]", stripped):
                issues = []
                if not re.search(r"\[TEST\]|\[IMPL\]", stripped):
                    issues.append("MISSING [TEST] or [IMPL]")
                if not re.search(r"\[US\d+\]", stripped):
                    issues.append("MISSING [USX] story label")
                if issues:
                    incomplete_tasks.append(f"  Line {i}: {stripped[:100]} ← {', '.join(issues)}")
                else:
                    complete_tasks.append(f"  Line {i}: {stripped[:100]}")
            else:
                bullets.append(f"  Line {i}: {stripped[:100]}")
        elif re.match(r"\[T\d+\]", stripped):
            issues = []
            if not re.search(r"\[TEST\]|\[IMPL\]", stripped):
                issues.append("MISSING [TEST] or [IMPL]")
            if not re.search(r"\[US\d+\]", stripped):
                issues.append("MISSING [USX] story label")
            if issues:
                incomplete_tasks.append(f"  Line {i}: {stripped[:100]} ← {', '.join(issues)}")
            else:
                complete_tasks.append(f"  Line {i}: {stripped[:100]}")

    parts = []
    if complete_tasks:
        parts.append("Complete machine-readable tasks ([Txxx] [TEST|IMPL] [PX] [USX] format):\n" + "\n".join(complete_tasks))
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
    # Only flag plain bullets as potentially violating when NO [Txxx] tasks exist —
    # if [Txxx] entries are present, bullets are sub-item descriptions under a task, not standalone tasks.
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
        f"- status is FAIL if any violation is BLOCKING\n"
        f"- status is PASS only if zero BLOCKING violations"
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
    parser = argparse.ArgumentParser(description="Tasks critic using local LLM")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    parser.add_argument("--iteration", type=int, help="Iteration number (auto-detected if omitted)")
    args = parser.parse_args()

    config = load_local_llm_config("tasks")
    if config is None:
        sys.exit(2)

    feature = args.feature or get_feature_from_branch("tasks-critic")
    spec_dir = Path(f"specs/{feature}")

    iteration = args.iteration if args.iteration is not None else next_iteration(spec_dir, CRITIC_RESULT_PREFIX)

    constitution_path = Path(".specify/memory/constitution.md")
    spec_path = spec_dir / "spec.md"
    plan_path = spec_dir / "plan.md"
    tasks_path = spec_dir / "tasks.md"

    for p in (constitution_path, spec_path, plan_path, tasks_path):
        if not p.exists():
            print(f"[tasks-critic] ERROR: required file not found: {p}", flush=True)
            sys.exit(1)

    constitution = constitution_path.read_text(encoding="utf-8")
    spec = spec_path.read_text(encoding="utf-8")
    plan = plan_path.read_text(encoding="utf-8")
    tasks = tasks_path.read_text(encoding="utf-8")

    task_format_analysis = _analyze_task_format(tasks)
    prompt = build_tasks_critic_prompt(constitution, spec, plan, tasks, iteration,
                                       task_format_analysis=task_format_analysis)

    print(f"[tasks-critic] Running iteration {iteration} via local LLM ({config['model']})...", flush=True)

    def _progress(token_count: int, elapsed_s: float, done: bool = False) -> None:
        if done:
            print(f"[tasks-critic]   done — {token_count} tokens in {elapsed_s:.0f}s", flush=True)
        else:
            print(f"[tasks-critic]   ... {token_count} tokens ({elapsed_s:.0f}s elapsed)", flush=True)

    try:
        raw = call_local_llm(prompt, config, progress_fn=_progress)
    except Exception as e:
        print(f"[tasks-critic] ERROR: local LLM call failed: {e}", flush=True)
        sys.exit(1)

    cleaned = strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[tasks-critic] ERROR: could not parse LLM response as JSON: {e}", flush=True)
        print(f"[tasks-critic] Raw response (first 500 chars): {cleaned[:500]}", flush=True)
        sys.exit(1)

    result["iteration"] = iteration

    result_path = spec_dir / f"{CRITIC_RESULT_PREFIX}-{iteration}.json"
    write_file(result_path, json.dumps(result, indent=2))

    status = result.get("status", "FAIL")
    violations = result.get("violations", [])
    blocking = sum(1 for v in violations if v.get("severity") == "BLOCKING")
    warnings = sum(1 for v in violations if v.get("severity") == "WARNING")

    if status == "PASS":
        print(f"[tasks-critic] iteration {iteration} → PASS → {result_path}", flush=True)
    else:
        print(f"[tasks-critic] iteration {iteration} → FAIL ({blocking} blocking, {warnings} warning) → {result_path}", flush=True)


if __name__ == "__main__":
    main()
