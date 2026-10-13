---
name: "ch-specify"
description: "Create or update the feature specification from a natural language feature description, grounded in the existing docs/ tree when present."
argument-hint: "Describe the feature you want to specify"
compatibility: "Requires spec-kit project structure with .specify/ directory"
metadata:
  author: "coding-harness"
  source: "coding-harness (local extension of speckit-specify; not sourced from upstream spec-kit)"
user-invocable: true
disable-model-invocation: false
---


## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Outline

1. **Read `docs/` for current-state grounding, if it exists.** Check whether a `docs/` directory exists at the project root.
   - If it exists: read `docs/index.md` and any `docs/reference/`/`docs/explanation/` pages that look relevant to the feature description below. Per `.specify/memory/documentation-principles.md`, `docs/` is kept current with what has actually shipped — unlike `spec.md`/`plan.md` history, it is never a changelog, so it's safe to treat as ground truth for "what does the product currently do."
   - Form a working judgment: is the feature described below **genuinely new capability**, or **a change to behaviour already documented in `docs/`**? Note which documented behaviour, if any, it changes.
   - If `docs/` doesn't exist (not yet bootstrapped via `/ch-docs-bootstrap`, or this project has `stages.docs.enabled: false` in `.specify/pipeline.json`), skip this step and proceed exactly as `/speckit-specify` would on its own.

2. **Delegate to `/speckit-specify`.** Read `.claude/skills/speckit-specify/SKILL.md` now and follow its Pre-Execution Checks and Outline sections exactly as currently written, inline in this conversation — do NOT invoke the Skill tool (doing so would transfer control away and the spec would never be written). The feature description is the text the user typed after `/ch-specify` (i.e. `$ARGUMENTS` above) — use it exactly as that file's own Outline expects "the feature description" to be used.

   Add one augmentation on top of that file's own steps: wherever its Outline extracts key concepts from the description (its step 5.2: "Identify: actors, actions, data, constraints") or generates Functional Requirements (its step 5.5), also incorporate the judgment formed in Step 1 above — a requirement that changes behaviour already documented in `docs/` should say so explicitly (e.g. "Changes the existing behaviour where X..." rather than reading as if X never existed before this feature).

   Always re-read `.claude/skills/speckit-specify/SKILL.md` fresh rather than relying on a cached memory of its structure, so this delegation stays correct even if that file's steps are renumbered or changed later.

3. **Report completion.** Report exactly what the delegated flow reports — no override of its "next steps" messaging. This command is a docs-aware variant of the manual `/speckit-specify` entry point, not part of the `ch-*-auto` automated-pipeline family, so its guidance toward `/speckit-clarify` or `/speckit-plan` (or, for this harness, `/ch-1-plan-auto`) stays as-is.
