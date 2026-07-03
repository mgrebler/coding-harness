"""Unit tests for agent_common.py pure functions. No LLM or network calls."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import agent_common


class TestStripFences(unittest.TestCase):
    def test_no_fence_passthrough(self):
        self.assertEqual(agent_common.strip_fences('{"a": 1}'), '{"a": 1}')

    def test_json_fence_stripped(self):
        result = agent_common.strip_fences('```json\n{"a": 1}\n```')
        self.assertEqual(result, '{"a": 1}')

    def test_plain_fence_stripped(self):
        result = agent_common.strip_fences('```\n{"a": 1}\n```')
        self.assertEqual(result, '{"a": 1}')

    def test_whitespace_trimmed(self):
        result = agent_common.strip_fences('  {"a": 1}  ')
        self.assertEqual(result, '{"a": 1}')


class TestNextIteration(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(agent_common.next_iteration(Path(d), "plan-critic-result"), 1)

    def test_one_result_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "plan-critic-result-1.json").write_text("{}")
            self.assertEqual(agent_common.next_iteration(Path(d), "plan-critic-result"), 2)

    def test_three_result_files(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(1, 4):
                (Path(d) / f"plan-critic-result-{i}.json").write_text("{}")
            self.assertEqual(agent_common.next_iteration(Path(d), "plan-critic-result"), 4)


class TestFindPassingIteration(unittest.TestCase):
    def _write(self, d, i, status):
        (Path(d) / f"plan-critic-result-{i}.json").write_text(json.dumps({"status": status}))

    def test_no_files_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(agent_common.find_passing_iteration(Path(d), "plan-critic-result"))

    def test_all_fail_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "FAIL")
            self._write(d, 2, "FAIL")
            self.assertIsNone(agent_common.find_passing_iteration(Path(d), "plan-critic-result"))

    def test_first_pass_returns_1(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "PASS")
            self.assertEqual(agent_common.find_passing_iteration(Path(d), "plan-critic-result"), 1)

    def test_second_pass_returns_2(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "FAIL")
            self._write(d, 2, "PASS")
            self.assertEqual(agent_common.find_passing_iteration(Path(d), "plan-critic-result"), 2)


class TestLoadLocalLlmConfig(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    def _write_config(self, data):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_no_file_returns_none(self):
        self.assertIsNone(agent_common.load_local_llm_config("plan"))

    def test_default_disabled_returns_none(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": False, "model": ""},
                            "critics": {}})
        self.assertIsNone(agent_common.load_local_llm_config("plan"))

    def test_critic_override_enabled_returns_config(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": False, "model": ""},
                            "critics": {"plan": {"enabled": True, "model": "qwen3:4b"}}})
        result = agent_common.load_local_llm_config("plan")
        self.assertIsNotNone(result)
        self.assertEqual(result["model"], "qwen3:4b")
        self.assertIn("ollama_url", result)

    def test_num_ctx_top_level_passed_through(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "num_ctx": 8192,
                            "default": {"enabled": True, "model": "llama3.2"}})
        result = agent_common.load_local_llm_config("plan")
        self.assertEqual(result["num_ctx"], 8192)

    def test_num_ctx_absent_when_not_set(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": True, "model": "llama3.2"}})
        result = agent_common.load_local_llm_config("plan")
        self.assertNotIn("num_ctx", result)

    def test_default_enabled_returns_config(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": True, "model": "llama3.2"},
                            "critics": {}})
        result = agent_common.load_local_llm_config("plan")
        self.assertIsNotNone(result)
        self.assertEqual(result["model"], "llama3.2")

    def test_empty_model_returns_none(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": True, "model": ""},
                            "critics": {}})
        self.assertIsNone(agent_common.load_local_llm_config("plan"))

    def test_corrupt_json_returns_none(self):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{invalid json")
        self.assertIsNone(agent_common.load_local_llm_config("plan"))


class TestStageComplete(unittest.TestCase):
    def test_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(agent_common.stage_is_complete(Path(d), "plan"))

    def test_complete_after_write(self):
        with tempfile.TemporaryDirectory() as d:
            agent_common.write_stage_complete(Path(d), "plan")
            self.assertTrue(agent_common.stage_is_complete(Path(d), "plan"))

    def test_marker_content_includes_stage(self):
        with tempfile.TemporaryDirectory() as d:
            agent_common.write_stage_complete(Path(d), "plan")
            content = (Path(d) / "plan-auto-complete").read_text()
            self.assertIn("stage: plan", content)


class TestFormatViolationsBlock(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(agent_common.format_violations_block(None, 2), "")

    def test_empty_list_returns_empty(self):
        self.assertEqual(agent_common.format_violations_block([], 2), "")

    def test_non_empty_list_returns_block(self):
        violations = [{"rule": "§T1", "severity": "BLOCKING", "finding": "missing"}]
        result = agent_common.format_violations_block(violations, 2)
        # § is JSON-encoded as § in the output
        self.assertIn("BLOCKING", result)
        self.assertIn("previous iteration (1)", result)  # iteration - 1 = 1


class TestLoadPriorViolations(unittest.TestCase):
    def _write_result(self, d, i, status, violations=None):
        data = {"status": status, "violations": violations or []}
        (Path(d) / f"plan-critic-result-{i}.json").write_text(json.dumps(data))

    def test_iteration_1_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(agent_common.load_prior_violations(Path(d), "plan-critic-result", 1))

    def test_previous_pass_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_result(d, 1, "PASS")
            self.assertIsNone(agent_common.load_prior_violations(Path(d), "plan-critic-result", 2))

    def test_previous_fail_returns_violations(self):
        viols = [{"rule": "§T1", "severity": "BLOCKING"}]
        with tempfile.TemporaryDirectory() as d:
            self._write_result(d, 1, "FAIL", viols)
            result = agent_common.load_prior_violations(Path(d), "plan-critic-result", 2)
            self.assertEqual(result, viols)


class TestFindTwoGateResumeState(unittest.TestCase):
    def _write(self, d, prefix, i, status, key="violations", items=None):
        data = {"status": status, key: items or []}
        (Path(d) / f"{prefix}-{i}.json").write_text(json.dumps(data))

    def test_iteration_1_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 1)
            self.assertEqual(it, 1)
            self.assertIsNone(g1)
            self.assertIsNone(g2)

    def test_gate1_fail_returns_violations(self):
        viols = [{"rule": "§T1"}]
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "FAIL", "violations", viols)
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertEqual(g1, viols)
            self.assertIsNone(g2)

    def test_gate1_pass_gate2_missing_decrements(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 1)
            self.assertIsNone(g1)
            self.assertIsNone(g2)

    def test_gate2_fail_returns_blocking_issues(self):
        issues = [{"issue": "arch violation"}]
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            self._write(d, "gate2", 1, "FAIL", "blocking_issues", issues)
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertIsNone(g1)
            self.assertEqual(g2, issues)

    def test_both_pass_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            self._write(d, "gate2", 1, "PASS")
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertIsNone(g1)
            self.assertIsNone(g2)


if __name__ == "__main__":
    unittest.main()
