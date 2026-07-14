"""
Eval tests for ch_1_plan_architecture_critic.py.
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
    make_llm_config,
    run_critic,
)

LOCAL_LLM_CONFIG = make_llm_config("architecture")
RESULT_PREFIX = "ch-1-plan-architecture-review-result"


def _setup_tmpdir(plan_fixture: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    spec_dir = tmpdir / "specs" / "001-health-endpoint"
    spec_dir.mkdir(parents=True)
    memory_dir = tmpdir / ".specify" / "memory"
    memory_dir.mkdir(parents=True)

    shutil.copy(FIXTURES / "constitution.md", memory_dir / "constitution.md")
    shutil.copy(FIXTURES / "architecture.md", memory_dir / "architecture.md")
    shutil.copy(FIXTURES / "architecture-principles.md", memory_dir / "architecture-principles.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(plan_fixture, spec_dir / "plan.md")
    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))
    return tmpdir


class TestArchitectureReviewGoodPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_good_plan_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "plan.md")
        result = run_critic(tmpdir, "architecture", result_prefix=RESULT_PREFIX)
        self.assertEqual(
            result["status"],
            "PASS",
            f"Expected PASS but got FAIL. Blocking issues: {result.get('blocking_issues')}",
        )


class TestArchitectureReviewViolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_plan_with_unjustified_microservice_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "plan-architecture-violation.md")
        result = run_critic(tmpdir, "architecture", result_prefix=RESULT_PREFIX)
        self.assertEqual(
            result["status"],
            "FAIL",
            "Expected FAIL for a plan introducing an unjustified microservice, "
            "shared mutable Redis cache, and non-idempotent retries",
        )
        rule_texts = " ".join(
            issue.get("title", "") + " " + issue.get("finding", "")
            for issue in result.get("blocking_issues", [])
        )
        self.assertRegex(
            rule_texts.lower(),
            r"microservice|idempoten|shared|distributed|coupling|scalab",
            "Expected a blocking issue citing the unjustified distributed-systems complexity",
        )


if __name__ == "__main__":
    unittest.main()
