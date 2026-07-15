#!/usr/bin/env python3
"""
.claude/agents/ch_4_implement_critic.py

Self-contained implement critic that runs against a local Ollama LLM.
Called by ch_4_implement_auto.py when local LLM is configured for the "implement" critic.

Usage:
  python3 .claude/agents/ch_4_implement_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

import re
from pathlib import Path

from agent_common.files import read_changed_source_files, read_optional, require_files
from agent_common.git import get_changed_files
from agent_common.ollama import run_local_critic_cli

CRITIC_RESULT_PREFIX = "ch-4-implement-critic-result"

_ROUTE_METHOD_PATTERNS = (".get(", ".post(", ".put(", ".delete(", ".patch(", ".options(", ".head(")


def _extract_imports(content: str) -> str:
    lines = content.splitlines()
    hits = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            hits.append(f"  Line {i}: {stripped}")
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
                    "Imports in this file (pre-extracted for §2 Stack Constraints check):\n"
                    + imports
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
            "2. Read each changed source and test file — see the 'Test file location' bullets under constitution §5 (Test-Driven Development) for where test files live in this project\n"
            "3. Validate against every rule below"
        )
    else:
        file_input = f"--- CHANGED SOURCE FILES (git diff main...HEAD) ---\n{changed_files_section}"

    tail = (
        "- status is FAIL if any violation is BLOCKING\n"
        "- status is PASS only if zero BLOCKING violations"
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
styling, type safety, and CI readiness. Read and apply every section. This project's
constitution.md is human-customized, so its section numbers may not match any numbers
used below — when a rule below references a constitution/architecture section, locate it
by heading text (e.g. "Stack Constraints") rather than assuming the number lines up.

For violations derived from the memory documents, use the section heading as the rule
label, e.g. "Constitution — Stack Constraints", "Architecture — Layer Separation".

Harness process checks (apply in addition to the above):

§I1 Task Traceability [BLOCKING]: every changed source file corresponds to a path or component listed in tasks.md; no phantom files
§I7 Spec Compliance [BLOCKING]: implemented behaviour covers every acceptance criterion in spec.md; nothing added beyond what spec.md requires

Stack Constraints — detection method: check IMPORT STATEMENTS ONLY (use the pre-extracted "Imports in this file" lists above) against the approved stack table in the constitution's Stack Constraints section. A violation exists ONLY IF an import line is for a package that is clearly outside that approved list — an import of the project's own approved framework/library, or its standard usage patterns, is NEVER a violation even if the import name resembles a class or route method name. If the pre-extracted import list shows no packages outside the approved stack, this check PASSES — do not add it to violations.

Architecture — Layer Separation: a violation requires application code to directly bypass a layer boundary declared in architecture.md or constitution.md (e.g. raw database queries or client instantiation, or embedded business logic, inside a layer that document designates as thin routing/presentation only). Mounting/registering sub-routers, calling a helper function or service, and imports between project files are NEVER violations on their own — only cite a violation when the specific boundary crossed is named in the embedded ARCHITECTURE/CONSTITUTION documents.

§I7 Spec Compliance — detection method: use the pre-extracted "Routes in this file" lists to compare implemented route paths against spec acceptance criteria. A §I7 violation exists when a route path, HTTP method, or response shape does not match what spec.md requires.

Evidence standard — before adding any item to the violations array you MUST:
- Quote the exact line(s) that constitute the violation AND name the specific prohibited behavior (e.g. "imports a package outside the approved stack" for Stack Constraints, "implements /status but spec requires /health" for §I7)
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
    def _build(spec_dir: Path, iteration: int) -> str:
        constitution_path = Path(".specify/memory/constitution.md")
        architecture_path = Path(".specify/memory/architecture.md")
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"
        tasks_path = spec_dir / "tasks.md"
        data_model_path = spec_dir / "data-model.md"

        require_files("ch-4-implement-critic", constitution_path, spec_path, plan_path, tasks_path)

        constitution = constitution_path.read_text(encoding="utf-8")
        architecture = read_optional(architecture_path, "(architecture.md not found)")
        spec = spec_path.read_text(encoding="utf-8")
        plan = plan_path.read_text(encoding="utf-8")
        tasks = tasks_path.read_text(encoding="utf-8")
        contracts = read_contracts(spec_dir)
        data_model = read_optional(data_model_path, "(data-model.md not found)")

        changed_sources = read_changed_source_files(get_changed_files())
        changed_sources = _annotate_source_files(changed_sources)

        return build_implement_critic_prompt(
            constitution,
            spec,
            plan,
            tasks,
            iteration,
            architecture=architecture,
            contracts=contracts,
            data_model=data_model,
            changed_files_section=changed_sources,
        )

    run_local_critic_cli("implement", CRITIC_RESULT_PREFIX, _build)


if __name__ == "__main__":
    main()
