"""
Eval tests for tasks_critic.py.
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
    "default": {"enabled": False, "model": ""},
    "critics": {
        "tasks": {"enabled": True, "model": OLLAMA_MODEL},
    },
}


def _setup_tmpdir(tasks_fixture: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    spec_dir = tmpdir / "specs" / "001-health-endpoint"
    spec_dir.mkdir(parents=True)
    memory_dir = tmpdir / ".specify" / "memory"
    memory_dir.mkdir(parents=True)

    shutil.copy(FIXTURES / "constitution.md", memory_dir / "constitution.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(FIXTURES / "good" / "plan.md", spec_dir / "plan.md")
    shutil.copy(tasks_fixture, spec_dir / "tasks.md")
    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))
    return tmpdir


def _run_critic(tmpdir: Path) -> dict:
    subprocess.run(
        [sys.executable, str(AGENTS / "tasks_critic.py"), "--feature", "001-health-endpoint"],
        cwd=tmpdir,
        check=True,
    )
    result_path = tmpdir / "specs" / "001-health-endpoint" / "tasks-critic-result-1.json"
    return json.loads(result_path.read_text(encoding="utf-8"))


class TestTasksCriticGoodTasks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_good_tasks_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "tasks.md")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "PASS",
                         f"Expected PASS but got FAIL. Violations: {result.get('violations')}")


class TestTasksCriticWrongFormat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_tasks_without_required_format_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "tasks-wrong-format.md")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for tasks without [TEST]/[IMPL] format")
        rule_texts = " ".join(
            v.get("rule", "") + " " + v.get("finding", "")
            for v in result.get("violations", [])
        )
        self.assertRegex(rule_texts.lower(), r"test|impl|format|§t",
                         "Expected a format/structure violation")


class TestTasksCriticNoStoryLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_tasks_without_story_labels_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "tasks-no-story-labels.md")
        result = _run_critic(tmpdir)
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for tasks missing US story labels")
        rule_texts = " ".join(
            v.get("rule", "") + " " + v.get("finding", "")
            for v in result.get("violations", [])
        )
        self.assertRegex(rule_texts.lower(), r"story|us1|label|traceab|§t",
                         "Expected a story traceability violation")


if __name__ == "__main__":
    unittest.main()
