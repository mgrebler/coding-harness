---
name: "ch-specify-from-issue"
description: "Create or update the feature specification from a GitHub issue's title and body."
argument-hint: "GitHub issue number or URL to specify from (e.g. 42, #42, or a full issues URL)"
compatibility: "Requires spec-kit project structure with .specify/ directory; requires the gh CLI installed and authenticated"
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

1. **Validate the issue reference.** `$ARGUMENTS` must be either:
   - A bare issue number, optionally prefixed with `#` (e.g. `42`, `#42`).
   - A full GitHub issue URL (e.g. `https://github.com/<owner>/<repo>/issues/<number>`).

   Strip a leading `#` if present. If `$ARGUMENTS` is empty, non-numeric, or not a plausible GitHub issue URL, STOP and show:
   ```
   No issue number or URL provided. Usage: /ch-specify-from-issue <issue-number-or-url>, e.g. /ch-specify-from-issue 42
   ```
   Do not guess or attempt to interpret free text as an issue reference.

   Do **not** attempt to manually resolve owner/repo from the git remote here — `gh issue view` (Step 3) resolves the target repo itself: given a bare number it infers the repo from the local git remote, and given a full URL it extracts owner/repo/number directly from the URL. There is nothing to hand-parse or reconcile.

2. **Check `gh` is installed and authenticated.**
   ```bash
   command -v gh >/dev/null 2>&1
   ```
   If not found, STOP:
   ```
   `gh` (GitHub CLI) is not installed. Install it and re-run this command.
   See https://cli.github.com/ for installation instructions.
   ```
   Do not attempt to install it yourself.

   ```bash
   gh auth status
   ```
   If this exits non-zero, STOP:
   ```
   `gh` is installed but not authenticated. Run `gh auth login` in your terminal to
   authenticate interactively, then re-run `/ch-specify-from-issue <issue>`.
   ```
   Do NOT attempt to authenticate on the user's behalf — no token prompting, no reading `GH_TOKEN`/`GITHUB_TOKEN` as a workaround, no touching `~/.config/gh` directly. Detect, explain, stop.

3. **Fetch the issue.**
   ```bash
   gh issue view <number-or-url> --json number,title,body,url,state
   ```
   Pass the validated number/URL from Step 1 as-is — no `--repo` flag needed.

   If this exits non-zero (nonexistent issue, no repo context / no git remote, no access, or the reference is actually a PR number), STOP and surface `gh`'s own error text verbatim. Do not retry or fall back to guessing.

4. **Empty-body check.** If the fetched issue body is empty or whitespace-only, warn before proceeding (the resulting spec would be based on very little information):
   ```
   > [!WARNING]
   > Issue #<number> ("<title>") has no body — only a title. The resulting spec will be
   > based on very little information and may need significant manual clarification.
   ```
   Ask the user to confirm before continuing. If they decline, STOP and suggest either adding detail to the issue first or using `/speckit-specify` directly with a manually-typed description.

5. **Compose the feature description.** Build a single string from the fetched issue:
   ```
   <issue title>

   <issue body, verbatim>

   ---
   Source: GitHub issue #<number> — <url>
   ```
   This composed string is the effective feature description for the delegation in Step 6. When that delegated flow parses the description into actors/actions/constraints (per `speckit-specify`'s Outline step 5), treat the trailing `Source:` line as metadata only — do not let it leak into generated user stories or requirements.

6. **Delegate to `/speckit-specify`.** Read `.claude/skills/speckit-specify/SKILL.md` now and follow its Pre-Execution Checks and Outline sections exactly as currently written, inline in this conversation — do NOT invoke the Skill tool (doing so would transfer control away and the spec would never be written). Wherever that file refers to `$ARGUMENTS`, "the text the user typed after `/speckit-specify`", or "the feature description", substitute the composed string from Step 5 instead — never the raw text typed after `/ch-specify-from-issue` (that raw text is only the issue reference, not the description).

   Its own hook-check logic (`.specify/extensions.yml` → `hooks.before_specify` / `hooks.after_specify`) runs exactly as documented there; do not skip it and do not invent a separate hook namespace for this command.

   Always re-read `.claude/skills/speckit-specify/SKILL.md` fresh rather than relying on a cached memory of its structure, so this delegation stays correct even if that file's steps are renumbered or changed later.

7. **Report completion.** After the delegated flow reports completion (`SPECIFY_FEATURE_DIRECTORY`, `SPEC_FILE`, checklist summary, readiness for next phase), append one line:
   ```
   Sourced from: GitHub issue #<number> (<url>)
   ```
   If the issue's `state` was `CLOSED`, note that too, since specifying from a closed issue may be unintentional.
