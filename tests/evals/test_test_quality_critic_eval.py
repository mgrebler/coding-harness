"""
Eval tests for ch_3_test_quality_critic.py.
Requires a running Ollama instance. Configure via environment variables:
  OLLAMA_URL   (default: http://localhost:11434)
  OLLAMA_MODEL (default: deepseek-r1:8b)

These tests create a real git repo so get_changed_files() returns the fixture test files.
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
    setup_git_repo,
)

LOCAL_LLM_CONFIG = make_llm_config("test-quality")

TEST_FILE_IN_REPO = "backend/tests/routes/health.test.ts"


def _setup_tmpdir(test_fixture: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    spec_dir = tmpdir / "specs" / "001-health-endpoint"
    spec_dir.mkdir(parents=True)
    memory_dir = tmpdir / ".specify" / "memory"
    memory_dir.mkdir(parents=True)

    shutil.copy(FIXTURES / "constitution.md", memory_dir / "constitution.md")
    shutil.copy(FIXTURES / "test-principles.md", memory_dir / "test-principles.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(FIXTURES / "good" / "plan.md", spec_dir / "plan.md")
    shutil.copy(FIXTURES / "good" / "tasks.md", spec_dir / "tasks.md")

    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))

    setup_git_repo(
        tmpdir, {TEST_FILE_IN_REPO: test_fixture}, commit_message="Add health endpoint tests"
    )
    return tmpdir


class TestTestQualityReviewGoodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_good_tests_pass(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "health.test.ts")
        result = run_critic(tmpdir, "test-quality")
        self.assertEqual(
            result["status"],
            "PASS",
            f"Expected PASS but got FAIL. Blocking issues: {result.get('blocking_issues')}",
        )


class TestTestQualityReviewOnlyDirective(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_test_with_only_directive_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "health-test-with-only.ts")
        result = run_critic(tmpdir, "test-quality")
        self.assertEqual(result["status"], "FAIL", "Expected FAIL for test file using it.only")
        issue_texts = " ".join(
            issue.get("title", "") + " " + issue.get("finding", "")
            for issue in result.get("blocking_issues", [])
        )
        self.assertRegex(
            issue_texts.lower(),
            r"only|§tq9|ci",
            "Expected a CI readiness violation citing the it.only directive",
        )


if __name__ == "__main__":
    unittest.main()
