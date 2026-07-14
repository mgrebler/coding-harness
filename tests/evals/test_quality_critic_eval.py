"""
Eval tests for ch_4_implement_quality_critic.py.
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
    make_llm_config,
    run_critic,
    setup_git_repo,
)

LOCAL_LLM_CONFIG = make_llm_config("implement-quality-review")
RESULT_PREFIX = "ch-4-implement-code-quality-review-result"

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
    shutil.copy(FIXTURES / "code-quality-principles.md", memory_dir / "code-quality-principles.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(FIXTURES / "good" / "plan.md", spec_dir / "plan.md")
    shutil.copy(FIXTURES / "good" / "tasks.md", spec_dir / "tasks.md")

    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))

    setup_git_repo(
        tmpdir,
        {
            IMPL_FILE_IN_REPO: impl_fixture,
            INDEX_FILE_IN_REPO: FIXTURES / "good" / "index.ts",
            TEST_FILE_IN_REPO: FIXTURES / "good" / "health.test.ts",
        },
        commit_message="Implement health endpoint",
    )
    return tmpdir


class TestQualityReviewGoodImpl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_clean_implementation_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "health.ts")
        result = run_critic(tmpdir, "implement-quality-review", result_prefix=RESULT_PREFIX)
        self.assertEqual(
            result["status"],
            "PASS",
            f"Expected PASS but got FAIL. Blocking issues: {result.get('blocking_issues')}",
        )


class TestQualityReviewSwallowedException(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_swallowed_exception_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "health-swallowed-exception.ts")
        result = run_critic(tmpdir, "implement-quality-review", result_prefix=RESULT_PREFIX)
        self.assertEqual(
            result["status"],
            "FAIL",
            "Expected FAIL for a handler with an empty catch block that swallows errors",
        )
        rule_texts = " ".join(
            issue.get("title", "") + " " + issue.get("finding", "")
            for issue in result.get("blocking_issues", [])
        )
        self.assertRegex(
            rule_texts.lower(),
            r"swallow|catch|silent|exception|error handling",
            "Expected a blocking issue citing the swallowed exception",
        )


if __name__ == "__main__":
    unittest.main()
