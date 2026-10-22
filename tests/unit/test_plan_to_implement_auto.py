"""Unit tests for ch_plan_to_implement_auto.py's _check_stage_result stage-result
handling — the pass/fail/usage-limit-pause branching used after each stage subprocess."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import ch_plan_to_implement_auto as ptia

from agent_common.console import HUMAN_ACTION_EXIT_CODE, USAGE_LIMIT_EXIT_CODE


class TestCheckStageResult(unittest.TestCase):
    def test_zero_exit_code_logs_passed_and_does_not_exit(self):
        with patch.object(ptia, "log") as mock_log:
            ptia._check_stage_result(0, 1, "plan", "ch-1-plan-critic-escalation.md")

        mock_log.assert_called_once_with("Stage 1/5 (plan): PASSED.")

    def test_generic_failure_logs_escalation_hint_and_exits_1(self):
        with patch.object(ptia, "log") as mock_log, self.assertRaises(SystemExit) as cm:
            ptia._check_stage_result(1, 2, "tasks", "ch-2-tasks-critic-escalation.md")

        self.assertEqual(cm.exception.code, 1)
        mock_log.assert_called_once_with(
            "Stage 2/5 (tasks): FAILED. Review ch-2-tasks-critic-escalation.md and re-run."
        )

    def test_usage_limit_exit_code_logs_paused_and_exits_with_that_code(self):
        with patch.object(ptia, "log") as mock_log, self.assertRaises(SystemExit) as cm:
            ptia._check_stage_result(
                USAGE_LIMIT_EXIT_CODE, 4, "implement", "ch-4-implement-critic-escalation.md"
            )

        self.assertEqual(cm.exception.code, USAGE_LIMIT_EXIT_CODE)
        mock_log.assert_called_once()
        self.assertIn("PAUSED", mock_log.call_args[0][0])
        self.assertIn("re-run", mock_log.call_args[0][0].lower())

    def test_human_action_exit_code_logs_waiting_and_exits_with_that_code(self):
        with patch.object(ptia, "log") as mock_log, self.assertRaises(SystemExit) as cm:
            ptia._check_stage_result(
                HUMAN_ACTION_EXIT_CODE, 2, "tasks", "ch-2-tasks-critic-escalation.md"
            )

        self.assertEqual(cm.exception.code, HUMAN_ACTION_EXIT_CODE)
        self.assertIn("WAITING for human action", mock_log.call_args[0][0])

    def test_stage_5_docs_failure_logs_escalation_hint_and_exits_1(self):
        with patch.object(ptia, "log") as mock_log, self.assertRaises(SystemExit) as cm:
            ptia._check_stage_result(1, 5, "docs", "ch-5-docs-critic-escalation.md")

        self.assertEqual(cm.exception.code, 1)
        mock_log.assert_called_once_with(
            "Stage 5/5 (docs): FAILED. Review ch-5-docs-critic-escalation.md and re-run."
        )


class TestStageSkipReason(unittest.TestCase):
    """_stage_skip_reason is the pure helper run() consults before each
    toggleable stage's existing resume-check block — None when the stage is
    enabled (the default), a log-ready message when disabled."""

    def test_enabled_stage_returns_none(self):
        with patch.object(ptia, "is_stage_enabled", return_value=True):
            self.assertIsNone(ptia._stage_skip_reason(3, "test"))

    def test_disabled_stage_returns_skip_message(self):
        with patch.object(ptia, "is_stage_enabled", return_value=False):
            reason = ptia._stage_skip_reason(3, "test")

        self.assertIsNotNone(reason)
        self.assertIn("Stage 3/5 (test)", reason)
        self.assertIn("disabled", reason)
        self.assertIn("pipeline.json", reason)

    def test_disabled_docs_stage_uses_its_own_number_and_key(self):
        with patch.object(ptia, "is_stage_enabled", return_value=False):
            reason = ptia._stage_skip_reason(5, "docs")

        self.assertIn("Stage 5/5 (docs)", reason)


if __name__ == "__main__":
    unittest.main()
