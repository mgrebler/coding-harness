"""
Eval tests for test_critic.py.
Requires a running Ollama instance. Configure via environment variables:
  OLLAMA_URL   (default: http://localhost:11434)
  OLLAMA_MODEL (default: deepseek-r1:8b)

These tests create a real git repo so get_changed_files() returns the fixture test files.
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
        "test": {"enabled": True, "model": OLLAMA_MODEL},
    },
}

TEST_FILE_IN_REPO = "backend/tests/routes/health.test.ts"


def _setup_git_repo(tmpdir: Path, test_fixture: Path) -> None:
    def git(*args):
        subprocess.run(["git", *args], cwd=tmpdir, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "test@harness.local")
    git("config", "user.name", "Harness Test")
    (tmpdir / "README.md").write_text("# test repo")
    git("add", "README.md")
    git("commit", "-m", "Initial commit")
    git("checkout", "-b", "001-health-endpoint")

    test_path = tmpdir / TEST_FILE_IN_REPO
    test_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(test_fixture, test_path)
    git("add", TEST_FILE_IN_REPO)
    git("commit", "-m", "Add health endpoint tests")


def _setup_tmpdir(test_fixture: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    spec_dir = tmpdir / "specs" / "001-health-endpoint"
    spec_dir.mkdir(parents=True)
    memory_dir = tmpdir / ".specify" / "memory"
    memory_dir.mkdir(parents=True)

    shutil.copy(FIXTURES / "constitution.md", memory_dir / "constitution.md")
    shutil.copy(FIXTURES / "architecture.md", memory_dir / "architecture.md")
    shutil.copy(FIXTURES / "test-principles.md", memory_dir / "test-principles.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(FIXTURES / "good" / "plan.md", spec_dir / "plan.md")
    shutil.copy(FIXTURES / "good" / "tasks.md", spec_dir / "tasks.md")

    results_dir = spec_dir / "test-results"
    results_dir.mkdir()
    shutil.copy(FIXTURES / "good" / "test-results" / "T001-red.txt", results_dir / "T001-red.txt")

    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))

    _setup_git_repo(tmpdir, test_fixture)
    return tmpdir


def _run_critic(tmpdir: Path) -> dict:
    subprocess.run(
        [sys.executable, str(AGENTS / "test_critic.py"), "--feature", "001-health-endpoint"],
        cwd=tmpdir,
        check=True,
    )
    result_path = tmpdir / "specs" / "001-health-endpoint" / "test-critic-result-1.json"
    return json.loads(result_path.read_text(encoding="utf-8"))


class TestTestCriticGoodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_good_tests_pass(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "health.test.ts")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "PASS",
                         f"Expected PASS but got FAIL. Violations: {result.get('violations')}")


class TestTestCriticTestOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_test_with_only_directive_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "health-test-with-only.ts")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for test file using it.only")
        rule_texts = " ".join(
            v.get("rule", "") + " " + v.get("finding", "")
            for v in result.get("violations", [])
        )
        self.assertRegex(rule_texts.lower(), r"only|§tq9|ci|spec.cover|sc-00",
                         "Expected a CI readiness or spec coverage violation")


if __name__ == "__main__":
    unittest.main()
