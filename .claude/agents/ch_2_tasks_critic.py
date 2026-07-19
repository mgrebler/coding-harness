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

from pathlib import Path

from agent_common.files import require_files
from agent_common.ollama import run_local_critic_cli
from agent_common.preflight_checks import analyze_task_format as _analyze_task_format

CRITIC_RESULT_PREFIX = "ch-2-tasks-critic-result"

# Task-line classification (checkbox/ID/format regexes) lives in
# agent_common/preflight_checks.py — it's the single source of truth shared
# with the deterministic pre-critic gate in ch_2_tasks_auto.py, so the
# critic's contextual analysis and the hard gate never drift out of sync.


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
§T3 Test/Impl Pairing [BLOCKING]: apply Constitution §5 ("Non-deliverable tasks") and §6 ("Pairing scope") exactly as written — do not apply a stricter or looser pairing rule than what those sections state. In particular:
  - A task with no testable deliverable (orientation, read-and-confirm) needs NO [TEST]/[IMPL] tag at all — do not flag it either for lacking a tag or, in a later iteration, for having no tag to remove. If a prior iteration's violation list (see violations_block above) already asked for a tag to be added or removed on a given task ID, do not flip that request in the other direction this iteration — re-read Constitution §5 first.
  - A [TEST] task's description may explicitly name more than one [IMPL] task it covers (documented N:1 e2e/integration pairing per §6) — this is NOT an orphan [IMPL] task and must not be flagged, provided the linkage is stated in tasks.md.
  - An [IMPL] task with genuinely no [TEST] task anywhere in tasks.md, and not covered by a documented N:1 [TEST] task, IS a violation — quote the missing linkage.
§T5 Task Format [BLOCKING]: every task entry must use the machine-readable format `- [ ] TXXX [TEST|IMPL] [PY] [USZ] description` (or `- [x] TXXX ...` for a completed task) — a markdown checkbox (`- [ ]` / `- [x]` / `- [X]`) immediately followed by a bare, unbracketed task ID (`T001`, not `[T001]`), per `.specify/templates/tasks-template.md`. This checkbox is not cosmetic: `ch_3_test_auto.py` and `ch_4_implement_auto.py` scan for the literal substring `- [ ]` to detect which tasks still need work, so a task line missing the checkbox (e.g. `- [T001] [IMPL] ...` with the ID merely bracketed instead) is silently treated as already complete and skipped forever. Tasks written as numbered lists (1. 2. 3.), plain bullet points, bracketed-ID-without-checkbox (`- [T001] ...`), or free-form prose without `[TEST]`/`[IMPL]` labels ALL violate §T5.
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
