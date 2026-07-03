"""
Eval tests for implement_critic.py.
Requires a running Ollama instance. Configure via environment variables:
  OLLAMA_URL   (default: http://localhost:11434)
  OLLAMA_MODEL (default: deepseek-r1:8b)

These tests create a real git repo so get_changed_files() returns the fixture source files.
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
    "default": {"enabled": False, "model": ""},
    "critics": {
        "implement": {"enabled": True, "model": OLLAMA_MODEL},
    },
}

IMPL_FILE_IN_REPO = "backend/src/api/health.ts"


def _setup_git_repo(tmpdir: Path, impl_fixture: Path) -> None:
    def git(*args):
        subprocess.run(["git", *args], cwd=tmpdir, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "test@harness.local")
    git("config", "user.name", "Harness Test")
    (tmpdir / "README.md").write_text("# test repo")
    git("add", "README.md")
    git("commit", "-m", "Initial commit")
    git("checkout", "-b", "001-health-endpoint")

    impl_path = tmpdir / IMPL_FILE_IN_REPO
    impl_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(impl_fixture, impl_path)
    git("add", IMPL_FILE_IN_REPO)
    git("commit", "-m", "Implement health endpoint")


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

    _setup_git_repo(tmpdir, impl_fixture)
    return tmpdir


def _run_critic(tmpdir: Path) -> dict:
    subprocess.run(
        [sys.executable, str(AGENTS / "implement_critic.py"), "--feature", "001-health-endpoint"],
        cwd=tmpdir,
        check=True,
    )
    result_path = tmpdir / "specs" / "001-health-endpoint" / "implement-critic-result-1.json"
    return json.loads(result_path.read_text(encoding="utf-8"))


class TestImplementCriticGoodImpl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_correct_implementation_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "health.ts")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "PASS",
                         f"Expected PASS but got FAIL. Violations: {result.get('violations')}")


class TestImplementCriticWrongResponse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_implementation_with_wrong_path_and_response_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "health-wrong-response.ts")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for implementation using /status instead of /health and wrong response shape")
        rule_texts = " ".join(
            v.get("rule", "") + " " + v.get("finding", "")
            for v in result.get("violations", [])
        )
        self.assertRegex(rule_texts.lower(), r"spec|fr-001|§i7|health|status|compliance",
                         "Expected a spec compliance violation")


if __name__ == "__main__":
    unittest.main()
