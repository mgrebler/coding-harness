#!/usr/bin/env python3
"""
.claude/agents/implement_critic.py

Self-contained implement critic that runs against a local Ollama LLM.
Called by implement-auto.py when local LLM is configured for the "implement" critic.

Usage:
  python3 .claude/agents/implement_critic.py [--feature 012-my-feature] [--iteration 2]

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
    get_changed_files,
    get_feature_from_branch,
    load_local_llm_config,
    next_iteration,
    strip_fences,
    write_file,
)

CRITIC_RESULT_PREFIX = "implement-critic-result"

_PROHIBITED_PACKAGES = ("'express'", '"express"', "'fastify'", '"fastify"', "'koa'", '"koa"',
                        "'jest'", '"jest"', "'mocha'", '"mocha"', "'jasmine'", '"jasmine"')

_ROUTE_METHOD_PATTERNS = (".get(", ".post(", ".put(", ".delete(", ".patch(", ".options(", ".head(")


def _extract_imports(content: str) -> str:
    lines = content.splitlines()
    hits = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            prohibited = any(pkg in stripped for pkg in _PROHIBITED_PACKAGES)
            flag = " ← PROHIBITED PACKAGE" if prohibited else ""
            hits.append(f"  Line {i}: {stripped}{flag}")
    return "\n".join(hits)


def _extract_routes(content: str) -> str:
    lines = content.splitlines()
    hits = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if any(pat in stripped for pat in _ROUTE_METHOD_PATTERNS):
            hits.append(f"  Line {i}: {stripped[:120]}")
    return "\n".join(hits)


def _annotate_source_files(file_section: str) -> str:
    parts = re.split(r"(?m)^(--- .+ ---\n)", file_section)
    out = []
    i = 0
    while i < len(parts):
        segment = parts[i]
        if re.match(r"--- .+ ---\n", segment) and i + 1 < len(parts):
            content = parts[i + 1]
            imports = _extract_imports(content)
            routes = _extract_routes(content)
            annotations = []
            if imports:
                annotations.append(
                    "Imports in this file (pre-extracted for §2 Stack Constraints check):\n" + imports
                )
            if routes:
                annotations.append(
                    "Routes in this file (pre-extracted for §I7 Spec Compliance check):\n" + routes
                )
            if annotations:
                content = "\n\n".join(annotations) + "\n\nFull file:\n" + content
            out.append(segment)
            out.append(content)
            i += 2
        else:
            out.append(segment)
            i += 1
    return "".join(out)


def read_contracts(spec_dir: Path) -> str:
    contracts_dir = spec_dir / "contracts"
    if not contracts_dir.exists():
        return (
            "(no contracts directory — the 'documented shapes from contracts/' clause in §3 is NOT applicable "
            "to this branch; response shapes are validated via spec acceptance criteria instead. "
            "Absence of a contracts/ directory is NOT a §3 violation.)"
        )
    files = sorted(contracts_dir.glob("*"))
    if not files:
        return "(contracts directory is empty)"
    parts = []
    for f in files:
        try:
            parts.append(f"--- {f.name} ---\n{f.read_text(encoding='utf-8')}")
        except Exception:
            parts.append(f"--- {f.name} --- (could not read)")
    return "\n\n".join(parts)


def build_implement_critic_prompt(
    constitution: str,
    spec: str,
    plan: str,
    tasks: str,
    iteration: int,
    violations_block: str = "",
    architecture: str | None = None,
    contracts: str | None = None,
    data_model: str | None = None,
    changed_files_section: str | None = None,
    output_instructions: str = "",
) -> str:
    """
    Build the implement critic prompt.

    changed_files_section:
      None  → include git diff instructions (Claude path; agent reads files with tools)
      str   → embed pre-fetched content (local LLM path)

    architecture, contracts, data_model:
      None  → not embedded (Claude path reads them with tools)
      str   → embed content (local LLM path)
    """
    extra_sections = ""
    if architecture is not None:
        extra_sections += f"\n--- ARCHITECTURE ---\n{architecture}\n"
    if contracts is not None:
        extra_sections += f"\n--- CONTRACTS ---\n{contracts}\n"
    if data_model is not None:
        extra_sections += f"\n--- DATA MODEL ---\n{data_model}\n"

    if changed_files_section is None:
        file_input = (
            "Validation process:\n"
            "1. Run `git diff main...HEAD --name-only` and `git status --short` to identify all changed files\n"
            "2. Read each changed source file under backend/src/, frontend/src/, prisma/, and package.json files\n"
            "3. Read each changed test file under backend/tests/ and frontend/tests/\n"
            "4. Validate against every rule below"
        )
    else:
        file_input = f"--- CHANGED SOURCE FILES (git diff main...HEAD) ---\n{changed_files_section}"

    tail = (
        f"- status is FAIL if any violation is BLOCKING\n"
        f"- status is PASS only if zero BLOCKING violations"
    )
    if output_instructions:
        tail += f"\n{output_instructions}"

    return f"""You are the Implement Critic Agent for a spec-kit project.

Your sole function is to validate the code written on this branch against the rules below.
You do not fix code. You do not write code. You identify violations only.
{violations_block}
Inputs already loaded for you:

--- CONSTITUTION ---
{constitution}
{extra_sections}
--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

{file_input}

IMPORTANT context: this critic runs AFTER all tasks are complete. The branch contains BOTH test files (written during [TEST] tasks) AND implementation files (written during [IMPL] tasks). The presence of implementation files is expected and correct — this critic validates the QUALITY of the implementation, not whether [TEST] preceded [IMPL] (that was enforced during task execution). Do NOT report TDD order violations based solely on the presence of both test and implementation files on the branch.

Validate against ALL rules in the embedded CONSTITUTION and ARCHITECTURE documents above.
Those documents are the authoritative source for all project-specific constraints — stack,
layer separation, TDD compliance, test coverage, contract compliance, schema migration,
styling, TypeScript safety, and CI readiness. Read and apply every section.

For violations derived from the memory documents, use the section as the rule label,
e.g. "Constitution §2 — Stack Constraints", "Architecture §2 — Backend Layer Separation".

Harness process checks (apply in addition to the above):

§I1 Task Traceability [BLOCKING]: every changed source file corresponds to a path or component listed in tasks.md; no phantom files
§I7 Spec Compliance [BLOCKING]: implemented behaviour covers every acceptance criterion in spec.md; nothing added beyond what spec.md requires

§2 Stack Constraints — detection method: check IMPORT STATEMENTS ONLY (use the pre-extracted "Imports in this file" lists above). A violation exists ONLY IF an import line contains a prohibited package: 'express', 'fastify', 'koa', 'jest', 'mocha', or 'jasmine'. NOT violations: `import {{ Hono }} from 'hono'` (Hono package and class share the same name — this IS correct Hono usage), `new Hono()` (correct Hono app/router instantiation), `app.route()`, `route.get()`, `route.post()` (standard Hono routing API). If the pre-extracted import list shows no prohibited packages, §2 PASSES — do not add it to violations.

Architecture §2 — Backend Layer Separation: a violation requires a route handler to DIRECTLY contain SQL queries, raw DB instantiation (e.g. `new PrismaClient()`), or embedded business logic algorithms. NOT violations: `app.route(path, subRouter)` in index.ts (the correct entry-point pattern for mounting sub-routers), a route handler calling a helper function or service, imports between project files. Registering a sub-router in index.ts is the correct architecture — it does not violate layer separation.

§I7 Spec Compliance — detection method: use the pre-extracted "Routes in this file" lists to compare implemented route paths against spec acceptance criteria. A §I7 violation exists when a route path, HTTP method, or response shape does not match what spec.md requires.

Evidence standard — before adding any item to the violations array you MUST:
- Quote the exact line(s) that constitute the violation AND name the specific prohibited behavior (e.g. "imports from 'express'" for §2, "implements /status but spec requires /health" for §I7)
- If the quoted code does not show a specific prohibited behavior, it belongs in not_applicable, not violations
- If your analysis concludes "does not violate" or "no violation found", add it to not_applicable instead — do NOT put it in violations
- Never report a violation based on hypothetical future scenarios, code that might be added later, or conditions that "could" arise

Output ONLY valid JSON, no preamble, no markdown fences:
{{
  "iteration": {iteration},
  "status": "PASS or FAIL",
  "violations": [
    {{
      "rule": "<rule label, e.g. §I4 — Backend Layer Separation>",
      "severity": "BLOCKING or WARNING",
      "location": "<file path and line number or function name>",
      "finding": "<exact quoted line(s) from the source file constituting the violation>"
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
    parser = argparse.ArgumentParser(description="Implement critic using local LLM")
    parser.add_argument("--feature", help="Feature folder name (derived from git branch if omitted)")
    parser.add_argument("--iteration", type=int, help="Iteration number (auto-detected if omitted)")
    args = parser.parse_args()

    config = load_local_llm_config("implement")
    if config is None:
        sys.exit(2)

    feature = args.feature or get_feature_from_branch("implement-critic")
    spec_dir = Path(f"specs/{feature}")

    iteration = args.iteration if args.iteration is not None else next_iteration(spec_dir, CRITIC_RESULT_PREFIX)

    constitution_path = Path(".specify/memory/constitution.md")
    architecture_path = Path(".specify/memory/architecture.md")
    spec_path = spec_dir / "spec.md"
    plan_path = spec_dir / "plan.md"
    tasks_path = spec_dir / "tasks.md"
    data_model_path = spec_dir / "data-model.md"

    for p in (constitution_path, spec_path, plan_path, tasks_path):
        if not p.exists():
            print(f"[implement-critic] ERROR: required file not found: {p}", flush=True)
            sys.exit(1)

    constitution = constitution_path.read_text(encoding="utf-8")
    architecture = architecture_path.read_text(encoding="utf-8") if architecture_path.exists() else "(architecture.md not found)"
    spec = spec_path.read_text(encoding="utf-8")
    plan = plan_path.read_text(encoding="utf-8")
    tasks = tasks_path.read_text(encoding="utf-8")
    contracts = read_contracts(spec_dir)
    data_model = data_model_path.read_text(encoding="utf-8") if data_model_path.exists() else "(data-model.md not found)"

    changed_files = get_changed_files()
    content_parts = []
    for path_str in changed_files:
        if (path_str.startswith("specs/")
                or "-result-" in path_str):
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            content_parts.append(f"--- {path_str} ---\n{p.read_text(encoding='utf-8')}")
        except Exception:
            content_parts.append(f"--- {path_str} --- (could not read)")
    changed_sources = "\n\n".join(content_parts) if content_parts else "(no changed files found)"
    changed_sources = _annotate_source_files(changed_sources)

    prompt = build_implement_critic_prompt(
        constitution, spec, plan, tasks, iteration,
        architecture=architecture,
        contracts=contracts,
        data_model=data_model,
        changed_files_section=changed_sources,
    )

    print(f"[implement-critic] Running iteration {iteration} via local LLM ({config['model']})...", flush=True)

    def _progress(token_count: int, elapsed_s: float, done: bool = False) -> None:
        if done:
            print(f"[implement-critic]   done — {token_count} tokens in {elapsed_s:.0f}s", flush=True)
        else:
            print(f"[implement-critic]   ... {token_count} tokens ({elapsed_s:.0f}s elapsed)", flush=True)

    try:
        raw = call_local_llm(prompt, config, progress_fn=_progress)
    except Exception as e:
        print(f"[implement-critic] ERROR: local LLM call failed: {e}", flush=True)
        sys.exit(1)

    cleaned = strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[implement-critic] ERROR: could not parse LLM response as JSON: {e}", flush=True)
        print(f"[implement-critic] Raw response (first 500 chars): {cleaned[:500]}", flush=True)
        sys.exit(1)

    result["iteration"] = iteration

    result_path = spec_dir / f"{CRITIC_RESULT_PREFIX}-{iteration}.json"
    write_file(result_path, json.dumps(result, indent=2))

    status = result.get("status", "FAIL")
    violations = result.get("violations", [])
    blocking = sum(1 for v in violations if v.get("severity") == "BLOCKING")
    warnings = sum(1 for v in violations if v.get("severity") == "WARNING")

    if status == "PASS":
        print(f"[implement-critic] iteration {iteration} → PASS → {result_path}", flush=True)
    else:
        print(f"[implement-critic] iteration {iteration} → FAIL ({blocking} blocking, {warnings} warning) → {result_path}", flush=True)


if __name__ == "__main__":
    main()
