---
name: ch-6-validate-auto
description: Validates the current feature branch on a deployed preview by invoking ch_6_validate_auto.py. Pushes the branch, opens or reuses its draft PR, waits for the preview deploy, has an agent drive a recorded browser through the spec's acceptance scenarios, success criteria and quickstart, and posts the report to the PR. Opt-in; run manually after reviewing the docs.
user-invocable: true
---

# Validate Auto-Orchestrator

Validate the current feature branch's HEAD commit on its deployed preview.

All orchestration logic lives in `.claude/agents/ch_6_validate_auto.py`. This skill is a thin
invocation wrapper — do not re-implement the stage here.

---

## Execution

Run from the repo root:

```bash
python .claude/agents/ch_6_validate_auto.py
```

The script derives the feature from the current git branch automatically.
To target a specific feature, pass `--feature <name>`:

```bash
python .claude/agents/ch_6_validate_auto.py --feature 015-job-description-rich-text
```

Wait for the script to complete and relay its output to the user.

---

## What the script does

1. Does nothing unless `.specify/pipeline.json` sets `stages.validate.enabled: true` and has a
   `validation` block (see README § Deployed Validation)
2. Validates pre-flight conditions (implement and docs stages complete, clean working tree,
   `gh` authenticated, validation generation provider is Claude, and this feature's fixtures
   present when `fixtures_dir` is set, unless `fixtures_guide` lets the stage draft them, after
   which it checks and commits them before choosing the commit to validate)
3. Pushes the feature branch (never forced) and finds or opens its **draft** PR against `main`,
   adding `Closes #N` when the spec came from an issue
4. Runs the project's `deploy_command`, which prints this commit's preview URL — inside the
   project's `lock_command`/`unlock_command`, when set, for a shared environment
5. Runs the project's `auth_command`, which writes a logged-in Playwright session file, then
   the optional `seed_command`, which creates data a browser can't (e.g. an inbound email)
6. Has a validation agent drive a recorded browser (Playwright MCP) through every acceptance
   scenario, success criterion and quickstart section, writing one verdict per scenario. It
   first checks the preview is fit to validate on; if not, the stage escalates "Validation
   Environment Not Ready" without attempting any scenario
7. Checks the report deterministically (every scenario covered, recordings on disk), retrying
   the agent once if it is incomplete
8. When `probe_commands` is set, has an operator agent settle BLOCKED scenarios with those
   commands, keeping only verdicts backed by a probe transcript
9. Posts the report as a PR comment (once per commit)
10. All PASS: writes `ch-6-validate-auto-complete` for this commit. Any FAIL/BLOCKED: writes
   `specs/$FEATURE/ch-6-validate-escalation.md` and exits non-zero

Everything the stage writes is gitignored and local: recordings, traces and the report live in
`specs/$FEATURE/validation/<sha12>/`. Re-running on an already-validated commit is a no-op; a new
commit is validated from scratch.

---

## What this skill does not do

- Does not fix code, commit, or merge — it is report-only
- Does not mark the PR ready for review — a human does that after watching the recordings
- Does not upload recordings anywhere — the PR comment lists their local paths
