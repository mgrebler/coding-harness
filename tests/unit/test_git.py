"""Unit tests for agent_common/git.py.

get_feature_from_branch and run_auto_commit remain only indirectly
exercised (mocked out) by tests in test_critic_loop.py. resolve_base_ref
and get_changed_files get direct coverage here, added alongside the
FOLLOWUP_HARNESS.md Bug 2 fix (stale local `main` silently widening
critic-prompt diffs).
"""

import sys
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
    def test_diffs_against_origin_main_when_available(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                return _result(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _result(returncode=0)
            if cmd[:2] == ["git", "diff"]:
                if cmd[2] != "origin/main...HEAD":
                    raise AssertionError(f"expected diff against origin/main...HEAD, got: {cmd}")
                return _result(returncode=0, stdout="a.py\nb.py\n")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch.object(git.subprocess, "run", side_effect=fake_run):
            self.assertEqual(git.get_changed_files(), ["a.py", "b.py"])

    def test_falls_back_to_local_main_when_origin_main_unavailable(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                return _result(returncode=1)
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _result(returncode=1)
            if cmd[:2] == ["git", "diff"]:
                if cmd[2] != "main...HEAD":
                    raise AssertionError(f"expected diff against main...HEAD, got: {cmd}")
                return _result(returncode=0, stdout="c.py\n")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch.object(git.subprocess, "run", side_effect=fake_run):
            self.assertEqual(git.get_changed_files(), ["c.py"])

    def test_blank_lines_filtered(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                return _result(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _result(returncode=0)
            if cmd[:2] == ["git", "diff"]:
                return _result(returncode=0, stdout="a.py\n\n  \nb.py\n")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch.object(git.subprocess, "run", side_effect=fake_run):
            self.assertEqual(git.get_changed_files(), ["a.py", "b.py"])


if __name__ == "__main__":
    unittest.main()
