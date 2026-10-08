#!/usr/bin/env python3
"""
.claude/agents/ch_5_docs_critic.py

Self-contained docs critic that runs against a local Ollama LLM.
Called by ch_5_docs_auto.py when local LLM is configured for the "docs" critic.

Usage:
  python3 .claude/agents/ch_5_docs_critic.py [--feature 012-my-feature] [--iteration 2]

Exit codes:
  0 - success: result file written
  1 - runtime error
  2 - local LLM not configured (caller should fall back to Claude)
"""

from pathlib import Path

from agent_common.files import (
    format_directory_tree,
    read_changed_source_files,
    read_directory_tree,
    require_files,
)
from agent_common.git import get_changed_files
from agent_common.ollama import run_local_critic_cli

CRITIC_RESULT_PREFIX = "ch-5-docs-critic-result"


def build_docs_critic_prompt(
    constitution: str,
    documentation_principles: str,
    spec: str,
    plan: str,
    tasks: str,
    iteration: int,
    violations_block: str = "",
    baseline_tree: str | None = None,
    new_tree: str | None = None,
    changed_files_section: str | None = None,
    output_instructions: str = "",
) -> str:
    """
    Build the docs critic prompt.

    baseline_tree:
      None  → include instructions to read specs/$FEATURE/ch-5-docs-baseline/ with tools (Claude path)
      str   → embed pre-fetched content (local LLM path)

    new_tree:
      None  → include instructions to read docs/ with tools (Claude path)
      str   → embed pre-fetched content (local LLM path)

    changed_files_section:
      None  → include git diff instructions (Claude path; agent reads files with tools)
      str   → embed pre-fetched content (local LLM path)
    """
    if baseline_tree is None:
        baseline_input = (
            "Baseline: read every file under specs/$FEATURE/ch-5-docs-baseline/ if it exists "
            "(this is the docs/ tree as it stood BEFORE this run's merge — absence means this "
            "is the first-ever docs run, so there is no baseline to regress against)."
        )
    else:
        baseline_input = f"--- BASELINE docs/ TREE (before this run's merge) ---\n{baseline_tree}"

    if new_tree is None:
        new_tree_input = (
            "Candidate: read every file under docs/ (the tree AFTER this run's merge) using Glob/Read."
        )
    else:
        new_tree_input = f"--- CANDIDATE docs/ TREE (after this run's merge) ---\n{new_tree}"

    if changed_files_section is None:
        file_input = (
            "Shipped diff: run `git fetch origin main --quiet` (ignore failure — offline/no remote), "
            "then `git diff origin/main...HEAD --name-only` if `origin/main` resolves, else "
            "`git diff main...HEAD --name-only`, and read each changed source file to confirm what "
            "this feature actually shipped."
        )
    else:
        file_input = f"--- SHIPPED SOURCE FILES (changed relative to base branch) ---\n{changed_files_section}"

    tail = (
        "- status is FAIL if any violation is BLOCKING\n"
        "- status is PASS only if zero BLOCKING violations\n"
        "- A rule you checked and found NOT broken is a clean pass: give it NO entry at all, or at "
        "most a not_applicable entry with a reason. Never add a 'violation' whose own finding says "
        "the rule was not broken, and never mark a non-violation as severity BLOCKING — that "
        "contradicts status and makes the result unusable."
    )
    if output_instructions:
        tail += f"\n{output_instructions}"

    return f"""You are the Docs Critic Agent for a spec-kit project.

Your sole function is to validate this run's merge into docs/ against the rules below.
You do not fix documentation. You do not write documentation. You identify violations only.
{violations_block}
Inputs already loaded for you:

--- CONSTITUTION ---
{constitution}

--- DOCUMENTATION PRINCIPLES ---
{documentation_principles}

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- TASKS ---
{tasks}

{baseline_input}

{new_tree_input}

{file_input}

Validate against ALL rules in the embedded DOCUMENTATION PRINCIPLES document above — it is the
authoritative source for docs/ structure (the Diátaxis category taxonomy: tutorials/, how-to/,
reference/, explanation/, maintainer/), placement rules, and automatic FAIL conditions. This
project's constitution.md is human-customized, so its section numbers may not match any numbers
used below — when a rule references a constitution section, locate it by heading text rather
than assuming the number lines up.

Harness process checks (apply in addition to DOCUMENTATION PRINCIPLES):

§D1 Correct Placement [BLOCKING]: new/changed content lives in the Diátaxis-appropriate category
per the Placement rules — e.g. a task-oriented "how do I..." guide is not buried inside reference/.

§D2 Merge Accuracy [BLOCKING]: every capability this feature added or changed (per spec.md and the
shipped diff) has a corresponding new-or-updated file in the CANDIDATE tree; nothing in the
CANDIDATE tree describes behaviour absent from the shipped diff/spec (hallucination guard).

§D3 No Regression vs. Baseline [BLOCKING]: every file in the BASELINE tree that this feature did
not need to touch is still present and unchanged in the CANDIDATE tree, or was deliberately and
correctly removed/rewritten for a reason traceable to this feature's diff — not silently dropped.

§D4 Internal Consistency [BLOCKING]: no two files in the CANDIDATE tree describe the same
capability with contradictory details.

§D5 Staleness [BLOCKING]: no file in the CANDIDATE tree references functionality this feature
removed or renamed, per the shipped diff.

§D6 Cross-Audience Leakage [BLOCKING]: no internal spec/plan/task IDs, internal file paths, or
maintainer-only operational detail appear outside maintainer/.

§D7 Structural Completeness [BLOCKING]: index.md links to every top-level file that should be
discoverable; no orphaned files; no dated/changelog-style entries anywhere in the tree.

Evidence standard — before adding any item to the violations array you MUST:
- Quote the exact file path and line(s) (or lack thereof) that constitute the violation, and name
  the specific rule broken (e.g. "reference/auth.md § Login describes a /session endpoint removed
  in this diff" for §D5)
- "finding" is a SHORT quoted excerpt (one or two lines) from a SINGLE file — never the full
  contents of a file, and never multiple files' content joined together. If a violation spans two
  files, pick the one file/line that best demonstrates it for "finding", and name the other file
  in "location" instead of quoting it too.
- "finding" must be valid inside a JSON string: plain text with internal newlines written as the
  `\n` escape sequence. Never use `+` or any other concatenation syntax inside the JSON value.
- If the excerpt you are quoting itself contains a double-quote character (e.g. it shows an
  example JSON body like {{"status": "ok"}}), every one of those double-quotes MUST be escaped as
  `\"` in your output. If you are not fully certain you can escape it correctly, paraphrase that
  part instead of quoting it verbatim (e.g. "a response body of status/ok" rather than copying the
  literal braces and quotes) — a correct paraphrase is far better than a quote that breaks the JSON.
- If the quoted content does not show a specific rule broken, it belongs in not_applicable, not violations
- If your analysis concludes "does not violate" or "no violation found", add it to not_applicable instead — do NOT put it in violations
- Never report a violation based on hypothetical future scenarios, content that might be added later, or conditions that "could" arise

Output ONLY valid JSON, no preamble, no markdown fences:
{{
  "iteration": {iteration},
  "status": "PASS or FAIL",
  "violations": [
    {{
      "rule": "<rule label, e.g. §D3 — No Regression vs. Baseline>",
      "severity": "BLOCKING or WARNING",
      "location": "<docs/ file path and section, e.g. docs/reference/auth.md § Login>",
      "finding": "<exact quoted content, or precise description of what's missing>"
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
        principles_path = Path(".specify/memory/documentation-principles.md")
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"
        tasks_path = spec_dir / "tasks.md"

        require_files(
            "ch-5-docs-critic", constitution_path, principles_path, spec_path, plan_path, tasks_path
        )

        constitution = constitution_path.read_text(encoding="utf-8")
        documentation_principles = principles_path.read_text(encoding="utf-8")
        spec = spec_path.read_text(encoding="utf-8")
        plan = plan_path.read_text(encoding="utf-8")
        tasks = tasks_path.read_text(encoding="utf-8")

        baseline_tree = format_directory_tree(
            read_directory_tree(spec_dir / "ch-5-docs-baseline"),
            empty_message="(no baseline — this is the first docs run for this project)",
        )
        new_tree = format_directory_tree(
            read_directory_tree(Path("docs")),
            empty_message="(docs/ is empty — nothing was written)",
        )
        changed_sources = read_changed_source_files(get_changed_files())

        return build_docs_critic_prompt(
            constitution,
            documentation_principles,
            spec,
            plan,
            tasks,
            iteration,
            baseline_tree=baseline_tree,
            new_tree=new_tree,
            changed_files_section=changed_sources,
        )

    run_local_critic_cli("docs", CRITIC_RESULT_PREFIX, _build)


if __name__ == "__main__":
    main()
