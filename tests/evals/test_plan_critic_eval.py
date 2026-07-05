"""
Eval tests for plan_critic.py.
Requires a running Ollama instance. Configure via environment variables:
  OLLAMA_URL   (default: http://localhost:11434)
  OLLAMA_MODEL (default: deepseek-r1:8b)
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _ollama import require_ollama
from common import (
    FIXTURES,
    OLLAMA_MODEL,
    OLLAMA_URL,
    assert_violations_match,
    make_llm_config,
    run_critic,
)

LOCAL_LLM_CONFIG = make_llm_config("plan")


def _setup_tmpdir(plan_fixture: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    spec_dir = tmpdir / "specs" / "001-health-endpoint"
    spec_dir.mkdir(parents=True)
    memory_dir = tmpdir / ".specify" / "memory"
    memory_dir.mkdir(parents=True)

    shutil.copy(FIXTURES / "constitution.md", memory_dir / "constitution.md")
    shutil.copy(FIXTURES / "architecture.md", memory_dir / "architecture.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(plan_fixture, spec_dir / "plan.md")
    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))
    return tmpdir


class TestPlanCriticGoodPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_good_plan_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "plan.md")
        result = run_critic(tmpdir, "plan")
        self.assertEqual(result["status"], "PASS",
                         f"Expected PASS but got FAIL. Violations: {result.get('violations')}")


class TestPlanCriticMissingTraceability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_plan_without_spec_references_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "plan-missing-traceability.md")
        result = run_critic(tmpdir, "plan")
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for plan that doesn't reference spec requirements")
        assert_violations_match(self, result, r"traceab|fr-001|sc-001|acceptance",
                                "Expected a traceability violation citing spec requirements")


class TestPlanCriticStackViolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_plan_using_banned_framework_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "plan-stack-violation.md")
        result = run_critic(tmpdir, "plan")
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for plan proposing Express (banned by constitution)")
        assert_violations_match(self, result, r"express|stack|constitution|prohibit|hono",
                                "Expected a stack constraint violation mentioning Express or Hono")


if __name__ == "__main__":
    unittest.main()
