---
name: ch-docs-bootstrap
description: Generate the initial docs/ tree for a project that's adopting the coding-harness documentation pipeline mid-project, by analysing the current codebase as a whole rather than a single feature's delta. Run once per project; ch-5-docs-auto takes over incrementally for every feature after.
user-invocable: true
---

# Docs Bootstrap

Generate the initial `docs/` tree for a project that already has code but no `docs/` yet —
adopting the harness's documentation stage mid-project rather than from feature #1. This is a
**one-time, whole-codebase** survey, not a feature delta: there is no `spec.md`/`plan.md`/
`tasks.md` and no `git diff` to read, because the content to document is everything already
built, not one feature's change.

After this runs once, `/ch-5-docs-auto` maintains `docs/` incrementally for every feature from
then on — this skill only ever needs to run again if `docs/` is deliberately thrown away and
rebuilt from scratch.

This skill does not run a critic loop and does not auto-commit. It generates files and hands off
to you for review, the same way `/speckit-constitution` does.

---

## Pre-flight

1. Check `.specify/memory/documentation-principles.md` exists. If it doesn't, STOP and tell the
   user: this project hasn't installed the docs pipeline stage yet — run `./install.sh` (or pull
   and re-run install for an already-installed project) before using this skill. Do not improvise
   a taxonomy from scratch; the file is the source of truth for what `docs/` must look like.
2. Check whether `docs/` already exists.
   - If it does, STOP and ask the user to confirm before continuing: this skill is for a *first*
     adoption, not for projects already using `ch-5-docs-auto` — regenerating from scratch would
     discard an already-maintained tree. Only proceed past this point on explicit confirmation.
   - If the user confirms, treat this exactly like the "doesn't exist" case — the existing tree is
     not read for merging, it is fully replaced from a fresh survey of the codebase.

---

## Survey

Read, in this order, to build an accurate picture of what the project actually does today:

1. `README.md` (and any top-level `docs/`-like files if a confirmed overwrite is in progress)
2. `.specify/memory/product-context.md` and `.specify/memory/architecture.md`, if present — human
   context on product intent and structure
3. Package manifests (`package.json`, `pyproject.toml`, `go.mod`, etc.) — stack and entry points
4. The source tree via Glob/Grep — routes, CLI commands, UI screens, public APIs; whatever
   surfaces the product's actual user-facing and maintainer-facing capabilities
5. `specs/*/spec.md`, if any exist from prior harness use — corroborating context only, never the
   primary source; the codebase itself is what's authoritative, since a spec can describe
   something that was later changed or cut

Do not read git history or prior commits for this — `docs/` describes the current state of the
world, not how it got here (see `documentation-principles.md`'s "Currency Over History"
principle).

---

## Generate

Read `.specify/memory/documentation-principles.md` in full and follow its taxonomy and placement
rules exactly — do not restate a remembered or possibly-stale copy of them. In outline, build:

```
docs/
  index.md
  tutorials/
  how-to/
  reference/
  explanation/
  maintainer/
```

- `index.md` — what the product is, linking into every top-level file below it.
- `reference/` — one file per capability/domain actually found in the survey step. This is
  normally where most of the content lands.
- `how-to/` — one file per distinct end-to-end user-facing workflow the survey turned up.
- `explanation/` — only for genuinely cross-cutting concepts a user would need to understand; skip
  this folder entirely (just don't create files in it) if nothing in the survey warrants it.
- `tutorials/` — only if there's a clear first-use / getting-started path; skip otherwise.
- `maintainer/` — operational/internal detail (deployment, internal architecture notes) that
  isn't meant for end users of the product.

Document only what the survey step actually found implemented. Never invent capabilities, and
never document something a spec or README describes as planned/aspirational if the code doesn't
back it up — traceability to real, present code is what makes this trustworthy as a source of
truth from day one.

---

## Summary

When done, report to the user:
- The full list of files written under `docs/`.
- A clear recommendation to review the content before committing — this skill runs without a
  critic loop, so nothing has mechanically checked it.
- A suggested commit message, e.g. `docs: bootstrap initial documentation tree`.
- A note that `/ch-5-docs-auto` will now incrementally maintain this tree starting with the next
  feature that goes through the pipeline — this skill does not need to run again unless `docs/` is
  deliberately rebuilt from scratch.
