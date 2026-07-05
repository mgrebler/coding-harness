"""
Eval tests for implement_critic.py.
Requires a running Ollama instance. Configure via environment variables:
  OLLAMA_URL   (default: http://localhost:11434)
  OLLAMA_MODEL (default: deepseek-r1:8b)

These tests create a real git repo so get_changed_files() returns the fixture source files.
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
    setup_git_repo,
)

LOCAL_LLM_CONFIG = make_llm_config("implement")

IMPL_FILE_IN_REPO = "backend/src/api/health.ts"
INDEX_FILE_IN_REPO = "backend/src/index.ts"
TEST_FILE_IN_REPO = "backend/tests/routes/health.test.ts"


def _setup_tmpdir(impl_fixture: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    spec_dir = tmpdir / "specs" / "001-health-endpoint"
    spec_dir.mkdir(parents=True)
    memory_dir = tmpdir / ".specify" / "memory"
    memory_dir.mkdir(parents=True)

    shutil.copy(FIXTURES / "constitution.md", memory_dir / "constitution.md")
    shutil.copy(FIXTURES / "architecture.md", memory_dir / "architecture.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(FIXTURES / "good" / "plan.md", spec_dir / "plan.md")
    shutil.copy(FIXTURES / "good" / "tasks.md", spec_dir / "tasks.md")

    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))

    setup_git_repo(tmpdir, {
        IMPL_FILE_IN_REPO: impl_fixture,
        INDEX_FILE_IN_REPO: FIXTURES / "good" / "index.ts",
        TEST_FILE_IN_REPO: FIXTURES / "good" / "health.test.ts",
    }, commit_message="Implement health endpoint")
    return tmpdir


class TestImplementCriticGoodImpl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_correct_implementation_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "health.ts")
        result = run_critic(tmpdir, "implement")
        self.assertEqual(result["status"], "PASS",
                         f"Expected PASS but got FAIL. Violations: {result.get('violations')}")


class TestImplementCriticWrongResponse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_implementation_with_wrong_path_and_response_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "health-wrong-response.ts")
        result = run_critic(tmpdir, "implement")
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for implementation using /status instead of /health and wrong response shape")
        assert_violations_match(self, result, r"spec|fr-001|§i7|health|status|compliance",
                                "Expected a spec compliance violation")


if __name__ == "__main__":
    unittest.main()
