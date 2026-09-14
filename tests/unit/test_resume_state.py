"""Unit tests for agent_common/resume_state.py pure functions. No LLM or network calls."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import resume_state


class TestNextIteration(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(resume_state.next_iteration(Path(d), "ch-1-plan-critic-result"), 1)

    def test_one_result_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ch-1-plan-critic-result-1.json").write_text("{}")
            self.assertEqual(resume_state.next_iteration(Path(d), "ch-1-plan-critic-result"), 2)

    def test_three_result_files(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(1, 4):
                (Path(d) / f"ch-1-plan-critic-result-{i}.json").write_text("{}")
            self.assertEqual(resume_state.next_iteration(Path(d), "ch-1-plan-critic-result"), 4)


class TestFindPassingIteration(unittest.TestCase):
    def _write(self, d, i, status):
        (Path(d) / f"ch-1-plan-critic-result-{i}.json").write_text(json.dumps({"status": status}))

    def test_no_files_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                resume_state.find_passing_iteration(Path(d), "ch-1-plan-critic-result")
            )

    def test_all_fail_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "FAIL")
            self._write(d, 2, "FAIL")
            self.assertIsNone(
                resume_state.find_passing_iteration(Path(d), "ch-1-plan-critic-result")
            )

    def test_first_pass_returns_1(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "PASS")
            self.assertEqual(
                resume_state.find_passing_iteration(Path(d), "ch-1-plan-critic-result"), 1
            )

    def test_second_pass_returns_2(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "FAIL")
            self._write(d, 2, "PASS")
            self.assertEqual(
                resume_state.find_passing_iteration(Path(d), "ch-1-plan-critic-result"), 2
            )


class TestStageComplete(unittest.TestCase):
    def test_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(resume_state.stage_is_complete(Path(d), "plan"))

    def test_complete_after_write(self):
        with tempfile.TemporaryDirectory() as d:
            resume_state.write_stage_complete(Path(d), "plan")
            self.assertTrue(resume_state.stage_is_complete(Path(d), "plan"))

    def test_marker_content_includes_stage(self):
        with tempfile.TemporaryDirectory() as d:
            resume_state.write_stage_complete(Path(d), "plan")
            content = (Path(d) / "plan-auto-complete").read_text()
            self.assertIn("stage: plan", content)


class TestFormatViolationsBlock(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(resume_state.format_violations_block(None, 2), "")

    def test_empty_list_returns_empty(self):
        self.assertEqual(resume_state.format_violations_block([], 2), "")

    def test_non_empty_list_returns_block(self):
        violations = [{"rule": "§T1", "severity": "BLOCKING", "finding": "missing"}]
        result = resume_state.format_violations_block(violations, 2)
        # § is JSON-encoded as § in the output
        self.assertIn("BLOCKING", result)
        self.assertIn("previous iteration (1)", result)  # iteration - 1 = 1


class TestValidateGateSchema(unittest.TestCase):
    def test_confidence_style_all_keys_present_does_not_raise(self):
        result = {"status": "PASS", "confidence": 9, "blocking_issues": []}
        resume_state.validate_gate_schema(result, "confidence")  # no raise

    def test_confidence_style_missing_keys_raises_naming_them(self):
        with self.assertRaises(resume_state.GateSchemaError) as cm:
            resume_state.validate_gate_schema({"findings": []}, "confidence")
        self.assertIn("blocking_issues", str(cm.exception))
        self.assertIn("confidence", str(cm.exception))
        self.assertIn("status", str(cm.exception))

    def test_violations_style_all_keys_present_does_not_raise(self):
        result = {"status": "FAIL", "violations": []}
        resume_state.validate_gate_schema(result, "violations")  # no raise

    def test_violations_style_missing_violations_raises(self):
        with self.assertRaises(resume_state.GateSchemaError) as cm:
            resume_state.validate_gate_schema({"status": "PASS"}, "violations")
        self.assertIn("violations", str(cm.exception))


class TestReadResult(unittest.TestCase):
    def test_no_summary_style_skips_validation(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "gate-1.json").write_text(json.dumps({"findings": []}))
            result = resume_state.read_result(Path(d), "gate", 1)
            self.assertEqual(result, {"findings": []})

    def test_confirmed_bug_input_raises_gate_schema_error(self):
        """Exact iteration-5 FOLLOWUP_HARNESS.md bug input: a quality-review
        subagent writing {"findings": []} instead of the confidence-style
        schema its prompt asked for must be a loud error, not a silent FAIL."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "gate-5.json").write_text(json.dumps({"findings": []}))
            with self.assertRaises(resume_state.GateSchemaError):
                resume_state.read_result(Path(d), "gate", 5, summary_style="confidence")

    def test_well_formed_confidence_result_passes_validation(self):
        with tempfile.TemporaryDirectory() as d:
            data = {"status": "PASS", "confidence": 9, "blocking_issues": []}
            (Path(d) / "gate-1.json").write_text(json.dumps(data))
            result = resume_state.read_result(Path(d), "gate", 1, summary_style="confidence")
            self.assertEqual(result, data)


class TestLoadPriorViolations(unittest.TestCase):
    def _write_result(self, d, i, status, violations=None):
        data = {"status": status, "violations": violations or []}
        (Path(d) / f"ch-1-plan-critic-result-{i}.json").write_text(json.dumps(data))

    def test_iteration_1_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                resume_state.load_prior_violations(Path(d), "ch-1-plan-critic-result", 1)
            )

    def test_previous_pass_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_result(d, 1, "PASS")
            self.assertIsNone(
                resume_state.load_prior_violations(Path(d), "ch-1-plan-critic-result", 2)
            )

    def test_previous_fail_returns_violations(self):
        viols = [{"rule": "§T1", "severity": "BLOCKING"}]
        with tempfile.TemporaryDirectory() as d:
            self._write_result(d, 1, "FAIL", viols)
            result = resume_state.load_prior_violations(Path(d), "ch-1-plan-critic-result", 2)
            self.assertEqual(result, viols)

    def test_previous_off_schema_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ch-1-plan-critic-result-1.json").write_text(json.dumps({"findings": []}))
            self.assertIsNone(
                resume_state.load_prior_violations(Path(d), "ch-1-plan-critic-result", 2)
            )


class TestFindTwoGateResumeState(unittest.TestCase):
    def _write(self, d, prefix, i, status, key="violations", items=None):
        data = {"status": status, key: items or []}
        if key == "blocking_issues":
            data.setdefault("confidence", 5)  # gate2 is confidence-style — needs both keys
        (Path(d) / f"{prefix}-{i}.json").write_text(json.dumps(data))

    def test_iteration_1_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            it, g1, g2 = resume_state.find_two_gate_resume_state(Path(d), "gate1", "gate2", 1)
            self.assertEqual(it, 1)
            self.assertIsNone(g1)
            self.assertIsNone(g2)

    def test_gate1_fail_returns_violations(self):
        viols = [{"rule": "§T1"}]
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "FAIL", "violations", viols)
            it, g1, g2 = resume_state.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertEqual(g1, viols)
            self.assertIsNone(g2)

    def test_gate1_pass_gate2_missing_decrements(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            it, g1, g2 = resume_state.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 1)
            self.assertIsNone(g1)
            self.assertIsNone(g2)

    def test_gate2_fail_returns_blocking_issues(self):
        issues = [{"issue": "arch violation"}]
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            self._write(d, "gate2", 1, "FAIL", "blocking_issues", issues)
            it, g1, g2 = resume_state.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertIsNone(g1)
            self.assertEqual(g2, issues)

    def test_both_pass_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            self._write(d, "gate2", 1, "PASS", "blocking_issues", [])
            it, g1, g2 = resume_state.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertIsNone(g1)
            self.assertIsNone(g2)

    def test_gate1_off_schema_falls_back_to_no_violations(self):
        """A gate1 result missing 'violations' (e.g. a ReportFindings-shaped
        {"findings": []} blob) must not crash resume — same fallback as a
        malformed/corrupt result file, not a hard error at resume time (the
        authoritative PASS/FAIL check happens elsewhere)."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "gate1-1.json").write_text(json.dumps({"findings": []}))
            it, g1, g2 = resume_state.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertIsNone(g1)
            self.assertIsNone(g2)

    def test_gate2_off_schema_falls_back_to_no_violations(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            (Path(d) / "gate2-1.json").write_text(json.dumps({"findings": []}))
            it, g1, g2 = resume_state.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 1)
            self.assertIsNone(g1)
            self.assertIsNone(g2)


if __name__ == "__main__":
    unittest.main()
