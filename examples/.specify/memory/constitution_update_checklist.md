# Constitution Update Checklist

This file logs all amendments to `constitution.md`, newest first. Each entry records the decision, the rationale, and who approved it. Required by the constitution's preamble before any amendment takes effect.

Each amendment touches three records, each with a distinct job:

- `constitution.md` — the rule itself, plus the version bump and a one-line SYNC IMPACT REPORT pointer to this file, both written by `/speckit-constitution`
- `constitution-decision-log.md` — the one-line summary row
- this file — the full before/after text, impact on existing features, and the downstream actions that must be completed before the amendment is fully landed

Copy the template entry below for each new amendment, numbered sequentially.

---

## Amendment NNN — [DATE]

**Section amended**: §N ([Section name])
**Approved by**: Human ([name]) — [how approval was given, e.g. after reviewing the plan.md Complexity Tracking for feature NNN]
**Context**: [What surfaced the need for the amendment — e.g. which feature's Constitution Check flagged a conflict, and why the human chose to amend rather than change the feature.]

**Before**:
> `constitution.md §N`: [quoted text before the amendment]

**After**:
> `constitution.md §N`: [quoted text after the amendment]

**Impact on existing features**: [Which specs/plans/code are affected, or "None".]

**Downstream actions required**:
- [ ] `constitution.md §N` updated
- [ ] `constitution.md` SYNC IMPACT REPORT pointer replaced + version bump (vX.Y.Z → vX.Y.Z, MAJOR/MINOR/PATCH)
- [ ] Templates synced (e.g. `plan-template.md` Constitution Check rows), or ⚠ pending with paths
- [ ] `constitution-decision-log.md` entry added
- [ ] `constitution_update_checklist.md` entry added (this entry)
- [ ] [Any affected `specs/NNN-feature/` artifacts to revise, or pipeline stage to resume]
