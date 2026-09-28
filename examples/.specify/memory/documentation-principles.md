# Documentation Principles

**Authoritative reference for all user-documentation decisions in this project.**

Read by the Documentation Agent when merging a feature's shipped behaviour into `docs/`, and by the Docs Critic when evaluating that merge. Both agents use the same source — the merge aims for the same bar it is reviewed against.

`docs/` is **the current state of the world**, not a changelog. It describes what the product does *today*, for two audiences at once: a real user trying to use the product, and a maintainer who needs a single source of truth for what's actually implemented. It is never a record of what changed and when — that history already lives in `specs/` and git. A dated entry, a "new in this release" note, or a reference to a spec/plan/task ID anywhere outside `maintainer/` is a defect in the documentation, not a legitimate note.

---

## Structure

`docs/` is organised using the Diátaxis framework — four categories, each with a distinct job, plus one addition for maintainer-only content:

- **`index.md`** — entry point. What the product is, and links into everything below.
- **`tutorials/`** — learning-oriented. Guided first-use walkthroughs. Touched rarely — only when a feature materially changes the first-use path.
- **`how-to/`** — task-oriented. "How to do X" guides, one file per user-facing workflow.
- **`reference/`** — information-oriented. One file per capability/domain — factual, exhaustive, no narrative. This is where most features land.
- **`explanation/`** — understanding-oriented. Concepts and *why* things behave as they do, from a user's perspective.
- **`maintainer/`** — operational/internal detail relevant to maintainers but not end users. Kept structurally separate so it never leaks into a user-facing page.

### Placement rules

Apply these when merging a feature's shipped behaviour:

- New or changed capability → add or revise the matching `reference/<capability-slug>.md`.
- New end-to-end user-facing workflow → add `how-to/<task-slug>.md`.
- Genuinely new cross-cutting concept a user would need to understand → add or revise an `explanation/` file.
- New onboarding-relevant capability that changes the first-use path → revise `tutorials/`.
- Operationally-relevant detail not meant for end users (internal IDs, ops behaviour, implementation-internal rationale) → `maintainer/`.
- Removed or replaced functionality → delete or rewrite the affected file(s). An emptied-out capability file is deleted, not left as a stub.
- Any time a new top-level file is added, `index.md` must link to it.

Each file should open with a single `# Title` (or minimal frontmatter with a `title:` field) so the set can be fed into a docs-site generator later without restructuring.

---

## Automatic FAIL Conditions

Immediately FAIL a review if any of the following exist.

### Misplacement
- Task-oriented content ("how do I...") inside `reference/`
- Capability/domain facts inside `how-to/` or `tutorials/`
- Conceptual/"why" material inside `reference/` instead of `explanation/`

### Merge Accuracy Failures
- A capability this feature added or changed has no corresponding new/updated file
- The doc set describes behaviour absent from this feature's spec or shipped diff (hallucinated functionality)

### Regression Against Baseline
- Content present in the pre-merge tree that this feature didn't touch is missing or altered without cause
- A file was silently deleted rather than deliberately removed for a stated reason

### Internal Consistency Failures
- Two files describe the same capability with contradictory details
- `index.md` is missing a link to a file that should be discoverable

### Staleness
- A reference to functionality this feature removed or renamed remains anywhere in the tree

### Cross-Audience Leakage
- Internal spec/plan/task IDs, internal file paths, or ops-only detail appear outside `maintainer/`
- Maintainer-only jargon appears in a user-facing file

### Structural Failures
- Dated entries, "new in this release" phrasing, or changelog-style framing anywhere in the tree
- Orphaned files not linked from `index.md` or any category listing

---

## Severity Rules

FAIL if:
- Any Critical/BLOCKING violation exists
- The merge omits a shipped capability's documentation entirely
- The merge introduces a regression against the pre-merge baseline

PASS only if:
- Every shipped capability change is documented in the correct category
- The pre-existing tree remains intact except for deliberate, correct revisions
- No internal artifact leaks outside `maintainer/`
- `index.md` stays a complete, accurate map of the tree

---

## Core Principles

### 1. Currency Over History

The doc set always describes today's behaviour. It never narrates what used to be true, what changed, or when. History belongs in `specs/` and git, not here.

### 2. Single Source of Truth

There is exactly one place a given fact about the product's behaviour lives. Don't duplicate the same capability description across files — if two files would need to say the same thing, one of them is wrong.

### 3. Category Discipline

Every piece of content has exactly one correct Diátaxis category based on what job it does for the reader (learn / do a task / look up a fact / understand a concept), not on convenience of where it was easiest to write.

### 4. Dual-Audience Separation

User-facing content assumes no internal knowledge of the codebase, specs, or task IDs. Maintainer-facing content is deliberately segregated into `maintainer/` so the two audiences never have to filter each other's content out.

### 5. Traceability to Shipped Behaviour

Everything documented must be backed by what's actually implemented — this feature's diff and spec, or something already in the codebase. Never document aspirational or planned behaviour.

### 6. Minimal Necessary Detail

Document what a user or maintainer actually needs to know to use or maintain the product. Avoid restating implementation detail that belongs in code comments or commit history.

---

## Heuristics

Automatically flag:

- A capability file with no corresponding shipped code
- A `how-to/` guide that's really a feature list (belongs in `reference/`)
- A `reference/` file with step-by-step imperative instructions (belongs in `how-to/`)
- Dated headers or "as of version X" phrasing
- Embedded spec/plan/task IDs outside `maintainer/`
- A file with no inbound link from `index.md` or a category listing
- Two files describing the same capability with different terminology
- A capability file left behind after its capability was removed
