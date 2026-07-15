"""Unit tests for ch_plan_to_implement_auto.py's _check_stage_result stage-result
handling — the pass/fail/usage-limit-pause branching used after each stage subprocess."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import ch_plan_to_implement_auto as ptia

from agent_common.console import USAGE_LIMIT_EXIT_CODE


class TestCheckStageResult(unittest.TestCase):
    def test_zero_exit_code_logs_passed_and_does_not_exit(self):
        with patch.object(ptia, "log") as mock_log:
            ptia._check_stage_result(0, 1, "plan", "ch-1-plan-critic-escalation.md")

        mock_log.assert_called_once_with("Stage 1/4 (plan): PASSED.")

    def test_generic_failure_logs_escalation_hint_and_exits_1(self):
        with patch.object(ptia, "log") as mock_log, self.assertRaises(SystemExit) as cm:
            ptia._check_stage_result(1, 2, "tasks", "ch-2-tasks-critic-escalation.md")

        self.assertEqual(cm.exception.code, 1)
        mock_log.assert_called_once_with(
            "Stage 2/4 (tasks): FAILED. Review ch-2-tasks-critic-escalation.md and re-run."
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


if __name__ == "__main__":
    unittest.main()
