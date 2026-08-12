"""Unit tests for agent_common/preflight_checks.py's commit-hygiene checks.

Focused coverage for oversized_committed_files(), added alongside the
FOLLOWUP_HARNESS.md Bug 2 fix: it independently reimplemented the same
"diff against main" computation as get_changed_files() (via merge-base +
diff instead of triple-dot), with its own hardcoded "main" default and no
staleness guard — now routed through the shared resolve_base_ref() helper
from agent_common.git (test_git.py covers that helper directly). No
broader coverage of this module is added here — task-format and red-state
checks remain untested, matching the pre-existing state of this module.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import preflight_checks as pc


def _result(returncode=0, stdout=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


class TestOversizedCommittedFiles(unittest.TestCase):
    def setUp(self):
        self._old_cwd = Path.cwd()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self._tmpdir.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmpdir.cleanup()

    def test_diffs_against_merge_base_with_resolved_origin_ref(self):
        """resolve_base_ref's origin/main preference should carry through to
        the merge-base call, not just get_changed_files()."""
        Path("big.bin").write_bytes(b"0" * 10)

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                return _result(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _result(returncode=0)  # origin/main resolves
            if cmd[:2] == ["git", "merge-base"]:
                if cmd[2] != "origin/main":
                    raise AssertionError(f"expected merge-base against origin/main, got: {cmd}")
                return _result(returncode=0, stdout="deadbeef\n")
            if cmd[:2] == ["git", "diff"]:
                return _result(returncode=0, stdout="big.bin\n")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("subprocess.run", side_effect=fake_run):
            oversized = pc.oversized_committed_files(max_bytes=5)

        self.assertEqual(oversized, [("big.bin", 10)])

    def test_falls_back_to_local_main_when_origin_main_unavailable(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                return _result(returncode=1)
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _result(returncode=1)
            if cmd[:2] == ["git", "merge-base"]:
                if cmd[2] != "main":
                    raise AssertionError(f"expected merge-base against main, got: {cmd}")
                return _result(returncode=0, stdout="deadbeef\n")
            if cmd[:2] == ["git", "diff"]:
                return _result(returncode=0, stdout="")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("subprocess.run", side_effect=fake_run):
            self.assertEqual(pc.oversized_committed_files(), [])

    def test_returns_empty_when_merge_base_fails(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "fetch"]:
                return _result(returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return _result(returncode=0)
            if cmd[:2] == ["git", "merge-base"]:
                return _result(returncode=1)
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("subprocess.run", side_effect=fake_run):
            self.assertEqual(pc.oversized_committed_files(), [])


if __name__ == "__main__":
    unittest.main()
