"""Unit tests for agent_common/preflight_checks.py's implement-stage checks:
test-case edits made outside the test phase, and CI output that shows the
environment, not the code, failed or skipped whole suites."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import preflight_checks as pc


class TestTestCaseEdits(unittest.TestCase):
    """043, 057, 060, 066: implementation commits edited existing test cases."""

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def _commit(self, path, content, subject):
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(content)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", subject)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        self.test = (
            "vi.mock('./db')\n"
            "it('lists three triggers', () => {\n"
            "  expect(TRIGGERS).toEqual(['a', 'b', 'c'])\n"
            "})\n"
        )
        self._commit("tests/triggers.test.ts", self.test, "test: T001 baseline")
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        self.cwd = Path.cwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_an_implementation_commit_changing_an_assertion_is_flagged(self):
        self._commit(
            "tests/triggers.test.ts",
            self.test.replace("'c']", "'c', 'd']"),
            "feat: T002 add trigger d",
        )
        edits = pc.test_case_edits(self.base)
        self.assertEqual(list(edits), ["tests/triggers.test.ts"])

    def test_test_phase_commits_setup_changes_and_new_files_are_not(self):
        self._commit(
            "tests/triggers.test.ts",
            self.test.replace("'c']", "'c', 'd']"),
            "test: T003 update the trigger list",
        )
        self._commit(
            "tests/triggers.test.ts",
            self.test.replace("'c']", "'c', 'd']").replace("./db", "./db-v2"),
            "fix: point the mock at the new module",
        )
        self._commit("tests/new.test.ts", "it('x', () => expect(1).toBe(1))\n", "feat: T004")
        self.assertEqual(pc.test_case_edits(self.base), {})


class TestCiOutputSignals(unittest.TestCase):
    def test_environment_failures_are_recognised(self):
        self.assertIn(
            "ECONNREFUSED",
            pc.ci_environment_failure("x\nError: connect ECONNREFUSED 127.0.0.1:5432\n"),
        )
        self.assertIsNone(pc.ci_environment_failure("AssertionError: expected 2 to be 3"))

    def test_wholly_skipped_files_from_vitest_output(self):
        output = (
            "packages/api test:  ↓ |shared| tests/db/create-list-clubs.test.ts (4 tests | 4 skipped)\n"
            " ↓ tests/reconciliation.context.test.ts (3 tests | 3 skipped)\n"
            " ✓ tests/unit.test.ts (5 tests | 1 skipped)\n"
        )
        self.assertEqual(
            pc.wholly_skipped_test_files(output),
            ["tests/db/create-list-clubs.test.ts", "tests/reconciliation.context.test.ts"],
        )
