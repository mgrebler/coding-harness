"""
Eval tests for plan_critic.py.
Requires a running Ollama instance. Configure via environment variables:
  OLLAMA_URL   (default: http://localhost:11434)
  OLLAMA_MODEL (default: deepseek-r1:8b)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _ollama import require_ollama

FIXTURES = Path(__file__).parent / "fixtures"
AGENTS = Path(__file__).parent.parent.parent / ".claude/agents"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-r1:8b")

LOCAL_LLM_CONFIG = {
    "ollama_url": OLLAMA_URL,
    "num_ctx": 16384,
    "keep_alive": -1,
    "temperature": 0.0,
    "default": {"enabled": False, "model": ""},
    "critics": {
        "plan": {"enabled": True, "model": OLLAMA_MODEL},
    },
}


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


def _run_critic(tmpdir: Path) -> dict:
    subprocess.run(
        [sys.executable, str(AGENTS / "plan_critic.py"), "--feature", "001-health-endpoint"],
        cwd=tmpdir,
        check=True,
    )
    result_path = tmpdir / "specs" / "001-health-endpoint" / "plan-critic-result-1.json"
    return json.loads(result_path.read_text(encoding="utf-8"))


class TestPlanCriticGoodPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_good_plan_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "plan.md")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "PASS",
                         f"Expected PASS but got FAIL. Violations: {result.get('violations')}")


class TestPlanCriticMissingTraceability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_plan_without_spec_references_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "plan-missing-traceability.md")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for plan that doesn't reference spec requirements")
        rule_texts = " ".join(
            v.get("rule", "") + " " + v.get("finding", "")
            for v in result.get("violations", [])
        )
        self.assertRegex(rule_texts.lower(), r"traceab|fr-001|sc-001|acceptance",
                         "Expected a traceability violation citing spec requirements")


class TestPlanCriticStackViolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_plan_using_banned_framework_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "plan-stack-violation.md")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for plan proposing Express (banned by constitution)")
        rule_texts = " ".join(
            v.get("rule", "") + " " + v.get("finding", "")
            for v in result.get("violations", [])
        )
        self.assertRegex(rule_texts.lower(), r"express|stack|constitution|prohibit|hono",
                         "Expected a stack constraint violation mentioning Express or Hono")


if __name__ == "__main__":
    unittest.main()
