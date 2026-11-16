"""Unit tests for agent_common/github_pr.py with git/gh mocked: the draft PR is
created against main with the issue-closing keyword, an existing PR gains the
keyword only when missing, its body is replaced only while the harness owns it, and a report comment is posted once per commit."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import github_pr


def _done(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class _FakeGh:
    """Records every command; answers `gh pr view` from self.pr."""

    def __init__(self, pr=None, comments=()):
        self.pr = pr
        self.comments = list(comments)
        self.calls = []

    def __call__(self, cmd, input=None):
        self.calls.append((cmd, input))
        if cmd[:3] == ["gh", "pr", "view"] and "comments" in cmd:
            return _done(stdout=json.dumps({"comments": [{"body": c} for c in self.comments]}))
        if cmd[:3] == ["gh", "pr", "view"]:
            if self.pr is None:
                return _done(1, stderr='no pull requests found for branch "x"')
            return _done(stdout=json.dumps(self.pr))
        if cmd[:3] == ["gh", "pr", "create"]:
            self.pr = {"number": 5, "url": "u", "body": input, "baseRefName": "main"}
        return _done()

    def commands(self, prefix):
        return [(c, i) for c, i in self.calls if c[: len(prefix)] == prefix]


class TestEnsureDraftPr(unittest.TestCase):
    def test_creates_draft_against_main_with_closing_keyword(self):
        gh = _FakeGh()
        with patch.object(github_pr, "_run", gh):
            pr = github_pr.ensure_draft_pr("012-x", "Title", "Body", 42)
        [(cmd, body)] = gh.commands(["gh", "pr", "create"])
        self.assertIn("--draft", cmd)
        self.assertEqual(cmd[cmd.index("--base") + 1], "main")
        self.assertTrue(body.endswith("Closes #42"))
        self.assertEqual(pr["number"], 5)

    def test_no_issue_means_no_keyword(self):
        gh = _FakeGh()
        with patch.object(github_pr, "_run", gh):
            github_pr.ensure_draft_pr("012-x", "Title", "Body", None)
        [(_, body)] = gh.commands(["gh", "pr", "create"])
        self.assertEqual(body, "Body")

    def test_existing_pr_gains_missing_keyword(self):
        gh = _FakeGh(pr={"number": 3, "url": "u", "body": "Human text", "baseRefName": "main"})
        with patch.object(github_pr, "_run", gh):
            github_pr.ensure_draft_pr("012-x", "Title", "Body", 42)
        [(cmd, body)] = gh.commands(["gh", "pr", "edit"])
        self.assertEqual(cmd[3], "3")
        self.assertEqual(body, "Human text\n\nCloses #42")
        self.assertEqual(gh.commands(["gh", "pr", "create"]), [])

    def test_existing_keyword_is_left_alone(self):
        gh = _FakeGh(pr={"number": 3, "url": "u", "body": "Fixes #42", "baseRefName": "main"})
        with patch.object(github_pr, "_run", gh):
            github_pr.ensure_draft_pr("012-x", "Title", "Body", 42)
        self.assertEqual(gh.commands(["gh", "pr", "edit"]), [])

    def test_harness_owned_body_is_replaced_keeping_keyword(self):
        for old in (f"Old\n{github_pr.GENERATED_MARKER}", f"x {github_pr.LEGACY_PLACEHOLDER} y"):
            with self.subTest(old):
                gh = _FakeGh(pr={"number": 3, "url": "u", "body": old, "baseRefName": "main"})
                with patch.object(github_pr, "_run", gh):
                    github_pr.ensure_draft_pr("012-x", "Title", "New", 42)
                [(_, body)] = gh.commands(["gh", "pr", "edit"])
                self.assertEqual(body, "New\n\nCloses #42")

    def test_unchanged_harness_body_is_not_edited(self):
        body = f"Same\n{github_pr.GENERATED_MARKER}"
        gh = _FakeGh(pr={"number": 3, "url": "u", "body": body, "baseRefName": "main"})
        with patch.object(github_pr, "_run", gh):
            github_pr.ensure_draft_pr("012-x", "Title", body, None)
        self.assertEqual(gh.commands(["gh", "pr", "edit"]), [])

    def test_view_failure_other_than_no_pr_raises(self):
        with (
            patch.object(github_pr, "_run", return_value=_done(1, stderr="HTTP 401")),
            self.assertRaises(github_pr.GitHubError),
        ):
            github_pr.find_pr("012-x")


class TestClosingKeyword(unittest.TestCase):
    def test_variants(self):
        for body in ("Closes #4", "fixed #4", "Resolves #4 and more"):
            self.assertTrue(github_pr.has_closing_keyword(body, 4), body)
        for body in ("Closes #42", "see #4", ""):
            self.assertFalse(github_pr.has_closing_keyword(body, 4), body)


class TestPostCommentOnce(unittest.TestCase):
    def test_posts_when_marker_absent(self):
        gh = _FakeGh(comments=["unrelated"])
        with patch.object(github_pr, "_run", gh):
            self.assertTrue(github_pr.post_comment_once(5, "<!-- m -->\nreport", "<!-- m -->"))
        [(_, body)] = gh.commands(["gh", "pr", "comment"])
        self.assertEqual(body, "<!-- m -->\nreport")

    def test_skips_when_marker_present(self):
        gh = _FakeGh(comments=["<!-- m -->\nold report"])
        with patch.object(github_pr, "_run", gh):
            self.assertFalse(github_pr.post_comment_once(5, "<!-- m -->\nreport", "<!-- m -->"))
        self.assertEqual(gh.commands(["gh", "pr", "comment"]), [])


class TestPushBranch(unittest.TestCase):
    def test_never_forces_and_raises_on_rejection(self):
        with (
            patch.object(github_pr, "_run", return_value=_done(1, stderr="rejected")) as run,
            self.assertRaises(github_pr.GitHubError),
        ):
            github_pr.push_branch()
        cmd = run.call_args[0][0]
        self.assertEqual(cmd, ["git", "push", "-u", "origin", "HEAD"])


if __name__ == "__main__":
    unittest.main()
