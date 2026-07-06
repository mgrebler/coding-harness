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

import argparse
import json
import sys
from pathlib import Path

from agent_common import (
    call_local_llm,
    get_changed_files,
    get_feature_from_branch,
    load_local_llm_config,
    next_iteration,
    strip_fences,
    write_file,
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
        review_process = f"--- CHANGED SOURCE FILES (git diff main...HEAD) ---\n{changed_files_section}"

    tail = (
        "- status is FAIL if any Critical issue exists, more than 3 High issues exist, or confidence < 7\n"
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

FAIL if: any Critical issue exists, more than 3 High severity issues exist, or confidence is below 7/10.

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
    parser = argparse.ArgumentParser(description="Code quality review using local LLM")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    parser.add_argument("--iteration", type=int, help="Iteration number (auto-detected if omitted)")
    args = parser.parse_args()

    config = load_local_llm_config("quality")
    if config is None:
        sys.exit(2)

    feature = args.feature or get_feature_from_branch("code-quality-review")
    spec_dir = Path(f"specs/{feature}")

    iteration = args.iteration if args.iteration is not None else next_iteration(spec_dir, RESULT_PREFIX)

    constitution_path = Path(".specify/memory/constitution.md")
    quality_principles_path = Path(".specify/memory/code-quality-principles.md")
    spec_path = spec_dir / "spec.md"
    plan_path = spec_dir / "plan.md"
    tasks_path = spec_dir / "tasks.md"

    for p in (constitution_path, spec_path, plan_path, tasks_path):
        if not p.exists():
            print(f"[code-quality-review] ERROR: required file not found: {p}", flush=True)
            sys.exit(1)

    constitution = constitution_path.read_text(encoding="utf-8")
    quality_principles = quality_principles_path.read_text(encoding="utf-8") if quality_principles_path.exists() else "(code-quality-principles.md not found)"
    spec = spec_path.read_text(encoding="utf-8")
    plan = plan_path.read_text(encoding="utf-8")
    tasks = tasks_path.read_text(encoding="utf-8")

    changed_files = get_changed_files()
    content_parts = []
    for path_str in changed_files:
        if path_str.startswith("specs/") or "-result-" in path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            content_parts.append(f"--- {path_str} ---\n{p.read_text(encoding='utf-8')}")
        except Exception:
            content_parts.append(f"--- {path_str} --- (could not read)")
    changed_sources = "\n\n".join(content_parts) if content_parts else "(no changed files found)"

    prompt = build_quality_review_prompt(
        constitution, spec, plan, tasks, quality_principles, iteration,
        changed_files_section=changed_sources,
    )

    print(f"[code-quality-review] Running iteration {iteration} via local LLM ({config['model']})...", flush=True)

    def _progress(token_count: int, elapsed_s: float, done: bool = False) -> None:
        if done:
            print(f"[code-quality-review]   done — {token_count} tokens in {elapsed_s:.0f}s", flush=True)
        else:
            print(f"[code-quality-review]   ... {token_count} tokens ({elapsed_s:.0f}s elapsed)", flush=True)

    try:
        raw = call_local_llm(prompt, config, progress_fn=_progress)
    except Exception as e:
        print(f"[code-quality-review] ERROR: local LLM call failed: {e}", flush=True)
        sys.exit(1)

    cleaned = strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[code-quality-review] ERROR: could not parse LLM response as JSON: {e}", flush=True)
        print(f"[code-quality-review] Raw response (first 500 chars): {cleaned[:500]}", flush=True)
        sys.exit(1)

    result["iteration"] = iteration

    result_path = spec_dir / f"{RESULT_PREFIX}-{iteration}.json"
    write_file(result_path, json.dumps(result, indent=2))

    status = result.get("status", "FAIL")
    confidence = result.get("confidence", 0)
    blocking = len(result.get("blocking_issues", []))

    if status == "PASS":
        print(f"[code-quality-review] iteration {iteration} → PASS (confidence {confidence}/10) → {result_path}", flush=True)
    else:
        print(f"[code-quality-review] iteration {iteration} → FAIL ({blocking} blocking issue(s), confidence {confidence}/10) → {result_path}", flush=True)


if __name__ == "__main__":
    main()
