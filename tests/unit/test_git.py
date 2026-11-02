"""Unit tests for agent_common/git.py.

get_feature_from_branch and run_auto_commit remain only indirectly
exercised (mocked out) by tests in test_critic_loop.py. resolve_base_ref
and get_changed_files get direct coverage here (the latter against a real repo), added alongside the
FOLLOWUP_HARNESS.md Bug 2 fix (stale local `main` silently widening
critic-prompt diffs).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import git


def _result(returncode=0, stdout=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


class TestRefExists(unittest.TestCase):
    def test_true_when_rev_parse_succeeds(self):
        with patch.object(git.subprocess, "run", return_value=_result(returncode=0)):
            self.assertTrue(git._ref_exists("origin/main"))

    def test_false_when_rev_parse_fails(self):
        with patch.object(git.subprocess, "run", return_value=_result(returncode=1)):
            self.assertFalse(git._ref_exists("origin/main"))


class TestResolveBaseRef(unittest.TestCase):
    def test_prefers_origin_ref_when_it_exists(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["git", "fetch"]:
                return _result(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _result(returncode=0)
            raise AssertionError(f"unexpected command: {cmd}")

        with patch.object(git.subprocess, "run", side_effect=fake_run):
            self.assertEqual(git.resolve_base_ref("main"), "origin/main")
        self.assertIn(["git", "fetch", "origin", "main"], calls)

    def test_falls_back_to_local_branch_when_origin_ref_unavailable(self):
        """Covers both a stale-but-present origin/main-less clone and a fetch
        failure (offline, no 'origin' remote) — fetch is best-effort (no
        check=True), so a non-zero fetch return code alone never raises;
        only rev-parse --verify on origin/<branch> decides the fallback."""

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                return _result(returncode=1)
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _result(returncode=1)
            raise AssertionError(f"unexpected command: {cmd}")

        with patch.object(git.subprocess, "run", side_effect=fake_run):
            self.assertEqual(git.resolve_base_ref("main"), "main")


class TestGetChangedFiles(unittest.TestCase):
    """Against a real throwaway repo: a feature branch off main with one
    committed, one modified-but-uncommitted, and one untracked file."""

    def setUp(self):
        self._orig_cwd = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        for cmd in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(cmd, check=True)
        Path("existing.py").write_text("v1\n")
        Path(".gitignore").write_text("ignored.log\n")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "base")
        self._git("checkout", "-q", "-b", "feature")
        Path("committed.py").write_text("x\n")
        self._git("add", "committed.py")
        self._git("commit", "-q", "-m", "feature work")

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self._tmp.cleanup()

    def _git(self, *args):
        subprocess.run(["git", *args], check=True)

    def test_includes_committed_uncommitted_and_untracked_changes(self):
        """specs/086: the test agent hadn't committed, so both local test
        critics found "nothing to review"."""
        Path("existing.py").write_text("v2\n")
        Path("tests").mkdir()
        Path("tests/new_test.py").write_text("t\n")
        Path("ignored.log").write_text("noise\n")
        self.assertEqual(
            sorted(git.get_changed_files()), ["committed.py", "existing.py", "tests/new_test.py"]
        )

    def test_branch_diff_includes_uncommitted_edits_to_an_existing_file(self):
        Path("existing.py").write_text("v2\n")
        self.assertIn("+v2", git.branch_diff("existing.py"))
        self.assertIsNone(git.branch_diff("committed.py"))


if __name__ == "__main__":
    unittest.main()
