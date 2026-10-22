"""Unit tests for agent_common/preflight_checks.py's commit-hygiene checks.

Focused coverage for oversized_committed_files(), added alongside the
FOLLOWUP_HARNESS.md Bug 2 fix: it independently reimplemented the same
"diff against main" computation as get_changed_files() (via merge-base +
diff instead of triple-dot), with its own hardcoded "main" default and no
staleness guard — now routed through the shared resolve_base_ref() helper
from agent_common.git (test_git.py covers that helper directly). Task-tag
and [MANUAL]-tagging checks are covered below; red-state checks remain
untested.
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


class TestTaskTags(unittest.TestCase):
    def test_tags_come_only_from_the_block_after_the_id(self):
        """specs/005: "no separate [TEST] task" in an [IMPL] line's prose was
        counted as an unchecked [TEST] task and aborted the test stage."""
        tasks = (
            "- [ ] T005 [IMPL] [US1] Add handler (no separate [TEST] task — covered by T004)\n"
            "- [ ] T006 [TEST] [P] [US1] Write failing test\n"
        )
        lines = pc.unchecked_task_lines(tasks, tag="[TEST]")
        self.assertEqual(len(lines), 1)
        self.assertIn("T006", lines[0])

    def test_bold_manual_tag_is_recognised(self):
        tasks = "- [ ] T030 **[MANUAL]** Run the manual verification checklist\n"
        self.assertEqual(pc.unchecked_task_lines(tasks, exclude_tag="[MANUAL]"), [])


class TestManualTagViolations(unittest.TestCase):
    def test_flags_untagged_human_approval_task(self):
        tasks = "- [ ] T002 Wait for human approval on constitution amendments\n"
        violations = pc.task_format_violations(tasks)
        self.assertEqual(len(violations), 1)
        self.assertIn("MISSING [MANUAL] tag", violations[0])

    def test_tagged_test_and_checked_tasks_are_not_flagged(self):
        tasks = (
            "- [ ] T017 [MANUAL] Perform the manual verification on a real AWS account\n"
            "- [ ] T037 [TEST] [US3] Write failing test that the live deploy step lists the stack\n"
            "- [x] T019 Perform the manual verification plan\n"
            "- [ ] T020 [IMPL] [US1] Implement the parser — src/parser.ts\n"
        )
        self.assertEqual(pc.task_format_violations(tasks), [])


class TestPendingHumanApprovals(unittest.TestCase):
    TEMPLATE_COMMENT = "<!--\n  List every amendment, one `- ` bullet each, or write `None.`\n-->\n"

    def test_none_means_nothing_pending(self):
        plan = f"## Pending Human Approvals\n\n{self.TEMPLATE_COMMENT}\nNone.\n\n## Project Structure\n- src/\n"
        self.assertEqual(pc.pending_human_approvals(plan), [])

    def test_lists_bullets_from_that_section_only(self):
        plan = (
            "## Pending Human Approvals\n\n"
            f"{self.TEMPLATE_COMMENT}"
            "- Constitution §2: add Route 53\n"
            "- architecture.md §8: API key at the edge\n\n"
            "## Project Structure\n- src/\n"
        )
        self.assertEqual(
            pc.pending_human_approvals(plan),
            ["- Constitution §2: add Route 53", "- architecture.md §8: API key at the edge"],
        )

    def test_plan_without_the_section_has_nothing_pending(self):
        self.assertEqual(pc.pending_human_approvals("## Summary\n- a\n"), [])


class TestRevisionRegressions(unittest.TestCase):
    PLAN = "## Summary\nx\n## Constitution Check\n| a |\n## Project Structure\ny\n"

    def test_unchanged_file_is_a_regression(self):
        self.assertIn("not changed", pc.revision_regressions(self.PLAN, self.PLAN)[0])

    def test_removed_section_is_reported(self):
        after = self.PLAN.replace("## Constitution Check\n| a |\n", "")
        problems = pc.revision_regressions(self.PLAN, after)
        self.assertEqual(len(problems), 1)
        self.assertIn("## Constitution Check", problems[0])

    def test_collapsed_tasks_are_reported(self):
        before = "## Phase 1\n" + "".join(f"- [ ] T00{i} [IMPL] x\n" for i in range(1, 7))
        after = "## Phase 1\n- [ ] T001 Wait for human approval\n"
        self.assertIn("task count dropped", pc.revision_regressions(before, after)[0])

    def test_genuine_edit_passes(self):
        after = self.PLAN.replace("y\n", "y — now with contracts/\n")
        self.assertEqual(pc.revision_regressions(self.PLAN, after), [])
