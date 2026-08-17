"""Unit tests for agent_common/critic_reconcile.py."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import critic_reconcile


class TestIsCleanPass(unittest.TestCase):
    def test_pass_with_no_findings_is_clean(self):
        self.assertTrue(critic_reconcile.is_clean_pass({"status": "PASS", "violations": []}))

    def test_pass_with_no_keys_at_all_is_clean(self):
        self.assertTrue(critic_reconcile.is_clean_pass({"status": "PASS"}))

    def test_fail_is_never_clean(self):
        self.assertFalse(critic_reconcile.is_clean_pass({"status": "FAIL", "violations": []}))

    def test_pass_with_violations_is_not_clean(self):
        self.assertFalse(
            critic_reconcile.is_clean_pass(
                {"status": "PASS", "violations": [{"rule": "x", "severity": "WARNING"}]}
            )
        )

    def test_pass_with_blocking_issues_is_not_clean_confidence_style(self):
        self.assertFalse(
            critic_reconcile.is_clean_pass({"status": "PASS", "blocking_issues": [{"title": "x"}]})
        )

    def test_pass_with_non_blocking_concerns_is_not_clean(self):
        self.assertFalse(
            critic_reconcile.is_clean_pass(
                {"status": "PASS", "non_blocking_concerns": [{"title": "x"}]}
            )
        )


class TestAllCleanPass(unittest.TestCase):
    def test_all_clean_true_when_every_critic_clean(self):
        raw_results = [
            {"id": "a", "model": "m1", "result": {"status": "PASS"}},
            {"id": "b", "model": "m2", "result": {"status": "PASS", "violations": []}},
        ]
        self.assertTrue(critic_reconcile.all_clean_pass(raw_results))

    def test_all_clean_false_if_any_critic_has_findings(self):
        raw_results = [
            {"id": "a", "model": "m1", "result": {"status": "PASS"}},
            {
                "id": "b",
                "model": "m2",
                "result": {"status": "FAIL", "violations": [{"rule": "x", "severity": "BLOCKING"}]},
            },
        ]
        self.assertFalse(critic_reconcile.all_clean_pass(raw_results))


class TestSynthesizeTrivialPass(unittest.TestCase):
    def test_violations_style_schema(self):
        raw_results = [
            {"id": "a", "model": "m1", "result": {"status": "PASS"}},
            {"id": "b", "model": "m2", "result": {"status": "PASS"}},
        ]
        result = critic_reconcile.synthesize_trivial_pass(raw_results, 2, "violations")
        self.assertEqual(result["iteration"], 2)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["not_applicable"], [])
        self.assertIn("a", result["summary"])
        self.assertIn("b", result["summary"])

    def test_confidence_style_schema(self):
        raw_results = [{"id": "a", "model": "m1", "result": {"status": "PASS"}}]
        result = critic_reconcile.synthesize_trivial_pass(raw_results, 1, "confidence")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blocking_issues"], [])
        self.assertEqual(result["non_blocking_concerns"], [])
        self.assertEqual(result["required_remediations"], [])
        self.assertEqual(result["confidence"], 10)


class TestBuildReconcilePrompt(unittest.TestCase):
    def test_includes_all_critic_ids_and_models(self):
        raw_results = [
            {"id": "qwen", "model": "qwen3:30b", "result": {"status": "FAIL", "violations": []}},
            {
                "id": "nvidia",
                "model": "meta/llama-3.1-70b",
                "result": {"status": "PASS", "violations": []},
            },
        ]
        prompt = critic_reconcile.build_reconcile_prompt(
            "CONTEXT BLOCK HERE", raw_results, 3, "violations"
        )
        self.assertIn("qwen", prompt)
        self.assertIn("qwen3:30b", prompt)
        self.assertIn("nvidia", prompt)
        self.assertIn("meta/llama-3.1-70b", prompt)
        self.assertIn("CONTEXT BLOCK HERE", prompt)
        self.assertIn("2 independent critics", prompt)

    def test_violations_style_includes_correct_schema_block(self):
        raw_results = [{"id": "a", "model": "m1", "result": {"status": "PASS"}}]
        prompt = critic_reconcile.build_reconcile_prompt("ctx", raw_results, 1, "violations")
        self.assertIn('"violations"', prompt)
        self.assertIn('"not_applicable"', prompt)
        self.assertNotIn('"blocking_issues"', prompt)

    def test_confidence_style_includes_correct_schema_block(self):
        raw_results = [{"id": "a", "model": "m1", "result": {"status": "PASS"}}]
        prompt = critic_reconcile.build_reconcile_prompt("ctx", raw_results, 1, "confidence")
        self.assertIn('"blocking_issues"', prompt)
        self.assertIn('"confidence"', prompt)
        self.assertNotIn('"not_applicable"', prompt)

    def test_output_instructions_appended(self):
        raw_results = [{"id": "a", "model": "m1", "result": {"status": "PASS"}}]
        prompt = critic_reconcile.build_reconcile_prompt(
            "ctx", raw_results, 1, "violations", output_instructions="- write it here"
        )
        self.assertIn("- write it here", prompt)

    def test_never_trust_a_finding_because_a_critic_said_so_rule_present(self):
        raw_results = [{"id": "a", "model": "m1", "result": {"status": "PASS"}}]
        prompt = critic_reconcile.build_reconcile_prompt("ctx", raw_results, 1, "violations")
        self.assertIn("critic said so", prompt)


if __name__ == "__main__":
    unittest.main()
