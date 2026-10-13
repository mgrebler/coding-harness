---
name: ch-5-docs-auto
description: Runs the automated user-documentation generation and critic loop for the current feature branch by invoking ch_5_docs_auto.py. Merges this feature's shipped behaviour into docs/, the project's persistent documentation set, then runs iterative critic review, fixes, and escalation. Run manually after reviewing the implementation.
user-invocable: true
---

# Docs Auto-Orchestrator

Run the automated documentation merge and review loop for the current feature branch.

All orchestration logic lives in `.claude/agents/ch_5_docs_auto.py`. This skill is a thin
invocation wrapper — do not re-implement the loop here.

---

## Execution

Run from the repo root:

```bash
python .claude/agents/ch_5_docs_auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/ch_5_docs_auto.py --feature 015-job-description-rich-text
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

1. Validates pre-flight conditions (spec.md, plan.md, tasks.md exist; the ch-4-implement stage is complete)
2. Snapshots the pre-merge `docs/` tree to `specs/$FEATURE/ch-5-docs-baseline/` (once per feature — never overwritten on resume)
3. Runs the docs agent to merge this feature's shipped behaviour into `docs/` — bootstrapping the full Diátaxis structure (`index.md`, `tutorials/`, `how-to/`, `reference/`, `explanation/`, `maintainer/`) if `docs/` doesn't exist yet, or merging in place if it does
4. Runs an iterative single-gate review loop (up to 3 iterations):
   - **Docs critic**: validates correct Diátaxis placement, merge accuracy against the shipped diff, no regression against the pre-merge baseline, internal consistency, staleness, cross-audience leakage, and structural completeness — per `.specify/memory/documentation-principles.md`
5. Runs the revision agent between iterations to address any blocking violations
6. On PASS: triggers auto-commit via the git extension
7. On 3-iteration exhaustion: writes `specs/$FEATURE/ch-5-docs-critic-escalation.md` and exits non-zero

The script is resume-safe: re-running after an interruption continues from the last
incomplete step using result files as idempotency markers.

---

## What this skill does not do

- Does not touch code, specs, plans, or tasks — its only output is `docs/`
- Does not implement critic logic — that lives in `ch_5_docs_auto.py` and its subagents
- Does not push to remote or open a pull request
