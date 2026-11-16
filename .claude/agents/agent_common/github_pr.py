"""Push/PR/comment helpers for ch_6_validate_auto.py, via `git` and `gh`.

These run in the orchestrator, never inside an agent: the validation agent
has no Bash, so the only GitHub-side effects of the stage are the ones made
here — a non-force push of the feature branch, a draft PR against main, its
description (until a human takes it over) with an issue-closing keyword, and
one report comment per commit."""

import json
import re
import subprocess

BASE_BRANCH = "main"
GENERATED_MARKER = "<!-- ch-6-validate-auto:generated-description -->"
# The fixed placeholder body PRs were opened with before descriptions were
# generated; a PR still carrying it was never edited by a human.
LEGACY_PLACEHOLDER = "Opened as a draft by ch-6-validate-auto so this branch gets a preview deploy"


class GitHubError(Exception):
    pass


def _run(cmd: list[str], input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, input=input)


def _gh(*args: str, input: str | None = None) -> str:
    result = _run(["gh", *args], input=input)
    if result.returncode != 0:
        raise GitHubError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def gh_authenticated() -> bool:
    return _run(["gh", "auth", "status"]).returncode == 0


def push_branch() -> None:
    """Push HEAD to its same-named branch on origin. Never forces: a rejected
    push means the remote branch has commits this checkout doesn't, which a
    human has to reconcile."""
    result = _run(["git", "push", "-u", "origin", "HEAD"])
    if result.returncode != 0:
        raise GitHubError(f"git push was rejected: {result.stderr.strip()}")


def find_pr(branch: str) -> dict | None:
    result = _run(["gh", "pr", "view", branch, "--json", "number,url,body,baseRefName"])
    if result.returncode == 0:
        return json.loads(result.stdout)
    if "no pull requests found" in result.stderr.lower():
        return None
    raise GitHubError(f"gh pr view failed: {result.stderr.strip()}")


def has_closing_keyword(body: str, issue: int) -> bool:
    return bool(re.search(rf"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#{issue}\b", body or ""))


def closing_line(issue: int) -> str:
    return f"Closes #{issue}"


def harness_owns_body(body: str) -> bool:
    """Whether a PR body is still one this stage wrote. A human takes it over
    by deleting the marker; a PR a human opened never has it."""
    return GENERATED_MARKER in (body or "") or LEGACY_PLACEHOLDER in (body or "")


def _with_closing_line(body: str, issue: int | None) -> str:
    if issue is None or has_closing_keyword(body, issue):
        return body
    return f"{body.rstrip()}\n\n{closing_line(issue)}"


def ensure_draft_pr(branch: str, title: str, body: str, issue: int | None) -> dict:
    """The branch's PR, creating it as a draft against main if none exists,
    and making sure its body closes issue (when there is one). An existing
    PR's body is replaced with body only while harness_owns_body; otherwise
    it is kept, gaining just the closing line if missing."""
    pr = find_pr(branch)
    if pr is None:
        body = _with_closing_line(body, issue)
        _gh(
            "pr", "create", "--draft", "--base", BASE_BRANCH, "--head", branch,
            "--title", title, "--body-file", "-",
            input=body,
        )  # fmt: skip
        pr = find_pr(branch)
        if pr is None:
            raise GitHubError(f"gh pr create succeeded but no PR is visible for {branch}")
        return pr
    current = pr["body"] or ""
    wanted = _with_closing_line(body if harness_owns_body(current) else current, issue)
    if wanted != current:
        pr["body"] = wanted
        _gh("pr", "edit", str(pr["number"]), "--body-file", "-", input=wanted)
    return pr


def post_comment_once(pr_number: int, body: str, marker: str) -> bool:
    """Post body as a PR comment unless one containing marker already exists,
    so a resumed run never posts the same commit's report twice. Returns
    whether a comment was posted."""
    existing = json.loads(_gh("pr", "view", str(pr_number), "--json", "comments"))
    if any(marker in c.get("body", "") for c in existing.get("comments", [])):
        return False
    _gh("pr", "comment", str(pr_number), "--body-file", "-", input=body)
    return True
