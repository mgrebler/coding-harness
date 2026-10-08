"""
Eval tests for ch_5_docs_critic.py.
Requires a running Ollama instance. Configure via environment variables:
  OLLAMA_URL   (default: http://localhost:11434)
  OLLAMA_MODEL (default: deepseek-r1:8b)

These tests create a real git repo so get_changed_files() returns the fixture source files
that were actually "shipped" for the feature, and copy a fixture docs/ tree into the tmpdir
so the critic evaluates it as the candidate merge.
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

LOCAL_LLM_CONFIG = make_llm_config("docs")

IMPL_FILE_IN_REPO = "backend/src/api/health.ts"
INDEX_FILE_IN_REPO = "backend/src/index.ts"
TEST_FILE_IN_REPO = "backend/tests/routes/health.test.ts"


def _setup_tmpdir(docs_fixture_dir: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    spec_dir = tmpdir / "specs" / "001-health-endpoint"
    spec_dir.mkdir(parents=True)
    memory_dir = tmpdir / ".specify" / "memory"
    memory_dir.mkdir(parents=True)

    shutil.copy(FIXTURES / "constitution.md", memory_dir / "constitution.md")
    shutil.copy(FIXTURES / "documentation-principles.md", memory_dir / "documentation-principles.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(FIXTURES / "good" / "plan.md", spec_dir / "plan.md")
    shutil.copy(FIXTURES / "good" / "tasks.md", spec_dir / "tasks.md")

    shutil.copytree(docs_fixture_dir, tmpdir / "docs")

    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))

    # No specs/001-health-endpoint/ch-5-docs-baseline/ — this is the first-ever docs
    # run for this fixture project, so there is nothing to regress against.
    setup_git_repo(
        tmpdir,
        {
            IMPL_FILE_IN_REPO: FIXTURES / "good" / "health.ts",
            INDEX_FILE_IN_REPO: FIXTURES / "good" / "index.ts",
            TEST_FILE_IN_REPO: FIXTURES / "good" / "health.test.ts",
        },
        commit_message="Implement health endpoint",
    )
    return tmpdir


class TestDocsCriticGoodMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_correct_merge_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "docs")
        result = run_critic(tmpdir, "docs")
        self.assertEqual(
            result["status"],
            "PASS",
            f"Expected PASS but got FAIL. Violations: {result.get('violations')}",
        )


class TestDocsCriticStaleReference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_docs_describing_unshipped_endpoint_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "docs-stale-reference")
        result = run_critic(tmpdir, "docs")
        self.assertEqual(
            result["status"],
            "FAIL",
            "Expected FAIL for docs describing /status when the shipped endpoint is /health",
        )
        assert_violations_match(
            self,
            result,
            r"merge accuracy|staleness|§d2|§d5|/health|/status",
            "Expected a merge-accuracy or staleness violation",
        )


class TestDocsCriticInternalJargon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_leaked_task_id_outside_maintainer_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "docs-internal-jargon")
        result = run_critic(tmpdir, "docs")
        self.assertEqual(
            result["status"],
            "FAIL",
            "Expected FAIL for an internal task ID leaked into a user-facing reference/ file",
        )
        assert_violations_match(
            self,
            result,
            r"leakage|§d6|task|internal|maintainer",
            "Expected a cross-audience leakage violation",
        )


if __name__ == "__main__":
    unittest.main()
