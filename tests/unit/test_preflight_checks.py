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


class TestRedStateIssue(unittest.TestCase):
    def test_genuine_assertion_failure_is_valid(self):
        self.assertIsNone(pc.red_state_issue("Tests  1 failed | 3 passed"))

    def test_passing_run_is_rejected_as_inconclusive(self):
        self.assertIn("neither", pc.red_state_issue("Tests  4 passed (4)"))

    def test_declared_regression_guard_passing_run_is_accepted(self):
        """specs/087, 016, 017: every rejected artifact was a declared
        regression-guard capture, kicked back twice per feature for nothing."""
        artifact = (
            "=== T023 — constitution §19 regression-guard exception ===\n"
            "These tests PASS against today's code.\n\nTests  4 passed (4)"
        )
        self.assertIsNone(pc.red_state_issue(artifact))

    def test_regression_guard_declaration_does_not_excuse_an_infra_failure(self):
        artifact = "§19 REGRESSION-GUARD EXCEPTION\nError: connect ECONNREFUSED 127.0.0.1:5432"
        self.assertIsNotNone(pc.red_state_issue(artifact))


class TestTaskFormatAnalysis(unittest.TestCase):
    """The grammar speckit-tasks/SKILL.md defines; the old analysis flagged every
    line outside it and the tasks critic flapped on the result (032, 033, 072)."""

    TASKS = (
        "## Phase 1: Setup\n"
        "- [ ] T001 Create the config file (no test — infrastructure setup)\n"
        "## Phase 3: User Story 1 (P1)\n"
        "- [ ] T002 [TEST] [US1, US2] Write failing test for the shared matcher\n"
        "- [ ] T003 [IMPL] [US1, US2] Implement the shared matcher (depends on T002)\n"
        "- [ ] T004 [IMPL] Implement the handler (depends on T002)\n"
        "## Phase 4: Polish\n"
        "- [ ] T005 [MANUAL] Deploy to dev and run the live checks\n"
        "## Dependencies & Execution Order\n"
        "- T002 [TEST] must complete before T003 [IMPL] begins\n"
        "- Phase 3 depends on Setup (T001)\n"
    )

    def test_only_a_story_phase_type_tagged_task_needs_a_story_label(self):
        _, incomplete, _, _ = pc.classify_task_lines(self.TASKS)
        self.assertEqual(len(incomplete), 1)
        self.assertIn("T004", incomplete[0])
        self.assertIn("MISSING [USX] story label", incomplete[0])

    def test_prose_bullets_citing_task_ids_are_not_tasks(self):
        complete, incomplete, _, bullets = pc.classify_task_lines(self.TASKS)
        self.assertFalse(any("must complete before" in line for line in complete + incomplete))
        self.assertEqual(len(bullets), 2)

    def test_bullet_opening_with_a_task_id_still_needs_a_checkbox(self):
        tasks = "## Phase 1: Setup\n- T001 Create the config file\n"
        _, incomplete, _, _ = pc.classify_task_lines(tasks)
        self.assertIn("MISSING checkbox", incomplete[0])


class TestTagViolations(unittest.TestCase):
    def test_flags_manual_combined_with_a_type_tag(self):
        """033, 047, 057: a [TEST] [MANUAL] task deadlocked the test stage."""
        tasks = "- [ ] T036 [TEST] [MANUAL] [US5] Run the live evaluation\n"
        violations = pc.task_format_violations(tasks)
        self.assertEqual(len(violations), 1)
        self.assertIn("[MANUAL] combined with [TEST]/[IMPL]", violations[0])

    def test_flags_unknown_tags(self):
        """051: a literal [non-deliverable] tag aborted the implement stage."""
        tasks = "- [ ] T141 [non-deliverable] [US1] Documentation stage updates docs/\n"
        violations = pc.task_format_violations(tasks)
        self.assertEqual(len(violations), 1)
        self.assertIn("unknown tag [non-deliverable]", violations[0])

    def test_multi_story_labels_are_known_tags(self):
        tasks = "- [ ] T002 [TEST] [P] [US1, US2] Write failing test\n"
        self.assertEqual(pc.task_format_violations(tasks), [])
        self.assertIn("[TEST]", pc.task_tags(tasks))

    def test_test_stage_skips_manual_test_tasks(self):
        tasks = "- [ ] T036 [TEST] [MANUAL] Run the live evaluation\n- [x] T002 [TEST] Write test\n"
        self.assertEqual(pc.unchecked_task_lines(tasks, tag="[TEST]", exclude_tag="[MANUAL]"), [])


class TestTaskTagGrammarCopies(unittest.TestCase):
    """Five hand-written versions of the tag rules drifted apart and the tasks
    critic flapped between them; every copy must be the grammar verbatim."""

    def test_skills_and_template_carry_the_grammar_verbatim(self):
        root = Path(__file__).parent.parent.parent
        for rel in (
            ".claude/skills/ch-2-tasks-critic/SKILL.md",
            ".claude/skills/speckit-tasks/SKILL.md",
            ".specify/templates/tasks-template.md",
        ):
            with self.subTest(rel):
                self.assertIn(pc.task_tag_grammar(), (root / rel).read_text(encoding="utf-8"))


class TestDiscardAcceptedFormatFindings(unittest.TestCase):
    TASKS = (
        "## Phase 3: User Story 1 (P1)\n"
        "- [ ] T002 [TEST] [US1] Write failing test\n"
        "- [ ] T003 [IMPL] Implement it (depends on T002)\n"
        "## Phase 4: Polish\n"
        "- [ ] T008 [MANUAL] Deploy to dev and run the live checks\n"
        "- [ ] T009 Write the CI workflow env passthrough\n"
    )

    @staticmethod
    def _finding(location, finding, rule="§T5 — Task Format"):
        return {"rule": rule, "severity": "BLOCKING", "location": location, "finding": finding}

    def test_drops_format_demands_on_accepted_lines_and_passes_the_result(self):
        """072: the critic demanded [TEST]/[IMPL] on a correct [MANUAL] task."""
        result = {
            "status": "FAIL",
            "violations": [self._finding("T008", "T008 uses [MANUAL] instead of [TEST] or [IMPL]")],
        }
        dropped = pc.discard_accepted_format_findings(result, self.TASKS)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["violations"], [])

    def test_keeps_manual_judgements_rejected_lines_and_other_rules(self):
        kept = [
            self._finding("T009", "T009 writes repo files so must not be tagged [MANUAL]"),
            self._finding("T009", "T009 is a deploy step missing a [MANUAL] tag"),
            self._finding("T003", "T003 is missing its [USX] story label"),
            self._finding("T003", "T003 has no paired test", rule="§T3 — Pairing"),
            self._finding("Phase 4", "Numbered list instead of tasks"),
        ]
        result = {"status": "FAIL", "violations": list(kept)}
        self.assertEqual(pc.discard_accepted_format_findings(result, self.TASKS), [])
        self.assertEqual(result["violations"], kept)
        self.assertEqual(result["status"], "FAIL")
