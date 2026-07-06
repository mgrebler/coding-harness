#!/usr/bin/env python3
"""
.claude/agents/plan_critic.py

Self-contained plan critic that runs against a local Ollama LLM.
Called by speckit-plan-critic/SKILL.md (human-in-the-loop) and
plan-auto.py (automated path) when local LLM is configured.

Usage:
  python3 .claude/agents/plan_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

import argparse
import json
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

CRITIC_RESULT_PREFIX = "plan-critic-result"


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
        f"- status is FAIL if any violation is BLOCKING\n"
        f"- status is PASS only if zero BLOCKING violations"
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
    parser = argparse.ArgumentParser(description="Plan critic using local LLM")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    parser.add_argument("--iteration", type=int, help="Iteration number (auto-detected if omitted)")
    args = parser.parse_args()

    config = load_local_llm_config("plan")
    if config is None:
        sys.exit(2)

    feature = args.feature or get_feature_from_branch("plan-critic")
    spec_dir = Path(f"specs/{feature}")

    iteration = args.iteration if args.iteration is not None else next_iteration(spec_dir, CRITIC_RESULT_PREFIX)

    constitution_path = Path(".specify/memory/constitution.md")
    architecture_path = Path(".specify/memory/architecture.md")
    spec_path = spec_dir / "spec.md"
    plan_path = spec_dir / "plan.md"

    for p in (constitution_path, spec_path, plan_path):
        if not p.exists():
            print(f"[plan-critic] ERROR: required file not found: {p}", flush=True)
            sys.exit(1)

    constitution = constitution_path.read_text(encoding="utf-8")
    architecture = architecture_path.read_text(encoding="utf-8") if architecture_path.exists() else "(architecture.md not found)"
    spec = spec_path.read_text(encoding="utf-8")
    plan = plan_path.read_text(encoding="utf-8")

    prompt = build_plan_critic_prompt(constitution, architecture, spec, plan, iteration)

    print(f"[plan-critic] Running iteration {iteration} via local LLM ({config['model']})...", flush=True)

    def _progress(token_count: int, elapsed_s: float, done: bool = False) -> None:
        if done:
            print(f"[plan-critic]   done — {token_count} tokens in {elapsed_s:.0f}s", flush=True)
        else:
            print(f"[plan-critic]   ... {token_count} tokens ({elapsed_s:.0f}s elapsed)", flush=True)

    try:
        raw = call_local_llm(prompt, config, progress_fn=_progress)
    except Exception as e:
        print(f"[plan-critic] ERROR: local LLM call failed: {e}", flush=True)
        sys.exit(1)

    cleaned = strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[plan-critic] ERROR: could not parse LLM response as JSON: {e}", flush=True)
        print(f"[plan-critic] Raw response (first 500 chars): {cleaned[:500]}", flush=True)
        sys.exit(1)

    result["iteration"] = iteration

    result_path = spec_dir / f"{CRITIC_RESULT_PREFIX}-{iteration}.json"
    write_file(result_path, json.dumps(result, indent=2))

    status = result.get("status", "FAIL")
    violations = result.get("violations", [])
    blocking = sum(1 for v in violations if v.get("severity") == "BLOCKING")
    warnings = sum(1 for v in violations if v.get("severity") == "WARNING")

    if status == "PASS":
        print(f"[plan-critic] iteration {iteration} → PASS → {result_path}", flush=True)
    else:
        print(f"[plan-critic] iteration {iteration} → FAIL ({blocking} blocking, {warnings} warning) → {result_path}", flush=True)


if __name__ == "__main__":
    main()
