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
      "finding": "<specific, citable description>"
    }}
  ],
  "non_blocking_concerns": [
    {{
      "title": "<short title>",
      "severity": "Medium or Low",
      "principle": "<principle name>",
      "finding": "<specific, citable description>"
    }}
  ],
  "required_remediations": ["<concrete required change>"],
  "summary": "<one paragraph>"
}}

Rules:
{tail}"""


def main():
    parser = argparse.ArgumentParser(description="Architecture review using local LLM")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    parser.add_argument("--iteration", type=int, help="Iteration number (auto-detected if omitted)")
    args = parser.parse_args()

    config = load_local_llm_config("architecture")
    if config is None:
        sys.exit(2)

    feature = args.feature or get_feature_from_branch("architecture-review")
    spec_dir = Path(f"specs/{feature}")

    iteration = args.iteration if args.iteration is not None else next_iteration(spec_dir, RESULT_PREFIX)

    constitution_path = Path(".specify/memory/constitution.md")
    architecture_path = Path(".specify/memory/architecture.md")
    arch_principles_path = Path(".specify/memory/architecture-principles.md")
    spec_path = spec_dir / "spec.md"
    plan_path = spec_dir / "plan.md"

    for p in (constitution_path, spec_path, plan_path):
        if not p.exists():
            print(f"[architecture-review] ERROR: required file not found: {p}", flush=True)
            sys.exit(1)

    constitution = constitution_path.read_text(encoding="utf-8")
    architecture = architecture_path.read_text(encoding="utf-8") if architecture_path.exists() else "(architecture.md not found)"
    arch_principles = arch_principles_path.read_text(encoding="utf-8") if arch_principles_path.exists() else "(architecture-principles.md not found)"
    spec = spec_path.read_text(encoding="utf-8")
    plan = plan_path.read_text(encoding="utf-8")

    prompt = build_architecture_review_prompt(constitution, architecture, spec, plan, arch_principles, iteration)

    print(f"[architecture-review] Running iteration {iteration} via local LLM ({config['model']})...", flush=True)

    def _progress(token_count: int, elapsed_s: float, done: bool = False) -> None:
        if done:
            print(f"[architecture-review]   done — {token_count} tokens in {elapsed_s:.0f}s", flush=True)
        else:
            print(f"[architecture-review]   ... {token_count} tokens ({elapsed_s:.0f}s elapsed)", flush=True)

    try:
        raw = call_local_llm(prompt, config, progress_fn=_progress)
    except Exception as e:
        print(f"[architecture-review] ERROR: local LLM call failed: {e}", flush=True)
        sys.exit(1)

    cleaned = strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[architecture-review] ERROR: could not parse LLM response as JSON: {e}", flush=True)
        print(f"[architecture-review] Raw response (first 500 chars): {cleaned[:500]}", flush=True)
        sys.exit(1)

    result["iteration"] = iteration

    result_path = spec_dir / f"{RESULT_PREFIX}-{iteration}.json"
    write_file(result_path, json.dumps(result, indent=2))

    status = result.get("status", "FAIL")
    confidence = result.get("confidence", 0)
    blocking = len(result.get("blocking_issues", []))

    if status == "PASS":
        print(f"[architecture-review] iteration {iteration} → PASS (confidence {confidence}/10) → {result_path}", flush=True)
    else:
        print(f"[architecture-review] iteration {iteration} → FAIL ({blocking} blocking issue(s), confidence {confidence}/10) → {result_path}", flush=True)


if __name__ == "__main__":
    main()
